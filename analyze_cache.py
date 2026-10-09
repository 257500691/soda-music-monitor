# -*- coding: utf-8 -*-
"""汽水音乐缓存音频分析模块。

汽水音乐把播放的歌曲以未加密的 M4A 容器缓存在 LunaCacheV2 目录，
只有音频数据体是 cenc/AES-128 加密的，容器头（moov）明文可读，
因此编码格式、码率、采样率、声道、位深、加密方式都能精确解析。

moov 盒子位于文件前部，所以只读文件头部即可，扫描整目录很快。
"""
from __future__ import annotations

import ctypes
import datetime
import glob
import json
import os
import struct
import time
from ctypes import wintypes

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(os.environ.get("APPDATA", ""), "SodaMusic", "LunaCacheV2")
INDEX_PATH = os.path.join(APP_DIR, "cache_index.json")
INDEX_VERSION = 2
HEAD_BYTES = 320 * 1024

# ---- 文件占用探测（判断哪个缓存文件正在被播放器使用）----
_GENERIC_READ = 0x80000000
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80
_ERROR_SHARING_VIOLATION = 32

if os.name == "nt":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.HANDLE]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _INVALID_HANDLE = wintypes.HANDLE(-1).value
else:
    _k32 = None

SAMPLE_RATES = {0: 96000, 1: 88200, 2: 64000, 3: 48000, 4: 44100, 5: 32000,
                6: 24000, 7: 22050, 8: 16000, 9: 12000, 10: 11025, 11: 8000}
AOT_NAMES = {1: "AAC-Main", 2: "AAC-LC", 3: "AAC-SSR", 5: "HE-AAC", 29: "HE-AACv2"}
AUDIO_OTI = (0x40, 0x66, 0x67, 0x68, 0x69, 0x6B, 0xA5, 0xA9, 0xDD, 0xE1)

# 档位推断依据：实测缓存中存在 64 / 128 / 256 / 320 kbps AAC-LC 与 FLAC 无损
TIER_STEPS = [(300, "极高"), (192, "较高"), (96, "标准"), (0, "流畅")]
TIER_COLORS = {"无损": "#ff5f9e", "极高": "#ffb020", "较高": "#33cc88",
               "标准": "#4a9eff", "流畅": "#8a8a8a", "未知": "#8a8a8a"}


def _read_head(path, size=HEAD_BYTES):
    """只读文件头部：moov 盒子位于文件前部，足够解析全部音质信息。"""
    with open(path, "rb") as fh:
        return fh.read(size)


def _rd_len(buf, i):
    """MPEG-4 描述符变长长度。"""
    val = 0
    for _ in range(4):
        val = (val << 7) | (buf[i] & 0x7F)
        i += 1
        if not (buf[i - 1] & 0x80):
            break
    return val, i


def _parse_esds(data):
    """从 esds 盒子读出编码、声明码率、AAC 档次、采样率、声道。"""
    idx = data.find(b"esds")
    if idx < 0:
        return {}
    buf = data[idx + 8:idx + 8 + 96]
    for k in range(max(0, len(buf) - 14)):
        if buf[k] != 0x04:
            continue
        length, p = _rd_len(buf, k + 1)
        if not 13 <= length <= 48 or p + 13 > len(buf):
            continue
        oti = buf[p]
        if oti not in AUDIO_OTI:
            continue
        out = {
            "oti": oti,
            "max_bitrate": int.from_bytes(buf[p + 5:p + 9], "big"),
            "avg_bitrate": int.from_bytes(buf[p + 9:p + 13], "big"),
            "esds": True,
        }
        j = p + 13
        if j < len(buf) and buf[j] == 0x05:
            ln, k3 = _rd_len(buf, j + 1)
            asc = buf[k3:k3 + ln]
            if len(asc) >= 2:
                out["aac_profile"] = asc[0] >> 3
                out["sf_index"] = ((asc[0] & 0x07) << 1) | (asc[1] >> 7)
                out["channel_config"] = (asc[1] >> 3) & 0x0F
        return out
    return {}


def _mvhd_duration(data):
    idx = data.find(b"mvhd")
    if idx < 0:
        return None
    p = idx + 4
    if p + 32 > len(data):
        return None
    if data[p] == 0:
        timescale, duration = struct.unpack(">II", data[p + 12:p + 20])
    else:
        timescale, duration = struct.unpack(">IQ", data[p + 20:p + 32])
    return duration / timescale if timescale else None


def _flac_info(data):
    """无损档：从 dfLa 盒子的 STREAMINFO 读出位深、采样率、声道、总样本数。"""
    idx = data.find(b"dfLa")
    if idx < 0:
        return {}
    si = data[idx + 12:idx + 12 + 34]
    if len(si) < 18:
        return {}
    try:
        sample_rate = (si[10] << 12) | (si[11] << 4) | (si[12] >> 4)
        channels = ((si[12] >> 1) & 0x07) + 1
        bits = (((si[12] & 0x01) << 4) | (si[13] >> 4)) + 1
        total = ((si[13] & 0x0F) << 32) | (si[14] << 24) | (si[15] << 16) | \
                (si[16] << 8) | si[17]
        return {"bits_per_sample": bits, "flac_sample_rate": sample_rate,
                "flac_channels": channels, "total_samples": total}
    except Exception:  # noqa: BLE001
        return {}


def analyze(path, with_stat=True):
    """解析单个缓存文件，返回音质信息字典。"""
    try:
        data = _read_head(path)
    except OSError:
        return None
    info = {"file": os.path.basename(path), "path": path}
    if with_stat:
        try:
            st = os.stat(path)
            info["size_bytes"] = st.st_size
            info["size_mb"] = round(st.st_size / 1048576, 2)
            info["mtime"] = datetime.datetime.fromtimestamp(st.st_mtime)
        except OSError:
            return None

    frma = data.find(b"frma")
    info["container_codec"] = data[frma + 4:frma + 8].decode("latin1") if frma > 0 else "?"
    schm = data.find(b"schm")
    info["scheme"] = data[schm + 8:schm + 12].decode("latin1") if schm > 0 else None
    info["encrypted"] = info["scheme"] == "cenc"
    info["brand"] = data[8:12].decode("latin1", "replace").strip() if len(data) > 12 else ""

    esds = _parse_esds(data)
    info.update({k: v for k, v in esds.items() if k != "esds"})
    duration = _mvhd_duration(data)
    info["duration"] = round(duration, 1) if duration else None

    if info.get("container_codec") == "fLaC":
        info["lossless"] = True
        info["codec"] = "FLAC"
        fi = _flac_info(data)
        info.update(fi)
        if fi.get("flac_sample_rate"):
            info["sample_rate"] = fi["flac_sample_rate"]
        if fi.get("flac_channels"):
            info["channels"] = fi["flac_channels"]
        if not info.get("duration") and fi.get("total_samples") and fi.get("flac_sample_rate"):
            info["duration"] = round(fi["total_samples"] / fi["flac_sample_rate"], 1)
    else:
        info["lossless"] = False
        profile = AOT_NAMES.get(esds.get("aac_profile"), "")
        info["codec"] = profile or (info.get("container_codec") or "?").upper()
        if esds.get("sf_index") is not None:
            info["sample_rate"] = SAMPLE_RATES.get(esds["sf_index"])
        if esds.get("channel_config") is not None:
            info["channels"] = esds["channel_config"]

    declared = info.get("avg_bitrate")
    info["declared_kbps"] = round(declared / 1000) if declared else None
    if info.get("duration") and info.get("size_bytes"):
        info["measured_kbps"] = round(info["size_bytes"] * 8 / info["duration"] / 1000)
    else:
        info["measured_kbps"] = None

    tier = tier_of(info)
    info["tier"] = tier
    info["tier_color"] = TIER_COLORS.get(tier, "#8a8a8a")
    return info


def tier_of(info):
    """推断音质档位。"""
    if info.get("lossless"):
        return "无损"
    kbps = info.get("declared_kbps") or info.get("measured_kbps")
    if not kbps:
        return "未知"
    for threshold, name in TIER_STEPS:
        if kbps >= threshold:
            return name
    return "未知"


def match_current(duration_s, candidates=None, tolerance=1.5):
    """按总时长匹配当前播放歌曲的缓存文件（多个版本时取最新缓存的）。"""
    if not duration_s:
        return None
    if candidates is None:
        candidates = scan_all()
    hits = [c for c in candidates
            if c.get("duration") and abs(c["duration"] - duration_s) <= tolerance]
    if not hits:
        return None
    return max(hits, key=lambda c: c.get("mtime") or datetime.datetime.min)


def is_in_use(path):
    """判断缓存文件是否正被其它进程占用。

    以独占方式打开（共享模式为 0）；如果文件已被打开，会失败并返回
    ERROR_SHARING_VIOLATION(32)。汽水音乐播放时会持有缓存文件的句柄，
    所以这是判断"哪一首/哪个档位正在播放"最可靠的信号。
    """
    if _k32 is None or not path:
        return False
    try:
        handle = _k32.CreateFileW(str(path), _GENERIC_READ, 0, None,
                                  _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None)
    except OSError:
        return False
    if not handle or handle == _INVALID_HANDLE:
        return ctypes.get_last_error() == _ERROR_SHARING_VIOLATION
    _k32.CloseHandle(handle)
    return False


_SCAN_MEMO = {"time": 0.0, "rows": None}


def index_ready():
    """解析索引是否已建立。

    首次全量扫描要读几百个文件头（数十秒），必须放在后台线程做；
    在索引建好之前界面不要同步匹配，否则会卡住界面。
    """
    try:
        return os.path.getsize(INDEX_PATH) > 2
    except OSError:
        return False


def scan_entries(max_age=4.0):
    """带短时记忆的目录扫描，避免每次匹配都全量重扫。"""
    now = time.time()
    if _SCAN_MEMO["rows"] is None or now - _SCAN_MEMO["time"] > max_age:
        _SCAN_MEMO["rows"] = scan_all()
        _SCAN_MEMO["time"] = now
    return _SCAN_MEMO["rows"]


def _atime(path):
    try:
        return os.path.getatime(path)
    except OSError:
        return 0.0


# 匹配依据说明（界面用）
MATCH_REASONS = {
    "lock": "播放中锁定",
    "single": "唯一缓存",
    "atime": "按访问时间推测",
    "mtime": "按缓存时间推测",
}


def find_current(duration_s, tolerance=1.5, entries=None, lock_tolerance=3.0,
                 atime_gap=60.0, atime_fresh=6 * 3600):
    """定位当前播放歌曲对应的缓存文件。

    同一首歌在缓存里往往存有多个音质档位（实测最多 6 个），文件名是无意义
    UUID，所以靠三路信号由强到弱判断，并在结果里写明依据（matched_by）：

      1) 'lock'   —— 文件正被播放器占用（独占打开报共享冲突）。最可靠，
                     能精确到档位；
      2) 'single' —— 该时长只有一个候选文件，无需推断；
      3) 'atime'  —— 播放器开始播放时会读取缓存文件，访问时间被刷新，
                     取访问时间明显最新的那个（且不能太旧）；
      4) 'mtime'  —— 以上都拿不到时，取缓存时间最新的，只能算粗猜。
    """
    if not duration_s:
        return None
    rows = entries if entries is not None else scan_entries()

    def gap(row, key="duration"):
        value = row.get("duration")
        return abs(value - duration_s) if value else 1e9

    # 1) 占用探测
    locked = [r for r in rows if is_in_use(r.get("path", ""))]
    if locked:
        best = min(locked, key=gap)
        if gap(best) <= lock_tolerance:
            result = dict(best)
            result["matched_by"] = "lock"
            return result

    hits = [r for r in rows
            if r.get("duration") and abs(r["duration"] - duration_s) <= tolerance]
    if not hits:
        return None

    # 2) 唯一候选
    if len(hits) == 1:
        result = dict(hits[0])
        result["matched_by"] = "single"
        return result

    # 3) 访问时间：取明显最新、且足够新的那个
    ordered = sorted(hits, key=lambda r: _atime(r.get("path", "")), reverse=True)
    top, second = _atime(ordered[0].get("path", "")), _atime(ordered[1].get("path", ""))
    if top - second > atime_gap and (time.time() - top) < atime_fresh:
        result = dict(ordered[0])
        result["matched_by"] = "atime"
        return result

    # 4) 退化：缓存时间最新
    best = max(hits, key=lambda r: r.get("mtime") or datetime.datetime.min)
    result = dict(best)
    result["matched_by"] = "mtime"
    return result


def match_reason(info):
    """把 matched_by 转成界面文案。"""
    if not info:
        return ""
    return MATCH_REASONS.get(info.get("matched_by"), "未知依据")


def history_of(duration_s, entries=None, tolerance=1.5):
    """同一首歌的历史档位记录（用于对比不同时期的音质）。"""
    if entries is None:
        entries = scan_entries()
    hits = [e for e in entries
            if e.get("duration") and abs(e["duration"] - duration_s) <= tolerance]
    hits.sort(key=lambda e: e.get("mtime") or datetime.datetime.min, reverse=True)
    return hits


def scan_all(use_index=True):
    """扫描整个缓存目录（带磁盘索引缓存）。"""
    if not os.path.isdir(CACHE_DIR):
        return []
    files = glob.glob(os.path.join(CACHE_DIR, "*.bin"))
    signatures = {}
    for f in files:
        try:
            st = os.stat(f)
        except OSError:
            continue
        signatures[f] = (st.st_mtime, st.st_size)

    cached = {}
    if use_index and os.path.exists(INDEX_PATH):
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if payload.get("version") == INDEX_VERSION:
                for entry in payload.get("entries", []):
                    key = entry.get("path")
                    if key in signatures:
                        sig = signatures[key]
                        if entry.get("_sig") == [round(sig[0], 3), sig[1]]:
                            entry["mtime"] = datetime.datetime.fromtimestamp(sig[0])
                            cached[key] = entry
        except Exception:  # noqa: BLE001
            cached = {}

    results = []
    for f, sig in signatures.items():
        if f in cached:
            results.append(cached[f])
            continue
        info = analyze(f, with_stat=False)
        if not info:
            continue
        info["size_bytes"] = sig[1]
        info["size_mb"] = round(sig[1] / 1048576, 2)
        info["mtime"] = datetime.datetime.fromtimestamp(sig[0])
        info["_sig"] = [round(sig[0], 3), sig[1]]
        # 实测码率依赖文件体积，必须在补上体积后计算
        if info.get("duration"):
            info["measured_kbps"] = round(sig[1] * 8 / info["duration"] / 1000)
        results.append(info)

    results.sort(key=lambda c: c.get("mtime") or datetime.datetime.min, reverse=True)
    if use_index:
        try:
            with open(INDEX_PATH, "w", encoding="utf-8") as fh:
                json.dump({"version": INDEX_VERSION,
                           "entries": [{k: v for k, v in r.items() if k != "mtime"}
                                       for r in results]},
                          fh, ensure_ascii=False)
        except OSError:
            pass
    return results


def tier_stats(entries):
    """统计各档位数量与缓存占用。"""
    stats = {}
    total_bytes = 0
    for e in entries:
        tier = e.get("tier", "未知")
        row = stats.setdefault(tier, {"count": 0, "bytes": 0})
        row["count"] += 1
        row["bytes"] += e.get("size_bytes") or 0
        total_bytes += e.get("size_bytes") or 0
    return {"total_count": len(entries), "total_bytes": total_bytes,
            "total_mb": round(total_bytes / 1048576, 1), "by_tier": stats}


def cache_dir_size():
    """缓存目录占用与文件数量（含索引库等全部文件）。"""
    total = 0
    count = 0
    if not os.path.isdir(CACHE_DIR):
        return {"bytes": 0, "count": 0}
    for name in os.listdir(CACHE_DIR):
        p = os.path.join(CACHE_DIR, name)
        try:
            if os.path.isfile(p):
                total += os.path.getsize(p)
                count += 1
        except OSError:
            pass
    return {"bytes": total, "count": count}


if __name__ == "__main__":
    import sys

    rows = scan_all()
    print(f"缓存目录: {CACHE_DIR}")
    print(f"解析到 {len(rows)} 个缓存文件\n")
    print("最近 5 个：")
    for r in rows[:5]:
        bits = f" {r['bits_per_sample']}bit" if r.get("bits_per_sample") else ""
        print(f"  {r['file'][:8]} {r['mtime']:%m-%d %H:%M} {r.get('size_mb'):6.2f}MB "
              f"{r.get('duration')}s  声明{r.get('declared_kbps')}kbps "
              f"实测{r.get('measured_kbps')}kbps {r.get('codec')} "
              f"{r.get('sample_rate')}Hz ch={r.get('channels')}{bits} "
              f"[{r.get('tier')}] {'加密' if r.get('encrypted') else '明文'}")
    st = tier_stats(rows)
    print(f"\n档位分布（共 {st['total_count']} 首，{st['total_mb']} MB）：")
    for tier, row in sorted(st["by_tier"].items(), key=lambda x: -x[1]["count"]):
        print(f"  {tier:4s} {row['count']:4d} 首  {row['bytes']/1048576:8.1f} MB")
    if len(sys.argv) > 1:
        target = float(sys.argv[1])
        m = match_current(target, rows)
        print(f"\n时长 {target}s 匹配结果: {m['file'] if m else '无'}")
        if m:
            for k in ("codec", "declared_kbps", "measured_kbps", "sample_rate",
                      "channels", "bits_per_sample", "tier", "encrypted"):
                print(f"  {k}: {m.get(k)}")
