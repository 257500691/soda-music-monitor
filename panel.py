# -*- coding: utf-8 -*-
"""汽水音乐监测面板

悬浮小窗显示当前播放歌曲与实时音质参数，双击展开完整面板。
数据来源：
  1. Windows 媒体会话(SMTC) —— 歌名/歌手/状态/总时长/播放进度
  2. LunaCacheV2 音频缓存    —— 编码/码率/采样率/声道/位深/无损/加密
  3. 采集器附加信息          —— 音频电平/进程CPU/内存/IO速率
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_cache as ac  # noqa: E402
from dashboard import DashboardPanel  # noqa: E402
from reactive_audio import ReactiveAudio  # noqa: E402

APP_DIR = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(APP_DIR, "smtc_helper.ps1")
STATE_DIR = os.path.join(tempfile.gettempdir(), "soda_monitor")
STATE_PATH = os.path.join(STATE_DIR, "state.json")

class Backend:
    """启动并看护 PowerShell 采集器进程。"""

    def __init__(self):
        os.makedirs(STATE_DIR, exist_ok=True)
        self.proc = None
        self.state_path = STATE_PATH
        self.ps_exe = self._find_powershell()
        self.audio = ReactiveAudio(self.ps_exe, STATE_DIR)
        self.last_start = 0.0

    @staticmethod
    def _find_powershell():
        candidates = [
            os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                         r"System32\WindowsPowerShell\v1.0\powershell.exe"),
            "pwsh.exe",
            "powershell.exe",
        ]
        for c in candidates:
            if os.path.sep in c:
                if os.path.exists(c):
                    return c
            else:
                from shutil import which
                found = which(c)
                if found:
                    return found
        return "powershell.exe"

    def start(self):
        self.audio.start()
        if self.proc and self.proc.poll() is None:
            return
        if not os.path.exists(HELPER):
            return
        creation = 0
        if os.name == "nt":
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        cmd = [self.ps_exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-WindowStyle", "Hidden", "-File", HELPER,
               "-OutPath", STATE_PATH, "-CoverPath", os.path.join(STATE_DIR, "cover.png"),
               "-CommandPath", os.path.join(STATE_DIR, "command.json"),
               "-IntervalMs", "700"]
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, creationflags=creation)
            self.last_start = time.monotonic()
        except OSError:
            self.proc = None

    def ensure_alive(self):
        """采集器挂了就重启（最多每 5 秒一次）。"""
        self.audio.ensure_alive()
        if self.proc is None or self.proc.poll() is not None:
            if time.monotonic() - self.last_start > 5:
                self.start()

    def read(self):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def stop(self):
        self.audio.stop()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def control(self, action, position=0):
        if action not in ("play_pause", "next", "previous", "seek"):
            return False
        path = os.path.join(STATE_DIR, "command.json")
        try:
            with open(path + ".tmp", "w", encoding="utf-8") as fh:
                json.dump({"id": uuid.uuid4().hex, "action": action,
                           "issued_at": time.time(), "position": max(0, float(position))}, fh)
            os.replace(path + ".tmp", path)
            return True
        except (OSError, ValueError):
            return False


class ProgressTracker:
    """汽水音乐的 SMTC 时间轴经常不刷新，因此结合墙钟推算进度。"""

    def __init__(self):
        self.duration = None
        self.base = 0.0
        self.base_t = time.monotonic()
        self.last_smtc = None
        self.live_t = 0.0
        self.playing = False

    def reset(self, duration=None, smtc_pos=0.0):
        self.duration = duration
        self.base = max(0.0, smtc_pos or 0.0)
        self.base_t = time.monotonic()
        self.last_smtc = None
        self.live_t = 0.0

    def value(self, now=None):
        now = now or time.monotonic()
        pos = self.base + (now - self.base_t) if self.playing else self.base
        if self.duration:
            pos = min(pos, self.duration)
        return max(0.0, pos)

    def update(self, status, smtc_pos, duration):
        now = time.monotonic()
        playing = (status == "Playing")
        if duration and not self.duration:
            self.reset(duration, smtc_pos)
        if smtc_pos is not None:
            moving = self.last_smtc is not None and abs(smtc_pos - self.last_smtc) > 0.25
            if moving:
                self.live_t = now
                if playing:
                    self.base, self.base_t = smtc_pos, now
            self.last_smtc = smtc_pos
        if playing != self.playing:
            self.base, self.base_t = self.value(now), now
        self.playing = playing
        return self.value(now)


def acquire_single_instance():
    """用命名互斥体保证只运行一个面板实例。

    返回 True 表示可以继续运行，False 表示已有实例在运行。
    """
    try:
        import ctypes

        handle = ctypes.windll.kernel32.CreateMutexW(None, False,
                                                     "Local\\SodaMusicMonitorPanel")
        if not handle:
            return True
        # ERROR_ALREADY_EXISTS = 183
        if ctypes.windll.kernel32.GetLastError() == 183:
            return False
        return True
    except Exception:  # noqa: BLE001
        return True


class Panel(DashboardPanel):
    def __init__(self):
        super().__init__(Backend(), ProgressTracker())


def selftest():
    """无界面自检：验证采集器 + 缓存解析链路。"""
    backend = Backend()
    backend.start()
    print(f"PowerShell: {backend.ps_exe}")
    print(f"状态文件: {STATE_PATH}")
    for _ in range(20):
        time.sleep(0.6)
        state = backend.read()
        if state:
            break
    else:
        print("采集器未产出状态文件")
        backend.stop()
        return 1

    print(f"会话: ok={state.get('ok')} 标题={state.get('title')!r} "
          f"歌手={state.get('artist')!r} 状态={state.get('status')} "
          f"时长={state.get('duration')}s 进度={state.get('position')}s")
    print(f"音频电平: {state.get('audio_peak')}  进程: {state.get('process')}")

    duration = state.get("duration")
    quality = ac.find_current(duration) if duration else None
    if quality:
        print("\n音质解析：")
        for k in ("file", "matched_by", "codec", "declared_kbps", "measured_kbps",
                  "sample_rate", "channels", "bits_per_sample", "lossless",
                  "encrypted", "tier", "size_mb", "duration"):
            print(f"  {k:16s} {quality.get(k)}")
    else:
        print("\n未匹配到缓存文件")
    backend.stop()
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    # 默认只允许一个实例；设 SODA_PANEL_ALLOW_MULTI=1 可同时开多个（多屏场景）
    if not os.environ.get("SODA_PANEL_ALLOW_MULTI") and not acquire_single_instance():
        sys.exit(0)
    app = Panel()
    if "--compact" not in sys.argv:
        app.toggle()
    app.root.mainloop()
