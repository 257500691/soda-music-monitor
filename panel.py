# -*- coding: utf-8 -*-
"""汽水音乐监测面板

悬浮小窗显示当前播放歌曲与实时音质参数，双击展开完整面板。
数据来源：
  1. Windows 媒体会话(SMTC) —— 歌名/歌手/状态/总时长/播放进度
  2. LunaCacheV2 音频缓存    —— 编码/码率/采样率/声道/位深/无损/加密
  3. 采集器附加信息          —— 音频电平/进程CPU/内存/IO速率
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_cache as ac  # noqa: E402

APP_DIR = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(APP_DIR, "smtc_helper.ps1")
STATE_DIR = os.path.join(tempfile.gettempdir(), "soda_monitor")
STATE_PATH = os.path.join(STATE_DIR, "state.json")

# 配色
BG = "#14161b"
CARD = "#1d2027"
CARD2 = "#23262e"
FG = "#e9ecf1"
MUTED = "#8b93a1"
ACCENT = "#4a9eff"
OK = "#33cc88"
WARN = "#ffb020"
ERR = "#ff5f6d"

FONT = "Microsoft YaHei UI"
COMPACT_W = 366
EXPANDED_W = 668


def fmt_time(seconds):
    if seconds is None:
        return "--:--"
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def ellipsize(text, limit):
    text = text or ""
    return text if len(text) <= limit else text[:limit - 1] + "…"


class Backend:
    """启动并看护 PowerShell 采集器进程。"""

    def __init__(self):
        os.makedirs(STATE_DIR, exist_ok=True)
        self.proc = None
        self.ps_exe = self._find_powershell()
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
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass


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


def rounded_rect(canvas, x1, y1, x2, y2, r, **kwargs):
    points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
              x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(points, smooth=True, **kwargs)


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


class Panel:
    def __init__(self):
        self.backend = Backend()
        self.backend.start()
        self.tracker = ProgressTracker()
        self.state = {}
        self.quality = None
        self.track_key = None
        self.expanded = False
        self.pinned = True
        self.user_moved = False
        self.drag = (0, 0)
        self.level_history = [0.0] * 28
        self.stats = None
        self.stats_loading = False
        self.last_stats_scan = 0.0
        self.history_rows = []
        self.error = None
        self.last_lift = 0.0
        self.last_match_try = 0.0
        self.desired = None

        self.root = tk.Tk()
        self.root.title("汽水音乐监测")
        self.root.configure(bg=BG)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-alpha", 0.96)
        except tk.TclError:
            pass
        # 先给一个合理的初始位置，避免窗口在左上角闪一下
        self.root.geometry(
            f"{COMPACT_W}x300+{self.root.winfo_screenwidth() - COMPACT_W - 28}+44")

        self._build_compact()
        self._build_detail()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

        for widget in (self.header, self.title_lbl, self.artist_lbl):
            widget.bind("<Button-1>", self.start_drag)
            widget.bind("<B1-Motion>", self.do_drag)
            widget.bind("<Double-Button-1>", lambda e: self.toggle())
        self.root.bind("<Escape>", lambda e: self.quit())

        self.fit_window()
        self.tick()
        # 布局稳定后再量一次：__init__ 里量到的尺寸可能还不是最终值
        self.root.after(200, self.fit_window)

    # ---------- 窗口 ----------
    def fit_window(self):
        """按内容实际所需高度调整窗口；详情超出屏幕时改为内部滚动。"""
        self.root.update_idletasks()
        compact_h = self.compact.winfo_reqheight()
        compact_w = max(COMPACT_W, self.compact.winfo_reqwidth())
        screen_h = self.root.winfo_screenheight()
        width, height = compact_w, compact_h

        if self.expanded:
            width = EXPANDED_W
            inner_h = self.detail_inner.winfo_reqheight()
            avail = max(200, screen_h - compact_h - 70)
            detail_h = min(inner_h + 8, avail)
            height = compact_h + detail_h + 10
            need_scroll = (inner_h + 8) > detail_h + 2
            if need_scroll and not self.detail_bar.winfo_ismapped():
                self.detail_bar.pack(side="right", fill="y")
            elif not need_scroll and self.detail_bar.winfo_ismapped():
                self.detail_bar.pack_forget()

        if self.user_moved:
            x, y = self.root.winfo_x(), self.root.winfo_y()
        else:
            x = self.root.winfo_screenwidth() - width - 28
            y = 44
        x = max(8, min(x, self.root.winfo_screenwidth() - width - 8))
        y = max(8, min(y, screen_h - height - 8))
        self.desired = (width, height, x, y)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def enforce_geometry(self):
        """有些情况下窗口位置请求会被丢弃（未映射时设几何、置顶标志刚变更等），
        在用户手动拖动之前持续校正回来。"""
        if self.user_moved or not self.desired:
            return
        width, height, x, y = self.desired
        want = f"{width}x{height}+{x}+{y}"
        try:
            if self.root.geometry() != want:
                self.root.geometry(want)
        except tk.TclError:
            pass

    def start_drag(self, event):
        self.drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def do_drag(self, event):
        self.user_moved = True
        self.root.geometry(f"+{event.x_root - self.drag[0]}+{event.y_root - self.drag[1]}")

    def toggle(self):
        self.expanded = not self.expanded
        if self.expanded:
            self.detail.pack(fill="both", expand=True, padx=10, pady=(0, 10))
            self.expand_btn.config(text="收起 ▴")
            self.detail_canvas.yview_moveto(0)
        else:
            self.detail.pack_forget()
            self.expand_btn.config(text="展开 ▾")
        self.fit_window()
        # 布局稳定后再校正一次（内嵌滚动区的尺寸依赖画布宽度）
        self.root.after(90, self.fit_window)

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.root.attributes("-topmost", self.pinned)
        self.pin_btn.config(fg=ACCENT if self.pinned else MUTED)

    def quit(self):
        self.backend.stop()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # ---------- 界面 ----------
    def _build_compact(self):
        self.compact = tk.Frame(self.root, bg=BG)
        # 只占自身所需高度，多出来的空间全给详情区（否则两者会平分空间、留出大片空白）
        self.compact.pack(fill="x")

        self.header = tk.Frame(self.compact, bg=BG, height=30)
        self.header.pack(fill="x", padx=12, pady=(10, 0))
        self.dot = tk.Canvas(self.header, width=10, height=10, bg=BG, highlightthickness=0)
        self.dot.pack(side="left", pady=6)
        self.dot_id = self.dot.create_oval(1, 1, 9, 9, fill=MUTED, outline="")
        self.title_lbl = tk.Label(self.header, text="正在连接…", bg=BG, fg=FG,
                                  font=(FONT, 11, "bold"), anchor="w")
        self.title_lbl.pack(side="left", padx=(7, 0), fill="x", expand=True)
        self.close_btn = tk.Label(self.header, text="✕", bg=BG, fg=MUTED,
                                  font=(FONT, 10), cursor="hand2")
        self.close_btn.pack(side="right", padx=(6, 0))
        self.close_btn.bind("<Button-1>", lambda e: self.quit())
        self.pin_btn = tk.Label(self.header, text="📌", bg=BG, fg=ACCENT,
                                font=(FONT, 9), cursor="hand2")
        self.pin_btn.pack(side="right")
        self.pin_btn.bind("<Button-1>", lambda e: self.toggle_pin())

        self.artist_lbl = tk.Label(self.compact, text="", bg=BG, fg=MUTED,
                                   font=(FONT, 9), anchor="w")
        self.artist_lbl.pack(fill="x", padx=12)

        body = tk.Frame(self.compact, bg=BG)
        body.pack(fill="x", padx=12, pady=(8, 0))
        self.bitrate_lbl = tk.Label(body, text="--", bg=BG, fg=FG,
                                    font=(FONT, 19, "bold"), anchor="w")
        self.bitrate_lbl.pack(anchor="w")
        self.tier_lbl = tk.Label(body, text="", bg=BG, fg=MUTED,
                                 font=(FONT, 9), anchor="w", justify="left")
        self.tier_lbl.pack(anchor="w")

        self.progress = tk.Canvas(self.compact, height=6, bg=BG, highlightthickness=0)
        self.progress.pack(fill="x", padx=12, pady=(9, 0))
        self.progress.bind("<Configure>", lambda e: self.draw_progress())

        times = tk.Frame(self.compact, bg=BG)
        times.pack(fill="x", padx=12, pady=(3, 0))
        self.time_lbl = tk.Label(times, text="--:-- / --:--", bg=BG, fg=MUTED,
                                 font=(FONT, 8), anchor="w")
        self.time_lbl.pack(side="left")
        self.enc_lbl = tk.Label(times, text="", bg=BG, fg=MUTED, font=(FONT, 8), anchor="e")
        self.enc_lbl.pack(side="right")

        self.level = tk.Canvas(self.compact, height=26, bg=BG, highlightthickness=0)
        self.level.pack(fill="x", padx=12, pady=(4, 0))
        self.level.bind("<Configure>", lambda e: self.draw_level())

        footer = tk.Frame(self.compact, bg=BG)
        footer.pack(fill="x", padx=12, pady=(2, 8))
        self.footer_lbl = tk.Label(footer, text="", bg=BG, fg=MUTED,
                                   font=(FONT, 8), anchor="w")
        self.footer_lbl.pack(side="left")
        self.expand_btn = tk.Label(footer, text="展开 ▾", bg=BG, fg=ACCENT,
                                   font=(FONT, 8), cursor="hand2")
        self.expand_btn.pack(side="right")
        self.expand_btn.bind("<Button-1>", lambda e: self.toggle())

    def _build_detail(self):
        # 详情区用「Canvas + 内嵌 Frame」实现纵向滚动，
        # 这样即使屏幕只有 768 高，全部参数也能看全。
        self.detail = tk.Frame(self.root, bg=CARD)
        self.detail_canvas = tk.Canvas(self.detail, bg=CARD, highlightthickness=0)
        self.detail_bar = tk.Scrollbar(self.detail, orient="vertical",
                                       command=self.detail_canvas.yview)
        self.detail_canvas.configure(yscrollcommand=self.detail_bar.set)
        self.detail_canvas.pack(side="left", fill="both", expand=True)
        self.detail_bar.pack(side="right", fill="y")

        inner = tk.Frame(self.detail_canvas, bg=CARD)
        self.detail_inner = inner
        self.detail_window = self.detail_canvas.create_window((0, 0), window=inner,
                                                              anchor="nw")
        inner.bind("<Configure>", lambda e: self.detail_canvas.configure(
            scrollregion=self.detail_canvas.bbox("all")))
        self.detail_canvas.bind("<Configure>", lambda e: self.detail_canvas.itemconfig(
            self.detail_window, width=e.width))
        for widget in (self.detail_canvas, inner):
            widget.bind("<MouseWheel>", self._on_wheel)

        self.detail_song = tk.Label(inner, text="", bg=CARD, fg=FG,
                                    font=(FONT, 11, "bold"), anchor="w",
                                    justify="left", wraplength=EXPANDED_W - 70)
        self.detail_song.pack(fill="x", padx=14, pady=(12, 0))

        head = tk.Frame(inner, bg=CARD)
        head.pack(fill="x", padx=14, pady=(8, 4))
        tk.Label(head, text="音质参数", bg=CARD, fg=ACCENT,
                 font=(FONT, 10, "bold")).pack(side="left")
        self.detail_source = tk.Label(head, text="", bg=CARD, fg=MUTED, font=(FONT, 8))
        self.detail_source.pack(side="right")

        self.param_grid = tk.Frame(inner, bg=CARD)
        self.param_grid.pack(fill="x", padx=14, pady=(6, 10))
        self.param_labels = {}
        fields = [("codec", "编码格式"), ("declared", "声明码率"), ("measured", "实测码率"),
                  ("rate", "采样率"), ("channels", "声道"), ("bits", "位深"),
                  ("lossless", "是否无损"), ("crypto", "加密方式"),
                  ("container", "容器格式"), ("tier", "推断档位")]
        for i, (key, label) in enumerate(fields):
            r, c = divmod(i, 2)
            cell = tk.Frame(self.param_grid, bg=CARD2)
            cell.grid(row=r, column=c, sticky="nsew", padx=3, pady=3)
            self.param_grid.columnconfigure(c, weight=1)
            tk.Label(cell, text=label, bg=CARD2, fg=MUTED,
                     font=(FONT, 8), anchor="w").pack(anchor="w", padx=8, pady=(4, 0))
            value = tk.Label(cell, text="—", bg=CARD2, fg=FG,
                             font=(FONT, 10, "bold"), anchor="w")
            value.pack(anchor="w", padx=8, pady=(0, 4))
            self.param_labels[key] = value

        tk.Label(inner, text="当前缓存文件", bg=CARD, fg=ACCENT,
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=14, pady=(2, 2))
        self.cache_lbl = tk.Label(inner, text="—", bg=CARD, fg=FG, justify="left",
                                  anchor="w", font=(FONT, 8), wraplength=EXPANDED_W - 70)
        self.cache_lbl.pack(fill="x", padx=14)
        open_btn = tk.Label(inner, text="打开缓存目录", bg=CARD, fg=ACCENT,
                            font=(FONT, 8, "underline"), cursor="hand2")
        open_btn.pack(anchor="w", padx=14, pady=(2, 8))
        open_btn.bind("<Button-1>", lambda e: self.open_cache_dir())

        hist_head = tk.Frame(inner, bg=CARD)
        hist_head.pack(fill="x", padx=14)
        tk.Label(hist_head, text="同一首歌的历史档位", bg=CARD, fg=ACCENT,
                 font=(FONT, 10, "bold")).pack(side="left")
        self.hist_hint = tk.Label(hist_head, text="", bg=CARD, fg=MUTED, font=(FONT, 8))
        self.hist_hint.pack(side="right")

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Soda.Treeview", background=CARD2, fieldbackground=CARD2,
                        foreground=FG, rowheight=19, borderwidth=0, font=(FONT, 8))
        style.configure("Soda.Treeview.Heading", background=CARD2, foreground=MUTED,
                        font=(FONT, 8), borderwidth=0)
        style.map("Soda.Treeview", background=[("selected", "#2d4a6b")])

        cols = ("time", "tier", "declared", "measured", "codec", "detail")
        self.tree = ttk.Treeview(inner, columns=cols, show="headings",
                                 height=5, style="Soda.Treeview")
        for col, text, width in (("time", "缓存时间", 88), ("tier", "档位", 46),
                                 ("declared", "声明", 58), ("measured", "实测", 58),
                                 ("codec", "编码", 62), ("detail", "参数", 250)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="x", padx=14, pady=(4, 8))

        self.stats_lbl = tk.Label(inner, text="缓存库统计：扫描中…", bg=CARD,
                                  fg=MUTED, justify="left", anchor="w", font=(FONT, 8))
        self.stats_lbl.pack(fill="x", padx=14)
        self.proc_lbl = tk.Label(inner, text="", bg=CARD, fg=MUTED,
                                 justify="left", anchor="w", font=(FONT, 8))
        self.proc_lbl.pack(fill="x", padx=14, pady=(4, 12))

    def _on_wheel(self, event):
        self.detail_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def open_cache_dir(self):
        try:
            os.startfile(ac.CACHE_DIR)  # noqa: S606
        except OSError:
            pass

    # ---------- 绘制 ----------
    def draw_progress(self):
        self.progress.delete("all")
        w = self.progress.winfo_width()
        if w <= 1:
            return
        rounded_rect(self.progress, 0, 0, w, 6, 3, fill=CARD2, outline="")
        frac = 0.0
        dur = self.tracker.duration or (self.state.get("duration") or 0)
        if dur:
            frac = min(1.0, self.tracker.value() / dur)
        if frac > 0:
            rounded_rect(self.progress, 0, 0, max(6, w * frac), 6, 3,
                         fill=self.quality.get("tier_color", ACCENT) if self.quality else ACCENT,
                         outline="")

    def draw_level(self, peak=None):
        self.level.delete("all")
        w = self.level.winfo_width()
        h = self.level.winfo_height()
        if w <= 1:
            return
        bars = len(self.level_history)
        gap = 2
        bw = max(2, (w - gap * (bars - 1)) / bars)
        for i, val in enumerate(self.level_history):
            x = i * (bw + gap)
            bh = max(1.5, val * (h - 2))
            color = OK if val < 0.65 else (WARN if val < 0.88 else ERR)
            self.level.create_rectangle(x, h - bh, x + bw, h, fill=color, outline="")

    # ---------- 数据 ----------
    def refresh_quality(self, title, duration):
        """按总时长 + 文件占用状态锁定当前歌曲的缓存文件并解析音质。"""
        self.last_match_try = time.monotonic()
        if not duration:
            self.quality = None
            return
        if not ac.index_ready():
            # 首次全量扫描还在后台跑，先不阻塞界面，稍后自动重试
            self.quality = None
            return
        try:
            self.quality = ac.find_current(duration)
        except Exception as exc:  # noqa: BLE001
            self.quality = None
            self.error = f"缓存解析失败: {exc}"

    def retry_match_if_needed(self, state):
        """推测出来的匹配结果不可靠，播放期间定期重试，拿到更强信号就升级。"""
        duration = state.get("duration")
        if not duration or not state.get("playing"):
            return
        if self.quality and self.quality.get("matched_by") in ("lock", "single"):
            return
        if time.monotonic() - self.last_match_try < 5:
            return
        self.refresh_quality(state.get("title"), duration)

    def scan_stats_async(self):
        if self.stats_loading:
            return

        def work():
            self.stats_loading = True
            try:
                rows = ac.scan_all()
                size = ac.cache_dir_size()
                self.stats = {"tier": ac.tier_stats(rows), "dir": size}
            except Exception as exc:  # noqa: BLE001
                self.error = f"统计失败: {exc}"
            finally:
                self.stats_loading = False

        threading.Thread(target=work, daemon=True).start()

    def update_history(self, duration):
        rows = ac.history_of(duration, entries=None) if duration else []
        rows = rows[:6]
        signature = [(r["file"], r.get("declared_kbps")) for r in rows]
        if signature == self.history_rows:
            return
        self.history_rows = signature
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            bits = f" {r['bits_per_sample']}bit" if r.get("bits_per_sample") else ""
            detail = (f"{r.get('sample_rate')}Hz · {r.get('channels')}声道{bits} · "
                      f"{'加密' if r.get('encrypted') else '明文'}")
            self.tree.insert("", "end", values=(
                r["mtime"].strftime("%m-%d %H:%M") if r.get("mtime") else "?",
                r.get("tier", "?"),
                f"{r.get('declared_kbps')}k" if r.get("declared_kbps") else "—",
                f"{r.get('measured_kbps')}k" if r.get("measured_kbps") else "—",
                r.get("codec", "?"),
                detail))
        self.hist_hint.config(text=f"共 {len(rows)} 个版本" if rows else "无记录")

    # ---------- 主循环 ----------
    def tick(self):
        try:
            self.backend.ensure_alive()
            state = self.backend.read()
        except Exception as exc:  # noqa: BLE001
            state = None
            self.error = str(exc)

        if state:
            self.state = state
            self.retry_match_if_needed(state)
            self.apply(state)

        # 汽水音乐自身窗口也是置顶的，会盖住面板，所以定期重新抬升层级
        if self.pinned and time.monotonic() - self.last_lift > 3:
            self.last_lift = time.monotonic()
            try:
                self.root.lift()
                self.root.attributes("-topmost", True)
            except tk.TclError:
                pass

        if time.monotonic() - self.last_stats_scan > 60:
            self.last_stats_scan = time.monotonic()
            self.scan_stats_async()

        self.enforce_geometry()
        self.draw_progress()
        self.root.after(400, self.tick)

    def apply(self, state):
        playing = state.get("playing")
        title = state.get("title") or ""
        artist = state.get("artist") or ""
        duration = state.get("duration") or 0
        status = state.get("status") or "None"

        self.dot.itemconfig(self.dot_id, fill=OK if playing else (WARN if title else MUTED))

        key = (title, artist, duration)
        if key != self.track_key:
            self.track_key = key
            self.tracker.reset(duration or None, state.get("position") or 0.0)
            self.refresh_quality(title, duration)
            if duration:
                self.update_history(duration)

        pos = self.tracker.update(status, state.get("position"), duration)

        self.title_lbl.config(text=ellipsize(title, 22) or "未检测到播放")
        self.artist_lbl.config(text=ellipsize(artist, 30) or
                               ("汽水音乐未在播放" if not title else ""))
        self.detail_song.config(
            text=(f"{title}\n{artist}" if artist else title) if title else "未检测到播放",
            fg=FG if title else MUTED)

        q = self.quality
        if q:
            color = q.get("tier_color", FG)
            self.bitrate_lbl.config(
                text=f"{q.get('declared_kbps') or q.get('measured_kbps') or '--'} kbps", fg=color)
            rate = q.get("sample_rate")
            rate_s = f"{rate / 1000:.1f}kHz" if rate else "—"
            guess = "" if q.get("matched_by") in ("lock", "single") else " · 推测"
            self.tier_lbl.config(
                text=f"{q.get('tier', '?')} · {q.get('codec', '?')} · {rate_s} · "
                     f"{q.get('channels', '?')}声道{guess}",
                fg=color)
            enc = "AES-128 加密" if q.get("encrypted") else "未加密"
            self.enc_lbl.config(text=enc)
            self.footer_lbl.config(
                text=f"缓存 {q.get('size_mb', '—')}MB · 实测 {q.get('measured_kbps', '—')}kbps")
            self.detail_source.config(
                text=f"匹配缓存 {q.get('file', '')[:8]} · {ac.match_reason(q)}")
        else:
            self.bitrate_lbl.config(text="-- kbps", fg=MUTED)
            hint = ("正在建立缓存索引…" if not ac.index_ready()
                    else "未匹配到缓存文件（可能尚未落盘）")
            self.tier_lbl.config(text=hint, fg=MUTED)
            self.enc_lbl.config(text="")
            self.footer_lbl.config(text="")
            self.detail_source.config(text="未匹配到缓存")

        self.time_lbl.config(text=f"{fmt_time(pos)} / {fmt_time(duration)}")

        peak = state.get("audio_peak")
        if peak is not None:
            self.level_history.append(min(1.0, float(peak) ** 0.5))
            self.level_history.pop(0)
            self.draw_level()

        self.update_params(q)
        self.update_stats()
        self.update_proc(state)
        self.draw_progress()

    def update_params(self, q):
        if not q:
            for lbl in self.param_labels.values():
                lbl.config(text="—", fg=MUTED)
            self.cache_lbl.config(text="—")
            return

        def put(key, text, color=FG):
            self.param_labels[key].config(text=text, fg=color)

        bits = f"{q['bits_per_sample']} bit" if q.get("bits_per_sample") else "不含此概念"
        put("codec", q.get("codec", "—"))
        put("declared", f"{q.get('declared_kbps')} kbps" if q.get("declared_kbps") else "—")
        put("measured", f"{q.get('measured_kbps')} kbps" if q.get("measured_kbps") else "—")
        put("rate", f"{q.get('sample_rate')} Hz" if q.get("sample_rate") else "—")
        put("channels", f"{q.get('channels')} ({'立体声' if q.get('channels') == 2 else '单声道'})"
            if q.get("channels") else "—")
        put("bits", bits)
        put("lossless", "是（真无损）" if q.get("lossless") else "否（有损压缩）",
            OK if q.get("lossless") else FG)
        put("crypto", "cenc / AES-128" if q.get("encrypted") else "未加密")
        put("container", f"M4A / {q.get('container_codec', '?')}")
        put("tier", q.get("tier", "—"), q.get("tier_color", FG))

        mt = q.get("mtime")
        try:
            at = datetime.datetime.fromtimestamp(os.path.getatime(q.get("path", "")))
            at_s = at.strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            at_s = "?"
        cache_text = (f"{q.get('file', '?')}\n"
                      f"{q.get('size_mb', '?')} MB · 时长 {q.get('duration', '?')}s\n"
                      f"缓存于 {mt.strftime('%Y-%m-%d %H:%M:%S') if mt else '?'} · "
                      f"最后访问 {at_s}\n"
                      f"匹配依据：{ac.match_reason(q)}\n"
                      f"{q.get('path', '')}")
        self.cache_lbl.config(text=cache_text)

    def update_stats(self):
        if not self.stats:
            self.stats_lbl.config(text="缓存库统计：扫描中…")
            return
        tier = self.stats["tier"]
        parts = [f"{k} {v['count']}首" for k, v in
                 sorted(tier["by_tier"].items(), key=lambda x: -x[1]["count"])]
        self.stats_lbl.config(
            text=f"缓存库：{tier['total_count']} 首音频 · {tier['total_mb']} MB ｜ "
                 f"目录共 {self.stats['dir']['count']} 个文件 "
                 f"{self.stats['dir']['bytes'] / 1073741824:.2f} GB\n" + " ｜ ".join(parts))

    def update_proc(self, state):
        proc = state.get("process")
        io = state.get("io_rate_kbps")
        peak = state.get("audio_peak")
        if not proc:
            self.proc_lbl.config(
                text=f"进程信息不可用（沙箱环境下看不到汽水音乐进程）\n"
                     f"音频电平 {peak if peak is not None else '—'} ｜ "
                     f"IO 速率 {io if io is not None else '—'} KB/s")
            return
        self.proc_lbl.config(
            text=f"进程 PID {proc.get('pid')} · CPU {proc.get('cpu_seconds')}s · "
                 f"内存 {proc.get('mem_mb')} MB ｜ 音频电平 "
                 f"{peak if peak is not None else '—'} ｜ IO 速率 "
                 f"{io if io is not None else '—'} KB/s")


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
    if "--expanded" in sys.argv:
        app.toggle()
    app.root.mainloop()
