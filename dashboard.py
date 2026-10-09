# -*- coding: utf-8 -*-
"""Native Tk music dashboard, backed by the existing SMTC collector."""
import datetime
import os
import threading
import time
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont
from glass_theme import apply_frosted_glass
from music_visualizer import MusicVisualizer
from artwork import ArtworkSource
from album_theme import palette_for

import analyze_cache as ac
from ui_widgets import (ACCENT, BG, CARD, CARD2, ERR, FG, FONT, LINE, METRICS,
                        MUTED, NUMBER_FONT, OK, TIER_COLORS, WARN, ActionButton,
                        RoundedCard, SampleHistory, Segments, TierDistribution,
                        TrendChart, rounded_rect)
from ui_widgets import TransportButton

COMPACT_W = 420
EXPANDED_W = 960


def fmt_time(seconds):
    if seconds is None:
        return "--:--"
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class DashboardPanel:
    def __init__(self, backend, tracker):
        self.backend, self.tracker = backend, tracker
        self.state, self.quality, self.track_key = {}, None, None
        self.expanded, self.pinned, self.user_moved = False, True, False
        self.drag, self.desired = (0, 0), None
        self.stats, self.stats_loading, self.error = None, False, None
        self.last_stats_scan = self.last_lift = self.last_match_try = 0
        self.history_rows = None
        self.samples = SampleHistory()
        self.last_seen = None
        self.page, self.metric, self.seconds = "reactive", "audio", 60
        self.cover_photo, self.cover_stamp, self.cover_track = None, None, None
        self.cover_image, self.art_result = None, None
        self.artwork = ArtworkSource(os.path.dirname(backend.state_path))
        self.theme_generation = 0
        self.last_control_id, self.control_notice_until = None, 0

        self.root = tk.Tk()
        self.root.title("汽水音乐 · 音乐仪表盘")
        self.root.configure(bg=BG, highlightthickness=1, highlightbackground=LINE)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.geometry(f"{COMPACT_W}x390+{self.root.winfo_screenwidth() - COMPACT_W - 28}+36")
        self._build_compact()
        self._build_detail()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.bind("<Escape>", lambda event: self.quit())
        self.root.bind_all("<MouseWheel>", self._on_wheel)
        self.root.bind("<Control-e>", lambda event: self.toggle())
        self.backend.start()
        self.fit_window()
        self.tick()
        self.animate()
        self.root.after(200, self.fit_window)
        self.root.after(250, self._round_window)

    def _round_window(self):
        self.glass_enabled = apply_frosted_glass(self.root, BG)

    def fit_window(self):
        self.root.update_idletasks()
        compact_h = self.compact.winfo_reqheight() + 2
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        width = min(EXPANDED_W if self.expanded else COMPACT_W, screen_w - 24)
        height = compact_h
        if self.expanded:
            inner_h = self.detail_inner.winfo_reqheight()
            detail_h = min(inner_h + 4, 386)
            self.detail_canvas.configure(height=detail_h)
            self.root.update_idletasks()
            height = self.compact.winfo_reqheight() + 2
            if inner_h > detail_h:
                self.detail_bar.pack(side="right", fill="y", padx=(4, 0))
            else:
                self.detail_bar.pack_forget()
        x = self.root.winfo_x() if self.user_moved else screen_w - width - 28
        y = self.root.winfo_y() if self.user_moved else 36
        x, y = max(8, min(x, screen_w - width - 8)), max(8, min(y, screen_h - height - 8))
        self.desired = width, height, x, y
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def enforce_geometry(self):
        if self.user_moved or not self.desired:
            return
        width, height, x, y = self.desired
        want = f"{width}x{height}+{x}+{y}"
        if self.root.geometry() != want:
            self.root.geometry(want)

    def _bind_drag(self, *widgets):
        for widget in widgets:
            widget.bind("<Button-1>", self.start_drag)
            widget.bind("<B1-Motion>", self.do_drag)
            widget.bind("<Double-Button-1>", lambda event: self.toggle())

    def start_drag(self, event):
        self.drag = event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y()

    def do_drag(self, event):
        self.user_moved = True
        self.root.geometry(f"+{event.x_root - self.drag[0]}+{event.y_root - self.drag[1]}")

    def toggle(self):
        self.expanded = not self.expanded
        self.hero.pack_forget()
        self.cover.pack_forget()
        self.hero_text.pack_forget()
        if self.expanded:
            self.summary.configure(padx=12)
            self.hero.configure(width=324)
            self.hero.pack(side="left", anchor="n", padx=(0, 36))
            self.cover.configure(width=324, height=324)
            self.cover.pack()
            self.hero_text.pack(fill="x", padx=12, pady=(15, 0))
            self.title_lbl.configure(font=(FONT, 20, "bold"))
            self.artist_lbl.configure(font=(FONT, 10))
            self.mini_card.pack_forget()
            self.detail.pack(side="right", fill="both", expand=True, pady=(7, 0))
            self.expand_btn.configure(text="收起  ↙")
            self.detail_canvas.yview_moveto(0)
            self.tier_lbl.pack(side="left", padx=(14, 0))
        else:
            self.summary.configure(padx=0)
            self.hero.pack(fill="x", pady=(0, 12))
            self.cover.configure(width=110, height=110)
            self.cover.pack(side="left")
            self.hero_text.pack(side="left", fill="both", expand=True, padx=(10, 0))
            self.title_lbl.configure(font=(FONT, 14, "bold"))
            self.artist_lbl.configure(font=(FONT, 9))
            self.detail.pack_forget()
            self.tier_lbl.pack_forget()
            self.mini_card.pack(fill="x", padx=22, pady=(3, 14), before=self.dock)
            self.expand_btn.configure(text="展开  ↗")
        self._render_cover()
        self.fit_window()
        self._clip_song()
        self.root.after(100, self.fit_window)

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.root.attributes("-topmost", self.pinned)
        self.pin_btn.set_active(self.pinned)
        self.pin_btn.configure(text="已置顶" if self.pinned else "置顶")

    def quit(self):
        self.artwork.close()
        self.backend.stop()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    @staticmethod
    def label(parent, text="", size=9, color=FG, bold=False, **kwargs):
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                        font=(FONT, size, "bold" if bold else "normal"), **kwargs)

    def _build_compact(self):
        self.compact = tk.Frame(self.root, bg=BG)
        self.compact.pack(fill="both", expand=True)
        self.header = tk.Frame(self.compact, bg=BG)
        self.header.pack(fill="x", padx=24, pady=(16, 18))
        logo = tk.Canvas(self.header, width=26, height=26, bg=BG, highlightthickness=0)
        logo.pack(side="left")
        for x, y in ((6, 10), (13, 5), (20, 8)):
            logo.create_line(x, y, x, 26-y, fill=FG, width=3, capstyle="round")
        brand = self.label(self.header, "汽水音乐", size=11, bold=True)
        brand.pack(side="left", padx=(8, 0))
        self.close_btn = ActionButton(self.header, "×", self.quit)
        self.close_btn.configure(font=(NUMBER_FONT, 17), pady=0)
        self.close_btn.pack(side="right", padx=(8, 0))
        self.pin_btn = ActionButton(self.header, "已置顶", self.toggle_pin, active=True)
        self.pin_btn.pack(side="right")
        self._bind_drag(self.header, brand, logo)

        self.summary = tk.Frame(self.compact, bg=BG)
        self.summary.pack(fill="x", padx=22, pady=(5, 22))
        self.hero = tk.Frame(self.summary, bg=BG)
        self.hero.pack(fill="x", pady=(0, 12))
        self.cover = tk.Canvas(self.hero, width=110, height=110, bg=BG, highlightthickness=0)
        self.cover.pack(side="left")
        self._draw_cover_placeholder()
        self.hero_text = tk.Frame(self.hero, bg=BG)
        self.hero_text.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.title_lbl = self.label(self.hero_text, "等待音乐响起", size=14, bold=True, anchor="w", width=1)
        self.title_lbl.pack(fill="x")
        self.artist_lbl = self.label(self.hero_text, "打开汽水音乐，开始播放", color=MUTED, anchor="w", width=1)
        self.artist_lbl.pack(fill="x", pady=(7, 0))
        self.status_lbl = self.label(self.hero_text, "●  正在连接", size=8, color=MUTED, anchor="w")
        self.status_lbl.pack(fill="x", pady=(12, 0))
        self.hero_text.bind("<Configure>", lambda event: self._clip_song())
        self._bind_drag(self.hero, self.hero_text, self.title_lbl, self.artist_lbl, self.cover)

        self.mini_card = RoundedCard(self.compact, padding=11)
        self.mini_card.pack(fill="x", padx=22, pady=(3, 14))
        mini_head = tk.Frame(self.mini_card.body, bg=CARD)
        mini_head.pack(fill="x")
        self.label(mini_head, "音乐律动", size=8, color=MUTED).pack(side="left")
        self.level_value = self.label(mini_head, "—", size=8, color=ACCENT)
        self.level_value.pack(side="right")
        self.level = MusicVisualizer(self.mini_card.body, mini=True)
        self.level.set_theme(ACCENT,CARD)
        self.level.pack(fill="x", pady=(4, 0))

        self.dock = tk.Frame(self.compact, bg=CARD)
        self.dock.pack(fill="x", padx=1)
        self.progress = tk.Canvas(self.dock, height=8, bg=CARD, highlightthickness=0)
        self.progress.pack(fill="x")
        self.progress.bind("<Configure>", lambda event: self.draw_progress())
        self.progress.bind("<Button-1>", self.seek)
        self.dock_row = tk.Frame(self.dock, bg=CARD)
        self.dock_row.pack(fill="x", padx=22, pady=(9, 3))
        self.time_lbl = self.label(self.dock_row, "--:-- / --:--", size=8, color=MUTED)
        self.time_lbl.pack(side="left")
        controls = tk.Frame(self.dock_row, bg=CARD)
        controls.pack(side="right")
        self.transport = {}
        for action in ("previous", "play_pause", "next"):
            button = TransportButton(controls, action, lambda a=action: self.control(a), primary=action=="play_pause")
            button.pack(side="left", padx=8)
            self.transport[action] = button

        self.quality_card = RoundedCard(self.dock, padding=10, color=CARD)
        self.quality_card.pack(fill="x", padx=12, pady=(0, 5))
        self.metric_labels = {}
        for index, (key, caption) in enumerate((("bitrate", "码率 · kbps"), ("rate", "采样率 · kHz"), ("codec", "音频编码"))):
            self.quality_card.body.columnconfigure(index, weight=1, uniform="metric")
            cell = tk.Frame(self.quality_card.body, bg=CARD)
            cell.grid(row=0, column=index, sticky="nsew", padx=10)
            self.label(cell, caption, size=8, color=MUTED, anchor="w").pack(fill="x")
            value = self.label(cell, "—", size=12, bold=True, anchor="w")
            value.pack(fill="x", pady=(3, 0))
            self.metric_labels[key] = value
        self.bitrate_lbl = self.metric_labels["bitrate"]
        self.tier_lbl = self.label(self.dock_row, "等待音质", size=8, color=MUTED)
        self.footer = tk.Frame(self.compact, bg=BG)
        self.footer.pack(fill="x", padx=22, pady=(9, 12))
        self.footer_lbl = self.label(self.footer, "等待连接…", size=8, color=MUTED, anchor="w")
        self.footer_lbl.pack(side="left", fill="x", expand=True)
        self.expand_btn = ActionButton(self.footer, "展开  ↗", self.toggle, active=True)
        self.expand_btn.pack(side="right")

    def control(self, action):
        if not self.backend.control(action):
            self.footer_lbl.configure(text="播放控制暂不可用", fg=WARN)

    def seek(self, event):
        duration = self.state.get("duration") or 0
        if duration and self.state.get("can_seek"):
            position = duration * max(0, min(1, event.x / max(1, self.progress.winfo_width())))
            self.backend.control("seek", position)

    def _build_detail(self):
        self.detail = tk.Frame(self.summary, bg=BG)
        self.tab_bar = tk.Frame(self.detail, bg=BG)
        self.tab_bar.pack(fill="x", pady=(0, 16))
        self.tabs = Segments(self.tab_bar, (("reactive", "音乐律动"), ("trends", "监测曲线"), ("quality", "音质与缓存")),
                             "reactive", self.switch_page)
        self.tabs.pack(side="left")
        self.detail_source = self.label(self.tab_bar, "", size=8, color=MUTED)
        self.detail_source.pack_forget()
        area = tk.Frame(self.detail, bg=BG)
        area.pack(fill="both", expand=True)
        self.detail_canvas = tk.Canvas(area, bg=BG, height=386, highlightthickness=0)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Soda.Vertical.TScrollbar", troughcolor=BG, background=LINE,
                        bordercolor=BG, arrowcolor=MUTED, lightcolor=LINE, darkcolor=LINE,
                        width=9, arrowsize=9)
        self.detail_bar = ttk.Scrollbar(area, orient="vertical", command=self.detail_canvas.yview,
                                        style="Soda.Vertical.TScrollbar")
        self.detail_canvas.configure(yscrollcommand=self.detail_bar.set)
        self.detail_canvas.pack(side="left", fill="both", expand=True)
        self.detail_inner = tk.Frame(self.detail_canvas, bg=BG)
        self.detail_window = self.detail_canvas.create_window((0, 0), window=self.detail_inner, anchor="nw")
        self.detail_inner.bind("<Configure>", lambda event: self.detail_canvas.configure(
            scrollregion=self.detail_canvas.bbox("all")))
        self.detail_canvas.bind("<Configure>", lambda event: self.detail_canvas.itemconfigure(
            self.detail_window, width=event.width))
        self.trends_page = tk.Frame(self.detail_inner, bg=BG)
        self.quality_page = tk.Frame(self.detail_inner, bg=BG)
        self.reactive_page = tk.Frame(self.detail_inner, bg=BG)
        self.reactive_page.pack(fill="x")

        self.label(self.reactive_page, "声音，正在流动", size=17, bold=True, anchor="center").pack(fill="x", pady=(10, 7))
        self.reactive_status = self.label(self.reactive_page, "●  连接音频中", size=8, color=MUTED)
        self.reactive_status.pack()
        self.visualizer = MusicVisualizer(self.reactive_page)
        self.visualizer.set_theme(ACCENT,BG)
        self.visualizer.configure(height=245)
        self.visualizer.pack(fill="x", pady=(8, 10))
        self.visual_modes = Segments(self.reactive_page, (("ring", "律动光环"), ("bars", "跳动频谱"), ("wave", "流动波形")),
                                     "ring", self.change_visual_mode)
        self.visual_modes.pack(pady=(2, 4))

        chart_card = RoundedCard(self.trends_page, padding=16)
        chart_card.pack(fill="x")
        chart_head = tk.Frame(chart_card.body, bg=CARD)
        chart_head.pack(fill="x")
        self.label(chart_head, "实时监测趋势", size=11, bold=True).pack(side="left")
        self.ranges = Segments(chart_head, ((30, "30 秒"), (60, "60 秒"), (120, "120 秒")),
                               60, self.change_range)
        self.ranges.pack(side="right")
        tools = tk.Frame(chart_card.body, bg=CARD)
        tools.pack(fill="x", pady=(12, 3))
        self.metric_switch = Segments(tools, (("audio", "音频电平"), ("io", "读写速率"), ("memory", "内存占用")),
                                      "audio", self.change_metric, bg=CARD)
        self.metric_switch.pack(side="left")
        self.chart_value = self.label(tools, "— %", size=17, color=ACCENT, bold=True)
        self.chart_value.pack(side="right")
        self.chart = TrendChart(chart_card.body, self.samples)
        self.chart.pack(fill="x", pady=(5, 0))
        self.chart_note = self.label(chart_card.body, "Windows 系统输出电平 · 鼠标移到曲线上查看采样值",
                                      size=8, color=MUTED, anchor="w")
        self.chart_note.pack(fill="x", pady=(5, 0))

        library_card = RoundedCard(self.trends_page, padding=14)
        library_card.pack(fill="x", pady=(12, 0))
        library_head = tk.Frame(library_card.body, bg=CARD)
        library_head.pack(fill="x")
        self.label(library_head, "缓存音质分布", size=10, bold=True).pack(side="left")
        self.stats_summary = self.label(library_head, "扫描中…", size=8, color=MUTED)
        self.stats_summary.pack(side="right")
        self.distribution = TierDistribution(library_card.body)
        self.distribution.pack(fill="x", pady=(5, 0))

        params_card = RoundedCard(self.quality_page, padding=14)
        params_card.pack(fill="x")
        self.label(params_card.body, "音质参数", size=11, bold=True).pack(anchor="w", pady=(0, 10))
        grid = tk.Frame(params_card.body, bg=CARD)
        grid.pack(fill="x")
        self.param_labels = {}
        fields = [("codec", "编码格式"), ("declared", "声明码率"), ("measured", "实测码率"),
                  ("rate", "采样率"), ("channels", "声道"), ("bits", "位深"),
                  ("lossless", "是否无损"), ("crypto", "加密方式"),
                  ("container", "容器格式"), ("tier", "推断档位")]
        for i, (key, caption) in enumerate(fields):
            row, column = divmod(i, 3)
            grid.columnconfigure(column, weight=1, uniform="param")
            cell = tk.Frame(grid, bg=CARD2)
            cell.grid(row=row, column=column, sticky="nsew", padx=3, pady=3)
            self.label(cell, caption, size=8, color=MUTED, anchor="w").pack(fill="x", padx=10, pady=(8, 3))
            value = self.label(cell, "—", size=10, bold=True, anchor="w")
            value.pack(fill="x", padx=10, pady=(0, 9))
            self.param_labels[key] = value

        history_card = RoundedCard(self.quality_page, padding=14)
        history_card.pack(fill="x", pady=(12, 0))
        history_head = tk.Frame(history_card.body, bg=CARD)
        history_head.pack(fill="x", pady=(0, 10))
        self.label(history_head, "这首歌的缓存版本", size=10, bold=True).pack(side="left")
        self.hist_hint = self.label(history_head, "暂无记录", size=8, color=MUTED)
        self.hist_hint.pack(side="right")
        style.configure("Soda.Treeview", background=CARD, fieldbackground=CARD,
                        foreground=FG, rowheight=29, borderwidth=0, font=(FONT, 8))
        style.layout("Soda.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Soda.Treeview.Heading", background=CARD2, foreground=MUTED,
                        font=(FONT, 8), borderwidth=0, relief="flat")
        style.map("Soda.Treeview", background=[("selected", CARD2)], foreground=[("selected", FG)])
        cols = ("time", "tier", "declared", "measured", "codec", "detail")
        self.tree = ttk.Treeview(history_card.body, columns=cols, show="headings", height=4, style="Soda.Treeview")
        for column, caption, width in (("time", "缓存时间", 110), ("tier", "档位", 56),
                                       ("declared", "声明码率", 84), ("measured", "实测码率", 84),
                                       ("codec", "编码", 80), ("detail", "采样参数", 230)):
            self.tree.heading(column, text=caption)
            self.tree.column(column, width=width, minwidth=width, anchor="w", stretch=False)
        self.tree.pack(fill="x")
        style.configure("Soda.Horizontal.TScrollbar", troughcolor=CARD, background=LINE,
                        bordercolor=CARD, arrowcolor=MUTED, lightcolor=LINE, darkcolor=LINE,
                        width=8, arrowsize=8)
        tree_scroll = ttk.Scrollbar(history_card.body, orient="horizontal", command=self.tree.xview,
                                    style="Soda.Horizontal.TScrollbar")
        tree_scroll.pack(fill="x", pady=(4, 0))
        self.tree.configure(xscrollcommand=tree_scroll.set)

        cache_card = RoundedCard(self.quality_page, padding=14)
        cache_card.pack(fill="x", pady=(12, 0))
        cache_head = tk.Frame(cache_card.body, bg=CARD)
        cache_head.pack(fill="x")
        self.label(cache_head, "当前缓存文件", size=10, bold=True).pack(side="left")
        ActionButton(cache_head, "打开目录  ↗", self.open_cache_dir, active=True).pack(side="right")
        self.cache_lbl = self.label(cache_card.body, "等待缓存匹配", size=8, color=MUTED,
                                    anchor="w", justify="left", wraplength=460)
        self.cache_lbl.pack(fill="x", pady=(7, 0))
        self.proc_lbl = self.label(self.quality_page, "", size=8, color=MUTED, anchor="w", justify="left",
                                   wraplength=460)
        self.proc_lbl.pack(fill="x", padx=4, pady=(12, 3))
        self.library_details = self.label(self.quality_page, "", size=8, color=MUTED,
                                         anchor="w", wraplength=460)
        self.library_details.pack(fill="x", padx=4, pady=(0, 3))

    def switch_page(self, page):
        self.page = page
        self.trends_page.pack_forget()
        self.quality_page.pack_forget()
        self.reactive_page.pack_forget()
        {"reactive": self.reactive_page, "trends": self.trends_page, "quality": self.quality_page}[page].pack(fill="x")
        if page == "quality":
            self.update_history(self.state.get("duration") or 0)
        self.detail_canvas.yview_moveto(0)
        self.fit_window()
        self.root.after(100, self.fit_window)

    def change_metric(self, metric):
        self.metric = self.chart.metric = metric
        self.chart_note.configure(text={
            "audio": "Windows 系统输出电平 · 鼠标移到曲线上查看采样值",
            "io": "汽水音乐进程读写速率 · 鼠标移到曲线上查看采样值",
            "memory": "汽水音乐进程组内存占用 · 鼠标移到曲线上查看采样值"}[metric])
        self.draw_charts()

    def change_visual_mode(self, mode):
        self.visualizer.select_mode(mode)

    def animate(self):
        source = getattr(self.backend, "audio", None)
        frame = source.read() if source else None
        now = time.monotonic()
        self.visualizer.render(frame, now)
        self.level.render(frame, now)
        active = frame and frame.get("rms", 0) > .00001
        text = "●  正在律动" if active else "●  等待声音" if frame else "●  连接音频中"
        if self.reactive_status.cget("text") != text:
            self.reactive_status.configure(text=text, fg=ACCENT if active else MUTED)
        level_text = f"{frame['peak'] * 100:.1f}% · 实时" if frame else "等待声音"
        if self.level_value.cget("text") != level_text:
            self.level_value.configure(text=level_text)
        self.root.after(33, self.animate)

    def change_range(self, seconds):
        self.seconds = self.chart.seconds = seconds
        self.draw_charts()

    def _on_wheel(self, event):
        if self.expanded:
            self.detail_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def open_cache_dir(self):
        try:
            os.startfile(ac.CACHE_DIR)
        except OSError:
            self.footer_lbl.configure(text="缓存目录暂不可用", fg=WARN)

    def _clip_song(self):
        if not hasattr(self, "title_lbl"):
            return
        width = max(1, self.hero_text.winfo_width() - 5)
        for label, text in ((self.title_lbl, self.state.get("title") or "等待音乐响起"),
                            (self.artist_lbl, self.state.get("artist") or
                             ("汽水音乐" if self.state.get("title") else "打开汽水音乐，开始播放"))):
            font = tkfont.Font(font=label.cget("font"))
            clipped = text
            if font.measure(clipped) > width:
                while clipped and font.measure(clipped + "…") > width:
                    clipped = clipped[:-1]
                clipped += "…"
            label.configure(text=clipped)

    def _draw_cover_placeholder(self):
        self.cover.delete("all")
        size = int(self.cover.cget("width"))
        center, radius = size/2, size*.37
        rounded_rect(self.cover, 12, 12, size-12, size-12, 10, fill=CARD2, outline=LINE)
        self.cover.create_oval(center-radius,center-radius,center+radius,center+radius,fill=CARD,outline=LINE)
        for fraction in (.80,.64,.47):
            r=radius*fraction
            self.cover.create_oval(center-r,center-r,center+r,center+r,outline=LINE)
        r=radius*.19
        self.cover.create_oval(center-r,center-r,center+r,center+r,fill=ACCENT,outline="")

    def _render_cover(self):
        if self.cover_image is None:
            self._draw_cover_placeholder()
            return
        from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageTk
        size = int(self.cover.cget("width"))
        side, margin = size-24, 12
        image = ImageOps.fit(self.cover_image, (side,side), method=Image.Resampling.LANCZOS)
        mask = Image.new("L", (side,side), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0,0,side-1,side-1),radius=9,fill=255)
        shadow = Image.new("L", (size,size),0)
        ImageDraw.Draw(shadow).rounded_rectangle((margin,margin+6,size-margin,size-margin+6),radius=12,fill=95)
        shadow=shadow.filter(ImageFilter.GaussianBlur(8))
        rendered=Image.new("RGB", (size,size), BG)
        rendered.paste("#111114", (0,0,size,size),shadow)
        rendered.paste(image,(margin,margin),mask)
        self.cover_photo=ImageTk.PhotoImage(rendered,master=self.root)
        self.cover.delete("all")
        self.cover.create_image(size/2,size/2,image=self.cover_photo)

    def update_cover(self, track_changed=False):
        if track_changed:
            self.cover_image, self.art_result = None, None
            self._render_cover()
        result = self.artwork.request(self.state)
        if not result and self.cover_image is None and self.state.get("cover_path"):
            try:
                from PIL import Image
                with Image.open(self.state["cover_path"]) as image:
                    self.cover_image=image.convert("RGB").copy()
                self._render_cover()
            except (OSError,ValueError):
                pass
        if result and result is not self.art_result:
            self.art_result, self.cover_image = result, result["image"]
            self._transition_theme(palette_for(self.cover_image,result["colors"]))
            self._render_cover()

    def _transition_theme(self, target):
        from music_visualizer import mix_color
        self.theme_generation += 1
        generation = self.theme_generation
        start = {key: globals()[key] for key in target}
        def step(index):
            if generation != self.theme_generation:
                return
            amount=index/8
            self._apply_theme({key:mix_color(start[key],value,amount) for key,value in target.items()})
            if index<8:
                self.root.after(45,lambda:step(index+1))
        step(1)

    def _apply_theme(self, palette):
        import ui_widgets
        import music_visualizer
        old = {globals()[key]:value for key,value in palette.items()}
        def recolor(widget):
            options = widget.keys()
            for option in ("bg","fg","highlightbackground","highlightcolor"):
                if option in options and widget.cget(option) in old:
                    widget.configure(**{option:old[widget.cget(option)]})
            for attribute in ("color","normal_bg","surface"):
                value = getattr(widget,attribute,None)
                if value in old:
                    setattr(widget,attribute,old[value])
            for child in widget.winfo_children():
                recolor(child)
        recolor(self.compact)
        for key,value in palette.items():
            globals()[key]=value
            setattr(ui_widgets,key,value)
            setattr(music_visualizer,key,value)
        for metric, (caption,unit,color) in list(METRICS.items()):
            METRICS[metric]=(caption,unit,ACCENT if metric=="audio" else color)
        def repaint(widget):
            if isinstance(widget,RoundedCard):widget._resize()
            elif isinstance(widget,(Segments,TransportButton)):widget.paint()
            for child in widget.winfo_children():repaint(child)
        repaint(self.compact)
        self.visualizer.set_theme(ACCENT,self.visualizer.master.cget("bg"))
        self.level.set_theme(ACCENT,self.level.master.cget("bg"))
        style=ttk.Style(self.root)
        style.configure("Soda.Treeview",background=CARD,fieldbackground=CARD,foreground=FG)
        style.configure("Soda.Treeview.Heading",background=CARD2,foreground=MUTED)
        style.map("Soda.Treeview",background=[("selected",CARD2)],foreground=[("selected",FG)])
        style.configure("Soda.Vertical.TScrollbar",troughcolor=BG,background=LINE,bordercolor=BG,
                        arrowcolor=MUTED,lightcolor=LINE,darkcolor=LINE)
        style.configure("Soda.Horizontal.TScrollbar",troughcolor=CARD,background=LINE,bordercolor=CARD,
                        arrowcolor=MUTED,lightcolor=LINE,darkcolor=LINE)
        if not getattr(self,"glass_enabled",False):self.root.configure(bg=BG)
        self.root.configure(highlightbackground=LINE)
        self._render_cover()
        self.draw_progress()
        self.draw_charts()
        self.distribution.draw(self.distribution.stats)

    def draw_progress(self):
        self.progress.delete("all")
        width = self.progress.winfo_width()
        if width <= 1:
            return
        rounded_rect(self.progress, 0, 2, width, 6, 2, fill=LINE, outline="")
        duration = self.tracker.duration or self.state.get("duration") or 0
        fraction = min(1.0, self.tracker.value() / duration) if duration else 0
        if fraction:
            rounded_rect(self.progress, 0, 2, max(4, width * fraction), 6, 2, fill=ACCENT, outline="")
            self.progress.create_oval(width * fraction - 3, 1, width * fraction + 3, 7, fill=FG, outline="")
        self.time_lbl.configure(text=f"{fmt_time(self.tracker.value() if duration else None)} / {fmt_time(duration or None)}")

    def draw_charts(self):
        now = time.monotonic()
        self.chart.draw(now)
        latest = self.samples.samples[-1] if self.samples.samples else {}
        recent = now - latest.get("time", 0) <= 3
        value = latest.get(self.metric) if recent else None
        _, unit, color = METRICS[self.metric]
        self.chart_value.configure(text=f"{value:,.1f} {unit}" if value is not None else f"— {unit}", fg=color)

    def refresh_quality(self, title, duration):
        self.last_match_try = time.monotonic()
        if not duration or not ac.index_ready():
            self.quality = None
            return
        try:
            self.quality = ac.find_current(duration)
        except Exception as exc:
            self.quality = None
            self.error = f"缓存解析失败: {exc}"

    def retry_match_if_needed(self, state):
        if not state.get("duration") or not state.get("playing"):
            return
        if self.quality and self.quality.get("matched_by") in ("lock", "single"):
            return
        if time.monotonic() - self.last_match_try >= 5:
            self.refresh_quality(state.get("title"), state["duration"])

    def scan_stats_async(self):
        if self.stats_loading:
            return
        self.stats_loading = True

        def work():
            try:
                rows = ac.scan_all()
                self.stats = {"tier": ac.tier_stats(rows), "dir": ac.cache_dir_size()}
            except Exception as exc:
                self.error = f"统计失败: {exc}"
            finally:
                self.stats_loading = False
        threading.Thread(target=work, daemon=True).start()

    def update_history(self, duration):
        if duration and not ac.index_ready():
            return
        rows = ac.history_of(duration) if duration else []
        signature = [(row["file"], row.get("declared_kbps")) for row in rows]
        if signature == self.history_rows:
            return
        self.history_rows = signature
        self.tree.delete(*self.tree.get_children())
        for row in rows[:6]:
            bits = f" · {row['bits_per_sample']}bit" if row.get("bits_per_sample") else ""
            rate = row.get("sample_rate")
            detail = f"{rate / 1000:g} kHz" if rate else "—"
            detail += f" · {row.get('channels', '—')} 声道{bits}"
            self.tree.insert("", "end", values=(
                row["mtime"].strftime("%m-%d %H:%M") if row.get("mtime") else "—",
                row.get("tier", "—"),
                f"{row['declared_kbps']}k" if row.get("declared_kbps") else "—",
                f"{row['measured_kbps']}k" if row.get("measured_kbps") else "—",
                row.get("codec", "—"), detail))
        self.tree.configure(height=max(1, min(6, len(rows))))
        self.hist_hint.configure(text=f"{len(rows)} 个版本 · 按时长匹配" if rows else "暂无记录")
        if self.expanded and self.page == "quality":
            self.root.after_idle(self.fit_window)

    @staticmethod
    def state_fresh(state):
        if not state or not state.get("timestamp"):
            return False
        try:
            stamp = datetime.datetime.fromisoformat(state["timestamp"].replace("Z", "+00:00"))
            now = datetime.datetime.now(stamp.tzinfo)
            return -2 <= (now - stamp).total_seconds() <= 4
        except (ValueError, TypeError):
            return False

    def tick(self):
        now = time.monotonic()
        try:
            self.backend.ensure_alive()
            state = self.backend.read()
        except Exception as exc:
            state = None
            self.error = str(exc)
        if self.state_fresh(state):
            if self.samples.append(state, now):
                self.last_seen = now
            self.apply(state)
        elif self.last_seen is None or now - self.last_seen > 3:
            self.samples.gap(now)
            self.apply({"status": "None"})
            self.status_lbl.configure(text="●  等待连接", fg=MUTED)
            self.footer_lbl.configure(text="正在连接采集器…", fg=MUTED)
        self.update_stats()
        if self.pinned and now - self.last_lift > 3:
            self.last_lift = now
            self.root.lift()
            self.root.attributes("-topmost", True)
        if now - self.last_stats_scan > 60:
            self.last_stats_scan = now
            self.scan_stats_async()
        self.enforce_geometry()
        self.draw_progress()
        self.draw_charts()
        self.root.after(400, self.tick)

    def apply(self, state):
        self.state = state
        title, artist = state.get("title") or "", state.get("artist") or ""
        duration, status = state.get("duration") or 0, state.get("status") or "None"
        key = title, artist, duration
        changed = key != self.track_key
        if changed:
            self.track_key = key
            self.tracker.reset(duration or None, state.get("position") or 0)
            self.refresh_quality(title, duration)
        else:
            self.retry_match_if_needed(state)
        self.tracker.update(status, state.get("position"), duration)
        self._clip_song()
        self.update_cover(changed)
        for action, field in (("previous","can_prev"),("play_pause","can_play_pause"),("next","can_next")):
            self.transport[action].update_state(state.get(field),state.get("playing",False))
        self.progress.configure(cursor="hand2" if state.get("can_seek") else "arrow")
        self.status_lbl.configure(text="●  正在播放" if state.get("playing") else
                                  ("●  已暂停" if title else "●  等待播放"),
                                  fg=ACCENT if state.get("playing") else MUTED)
        q = self.quality
        if q:
            color = TIER_COLORS.get(q.get("tier"), ACCENT)
            self.bitrate_lbl.configure(text=str(q.get("declared_kbps") or q.get("measured_kbps") or "—"), fg=color)
            self.metric_labels["rate"].configure(text=f"{q['sample_rate'] / 1000:g}" if q.get("sample_rate") else "—", fg=FG)
            self.metric_labels["codec"].configure(text=q.get("codec", "—"), fg=FG)
            guess = " · 推测" if q.get("matched_by") not in ("lock", "single") else ""
            self.tier_lbl.configure(text=f"{q.get('tier', '—')} · {q.get('channels', '—')} 声道{guess}", fg=color)
            self.detail_source.configure(text=ac.match_reason(q))
            self.footer_lbl.configure(text=f"{q.get('size_mb', '—')} MB 缓存 · 实测 {q.get('measured_kbps', '—')} kbps", fg=MUTED)
        else:
            for label in self.metric_labels.values():
                label.configure(text="—", fg=MUTED)
            self.tier_lbl.configure(text="匹配音质中…" if title else "等待音质信息", fg=MUTED)
            self.detail_source.configure(text="等待缓存匹配")
            self.footer_lbl.configure(text="正在建立缓存索引…" if title and not ac.index_ready() else
                                      ("尚未找到对应缓存" if title else "监测已就绪"), fg=MUTED)
        self.update_params(q)
        if self.expanded and self.page == "quality":
            self.update_history(duration)
        self.update_proc(state)
        result = state.get("control_result") or {}
        if result.get("id") and result["id"] != self.last_control_id:
            self.last_control_id = result["id"]
            if not result.get("success"):
                self.control_notice_until = time.monotonic() + 3
        if time.monotonic() < self.control_notice_until:
            self.footer_lbl.configure(text="播放器暂未响应，请重试", fg=WARN)
        if self.error:
            self.footer_lbl.configure(text="部分数据暂不可用", fg=WARN)

    def update_params(self, q):
        if not q:
            for label in self.param_labels.values():
                label.configure(text="—", fg=MUTED)
            self.cache_lbl.configure(text="等待缓存匹配")
            return
        def put(key, value, color=FG):
            self.param_labels[key].configure(text=value, fg=color)
        put("codec", q.get("codec", "—"))
        put("declared", f"{q['declared_kbps']} kbps" if q.get("declared_kbps") else "—")
        put("measured", f"{q['measured_kbps']} kbps" if q.get("measured_kbps") else "—")
        put("rate", f"{q['sample_rate']:,} Hz" if q.get("sample_rate") else "—")
        channels = q.get("channels")
        put("channels", "立体声 / 2" if channels == 2 else ("单声道 / 1" if channels == 1 else f"{channels} 声道" if channels else "—"))
        put("bits", f"{q['bits_per_sample']} bit" if q.get("bits_per_sample") else "不适用")
        put("lossless", "无损" if q.get("lossless") else "有损压缩", ACCENT if q.get("lossless") else FG)
        put("crypto", "cenc / AES-128" if q.get("encrypted") else "未加密")
        put("container", f"M4A / {q.get('container_codec', '?')}")
        put("tier", q.get("tier", "—"), TIER_COLORS.get(q.get("tier"), FG))
        stamp = q.get("mtime")
        try:
            accessed = datetime.datetime.fromtimestamp(os.path.getatime(q.get("path", ""))).strftime("%m-%d %H:%M")
        except OSError:
            accessed = "—"
        self.cache_lbl.configure(text=(f"{q.get('file', '—')}\n"
                                      f"{q.get('size_mb', '—')} MB  ·  {fmt_time(q.get('duration'))}  ·  "
                                      f"缓存于 {stamp:%m-%d %H:%M}  ·  {ac.match_reason(q)}\n"
                                      f"最后访问 {accessed}\n"
                                      f"{q.get('path', '')}") if stamp else q.get("file", "—"))

    def update_stats(self):
        tier = self.stats["tier"] if self.stats else None
        if self.distribution.stats is not tier:
            self.distribution.draw(tier)
        if tier:
            mb = tier["total_mb"]
            size = f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.0f} MB"
            self.stats_summary.configure(text=f"{tier['total_count']} 首音频  ·  {size}")
            directory = self.stats["dir"]
            self.library_details.configure(text=f"缓存库 {tier['total_count']} 首音频 · {size} · "
                                           f"目录共 {directory['count']} 个文件 · "
                                           f"占用 {directory['bytes'] / 1073741824:.2f} GB")

    def update_proc(self, state):
        proc = state.get("process") or {}
        if not proc or proc.get("stale"):
            self.proc_lbl.configure(text="进程数据暂不可用 · 采集到数据后自动显示")
        else:
            self.proc_lbl.configure(text=f"汽水音乐 · PID {proc.get('pid', '—')} · "
                                    f"{proc.get('count', '—')} 个进程 · "
                                    f"内存 {proc.get('mem_mb', '—')} MB · "
                                    f"累计 CPU 时间 {proc.get('cpu_seconds', '—')} s")
