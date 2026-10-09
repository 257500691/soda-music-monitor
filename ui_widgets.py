# -*- coding: utf-8 -*-
"""Small, dependency-free drawing widgets for the music dashboard."""
from collections import deque
import math
import tkinter as tk
from tkinter import font as tkfont

BG = "#505054"
CARD = "#58585c"
CARD2 = "#626266"
LINE = "#717175"
FG = "#f5f5f7"
MUTED = "#ceced1"
ACCENT = "#dedee2"
OK = "#80d7c4"
WARN = "#ecd090"
ERR = "#f1a4b6"
FONT = "Microsoft YaHei UI"
NUMBER_FONT = "Segoe UI"
TIER_COLORS = {"无损": "#dcc9fb", "极高": WARN, "较高": "#a3dfd9",
               "标准": "#b3d4f1", "流畅": "#c4cedc", "未知": "#c5c5cd"}


def rounded_rect(canvas, x1, y1, x2, y2, radius=14, **kwargs):
    radius = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    points = [x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
              x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
              x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


class RoundedCard(tk.Canvas):
    def __init__(self, parent, color=CARD, padding=14, **kwargs):
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0,
                         bd=0, **kwargs)
        self.color, self.padding = color, padding
        self.body = tk.Frame(self, bg=color)
        self.window = self.create_window(padding, padding, window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._resize)
        self.bind("<Configure>", self._resize)

    def _resize(self, event=None):
        width = max(1, self.winfo_width())
        self.itemconfigure(self.window, width=max(1, width - self.padding * 2))
        height = self.body.winfo_reqheight() + self.padding * 2
        if int(float(self.cget("height"))) != height:
            self.configure(height=height)
        self.delete("card")
        rounded_rect(self, 1, 2, width - 1, height - 1, radius=20,
                     fill=self.color, outline=LINE, width=1, tags="card")
        self.tag_lower("card")


class ActionButton(tk.Label):
    def __init__(self, parent, text, command, active=False, **kwargs):
        self.selected = active
        self.normal_bg = parent.cget("bg")
        super().__init__(parent, text=text, bg=self.normal_bg,
                         fg=ACCENT if active else MUTED, cursor="hand2",
                         font=(FONT, 9), padx=8, pady=5, takefocus=True, **kwargs)
        self.bind("<Button-1>", lambda event: command())
        self.bind("<Return>", lambda event: command())
        self.bind("<space>", lambda event: command())
        self.bind("<Enter>", lambda event: self.configure(bg=CARD2, fg=FG))
        self.bind("<Leave>", lambda event: self.set_active(self.selected))
        self.bind("<FocusIn>", lambda event: self.configure(bg=CARD2))
        self.bind("<FocusOut>", lambda event: self.set_active(self.selected))

    def set_active(self, active):
        self.selected = active
        self.configure(bg=self.normal_bg, fg=ACCENT if active else MUTED)


class Segments(tk.Canvas):
    def __init__(self, parent, choices, value, command, bg=CARD2):
        self.value, self.command, self.choices = value, command, list(choices)
        measure = tkfont.Font(family=FONT, size=8)
        self.widths = [measure.measure(title) + 25 for _, title in self.choices]
        super().__init__(parent, bg=parent.cget("bg"), width=sum(self.widths) + 6,
                         height=32, highlightthickness=0, cursor="hand2", takefocus=True)
        self.surface = bg
        self.bind("<Button-1>", self._click)
        self.bind("<Left>", lambda event: self._step(-1))
        self.bind("<Right>", lambda event: self._step(1))
        self.bind("<Return>", lambda event: self.select(self.value))
        self.bind("<space>", lambda event: self.select(self.value))
        self.paint()

    def paint(self):
        self.delete("all")
        rounded_rect(self, 0, 1, sum(self.widths) + 6, 31, 15,
                     fill=self.surface, outline=LINE)
        x = 3
        for (key, title), width in zip(self.choices, self.widths):
            if key == self.value:
                rounded_rect(self, x, 3, x + width, 29, 12, fill=LINE, outline="")
            self.create_text(x + width / 2, 16, text=title, fill=ACCENT if key == self.value else MUTED,
                             font=(FONT, 8, "bold" if key == self.value else "normal"))
            x += width

    def _click(self, event):
        self.focus_set()
        x = 3
        for (key, title), width in zip(self.choices, self.widths):
            if x <= event.x < x + width:
                self.select(key)
                return
            x += width

    def _step(self, direction):
        keys = [key for key, _ in self.choices]
        self.select(keys[(keys.index(self.value) + direction) % len(keys)])

    def select(self, value):
        self.value = value
        self.paint()
        self.command(value)


class TransportButton(tk.Canvas):
    def __init__(self, parent, kind, command, primary=False):
        self.kind, self.command, self.primary = kind, command, primary
        self.enabled, self.playing = False, False
        self.size = 46 if primary else 36
        super().__init__(parent, width=self.size, height=self.size, bg=parent.cget("bg"),
                         highlightthickness=0, takefocus=True, cursor="hand2")
        self.bind("<Button-1>", self._click)
        self.bind("<Return>", self._click)
        self.bind("<space>", self._click)
        self.bind("<FocusIn>", lambda event: self.paint())
        self.bind("<FocusOut>", lambda event: self.paint())
        self.paint()

    def _click(self, event):
        if self.enabled:
            self.command()

    def update_state(self, enabled, playing=False):
        self.enabled, self.playing = bool(enabled), playing
        self.configure(cursor="hand2" if enabled else "arrow")
        self.paint()

    def paint(self):
        self.delete("all")
        s, c = self.size, self.size / 2
        color = BG if self.primary else FG
        if self.primary:
            self.create_oval(1, 1, s-1, s-1, fill=FG if self.enabled else MUTED,
                             outline=ACCENT if self.focus_get() == self else "", width=2)
        elif not self.enabled:
            color = LINE
        if self.kind == "play_pause" and self.playing:
            for x in (c-6, c+4):
                self.create_line(x, c-7, x, c+7, fill=color, width=4, capstyle="round")
        elif self.kind == "previous":
            self.create_polygon(c+6,c-7,c-4,c,c+6,c+7,fill=color)
            self.create_line(c-6,c-7,c-6,c+7,fill=color,width=3,capstyle="round")
        else:
            self.create_polygon(c-4,c-7,c+7,c,c-4,c+7,fill=color)
            if self.kind == "next":
                self.create_line(c+9,c-7,c+9,c+7,fill=color,width=3,capstyle="round")


def valid_number(value):
    """Unavailable and invalid sensor values remain gaps, rather than zeroes."""
    try:
        number = float(value) if value is not None else None
        return number if number is not None and math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError):
        return None


class SampleHistory:
    def __init__(self):
        self.samples = deque(maxlen=640)
        self.last_token = None

    def append(self, state, now):
        token = state.get("timestamp")
        if token and token == self.last_token:
            return False
        self.last_token = token
        peak = valid_number(state.get("audio_peak"))
        proc = state.get("process") or {}
        self.samples.append({"time": now,
                             "audio": min(1.0, peak) * 100 if peak is not None else None,
                             "io": valid_number(state.get("io_rate_kbps")),
                             "memory": None if proc.get("stale") else valid_number(proc.get("mem_mb"))})
        self._trim(now)
        return True

    def gap(self, now):
        self.samples.append({"time": now, "audio": None, "io": None, "memory": None})
        self._trim(now)

    def _trim(self, now):
        while self.samples and self.samples[0]["time"] < now - 125:
            self.samples.popleft()

    def points(self, metric, seconds, now):
        return [(row["time"], row[metric]) for row in self.samples
                if now - seconds <= row["time"] <= now]


METRICS = {"audio": ("音频电平", "%", ACCENT),
           "io": ("读写速率", "KB/s", "#b3d4f1"),
           "memory": ("内存占用", "MB", "#dcc9fb")}


def nice_ceiling(value):
    if value <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(value))
    scaled = value / magnitude
    step = next((step for step in (1, 2, 2.5, 5, 10) if scaled <= step), 10)
    return step * magnitude


def axis_number(value):
    if value >= 1000:
        return f"{value / 1000:g}k"
    return f"{value:g}"


class TrendChart(tk.Canvas):
    def __init__(self, parent, history, mini=False, **kwargs):
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0,
                         bd=0, height=54 if mini else 186, **kwargs)
        self.history, self.mini = history, mini
        self.metric, self.seconds = "audio", 60
        self.now, self.plot = 0, None
        self.hover_xy = None
        self.bind("<Configure>", lambda event: self.draw(self.now))
        if not mini:
            self.bind("<Motion>", self._hover)
            self.bind("<Leave>", self._leave)

    def draw(self, now):
        self.now = now
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width < 8:
            return
        points = self.history.points(self.metric, self.seconds, now)
        values = [value for _, value in points if value is not None]
        name, unit, color = METRICS[self.metric]
        ceiling = (min(100, nice_ceiling(max(10, max(values, default=0) * 1.18)))
                   if self.metric == "audio" else nice_ceiling(max(values, default=1) * 1.12))
        left, right = (2, width - 3) if self.mini else (45, width - 16)
        top, bottom = (4, height - 4) if self.mini else (15, height - 27)
        self.plot = (left, right, top, bottom, ceiling)
        for i in range(5 if not self.mini else 2):
            fraction = i / (4 if not self.mini else 1)
            y = bottom - (bottom - top) * fraction
            self.create_line(left, y, right, y, fill=LINE, dash=(2, 5))
            if not self.mini:
                self.create_text(left - 10, y, text=axis_number(ceiling * fraction),
                                 anchor="e", fill=MUTED, font=(NUMBER_FONT, 8))
        if not self.mini:
            for fraction, text in ((0, f"−{self.seconds}s"), (.5, f"−{self.seconds // 2}s"), (1, "现在")):
                x = left + (right - left) * fraction
                self.create_text(x, height - 9, text=text, fill=MUTED, font=(FONT, 8))
                self.create_line(x, top, x, bottom, fill=LINE, dash=(2, 6))
        segments, segment, previous = [], [], None
        for stamp, value in points:
            if value is None or (previous is not None and stamp - previous > 3.0):
                if segment:
                    segments.append(segment)
                segment = []
            if value is not None:
                x = left + (stamp - (now - self.seconds)) / self.seconds * (right - left)
                y = bottom - min(value / ceiling, 1) * (bottom - top)
                segment.extend((x, y))
            previous = stamp
        if segment:
            segments.append(segment)
        fill = CARD2
        for segment in segments:
            if len(segment) >= 4:
                self.create_polygon(segment[0], bottom, *segment, segment[-2], bottom,
                                    fill=fill, outline="")
                self.create_line(*segment, fill=color, width=2, joinstyle="round")
        if points and points[-1][1] is not None and now - points[-1][0] <= 3:
            x = left + (points[-1][0] - now + self.seconds) / self.seconds * (right - left)
            y = bottom - min(points[-1][1] / ceiling, 1) * (bottom - top)
            self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline=self.cget("bg"))
        if not values:
            text = "等待音频输出" if self.metric == "audio" else "等待采集数据"
            self.create_text((left + right) / 2, (top + bottom) / 2,
                             text=text, fill=MUTED, font=(FONT, 9))
        if self.hover_xy:
            self._show_hover(*self.hover_xy)

    def _hover(self, event):
        self.hover_xy = event.x, event.y
        self._show_hover(event.x, event.y)

    def _leave(self, event):
        self.hover_xy = None
        self.delete("hover")

    def _show_hover(self, mouse_x, mouse_y):
        self.delete("hover")
        if not self.plot:
            return
        left, right, top, bottom, ceiling = self.plot
        if not left <= mouse_x <= right or not top <= mouse_y <= bottom:
            return
        target = self.now - self.seconds + (mouse_x - left) / (right - left) * self.seconds
        points = self.history.points(self.metric, self.seconds, self.now)
        if not points:
            return
        stamp, value = min(points, key=lambda point: abs(point[0] - target))
        if abs(stamp - target) > 3:
            return
        x = left + (stamp - self.now + self.seconds) / self.seconds * (right - left)
        name, unit, color = METRICS[self.metric]
        self.create_line(x, top, x, bottom, fill=MUTED, dash=(3, 3), tags="hover")
        label = f"{max(0, self.now - stamp):.0f} 秒前  ·  {value:.1f} {unit}" if value is not None else "此时数据不可用"
        if value is not None:
            y = bottom - min(value / ceiling, 1) * (bottom - top)
            self.create_oval(x - 4, y - 4, x + 4, y + 4, fill=color, outline=FG, tags="hover")
        label_x = max(left + 92, min(x, right - 92))
        rounded_rect(self, label_x - 92, top, label_x + 92, top + 27, 7,
                     fill=CARD2, outline=LINE, tags="hover")
        self.create_text(label_x, top + 13, text=label, fill=FG,
                         font=(FONT, 8), tags="hover")


class TierDistribution(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, bg=parent.cget("bg"), height=100, highlightthickness=0)
        self.stats = None
        self.bind("<Configure>", lambda event: self.draw(self.stats))

    def draw(self, stats):
        self.stats = stats
        self.delete("all")
        width = self.winfo_width()
        if width < 8:
            return
        if stats is None:
            self.create_text(width / 2, 48, text="正在读取缓存库…", fill=MUTED, font=(FONT, 9))
            return
        total = stats.get("total_count", 0)
        if not total:
            self.create_text(width / 2, 48, text="缓存库暂无音频", fill=MUTED, font=(FONT, 9))
            return
        tiers = [(tier, stats["by_tier"][tier]) for tier in TIER_COLORS if tier in stats["by_tier"]]
        x = 0
        for tier, row in tiers:
            span = width * row["count"] / total
            self.create_rectangle(x, 7, x + span, 20, fill=TIER_COLORS[tier], outline="")
            x += span
        for i, (tier, row) in enumerate(tiers):
            x, y = (i % 3) * (width / 3), 42 + (i // 3) * 27
            self.create_oval(x + 1, y - 3, x + 7, y + 3, fill=TIER_COLORS[tier], outline="")
            self.create_text(x + 14, y, anchor="w", text=f"{tier}  {row['count']} 首",
                             fill=FG, font=(FONT, 8))
            self.create_text(x + width / 3 - 15, y, anchor="e", text=f"{row['count'] / total:.0%}",
                             fill=MUTED, font=(NUMBER_FONT, 8))
