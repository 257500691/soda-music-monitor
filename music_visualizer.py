"""Audio-driven graphics. All motion comes from live spectrum/PCM features."""
import math
import time
import tkinter as tk

from ui_widgets import ACCENT, CARD, FG, FONT, LINE, MUTED, rounded_rect


def mix_color(a, b, amount):
    amount = max(0, min(1, amount))
    a = [int(a[index:index + 2], 16) for index in (1, 3, 5)]
    b = [int(b[index:index + 2], 16) for index in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


class MotionState:
    """Time-based attack/release keeps animation smooth at different frame rates."""
    def __init__(self):
        self.bands = [0.0] * 48
        self.bass = self.mid = self.treble = self.beat = 0.0
        self.last_time = None

    def update(self, frame, now):
        delta = 1 / 30 if self.last_time is None else max(0, min(.5, now - self.last_time))
        self.last_time = now
        targets = frame["bands"] if frame else [0.0] * 48
        for index, target in enumerate(targets):
            current = self.bands[index]
            response = .045 if target > current else .16
            self.bands[index] += (target - current) * (1 - math.exp(-delta / response))
        for key in ("bass", "mid", "treble"):
            target = frame.get(key, 0) if frame else 0
            current = getattr(self, key)
            response = .05 if target > current else .18
            setattr(self, key, current + (target - current) * (1 - math.exp(-delta / response)))
        self.beat = max(frame.get("beat", 0) if frame else 0, self.beat * math.exp(-delta / .15))
        return delta


class MusicVisualizer(tk.Canvas):
    def __init__(self, parent, mini=False):
        super().__init__(parent, bg=parent.cget("bg"), height=76 if mini else 310,
                         highlightthickness=0, bd=0)
        self.mini = mini
        self.mode = "bars" if mini else "ring"
        self.motion, self.frame, self.phase = MotionState(), None, 0.0
        self.palette = [mix_color("#4ba9ed", "#a86be2", index / 47) for index in range(48)]
        self.bind("<Configure>", lambda event: self.paint())

    def set_theme(self, accent, background):
        self.palette = [mix_color(accent, "#ffffff", .12 + index / 47 * .48) for index in range(48)]
        self.configure(bg=background)
        self.paint()

    def render(self, frame, now=None):
        now = time.monotonic() if now is None else now
        self.frame = frame
        delta = self.motion.update(frame, now)
        if frame and frame.get("rms", 0) > .00001:
            self.phase += delta * (.045 + self.motion.bass * .055)
        if self.winfo_ismapped():
            self.paint()

    def select_mode(self, mode):
        self.mode = mode
        self.paint()

    def paint(self):
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width < 8:
            return
        if self.mode == "ring":
            self._ring(width, height)
        elif self.mode == "wave":
            self._wave(width, height)
        else:
            self._bars(width, height)
        if not self.mini:
            self._energies(width, height)

    def _ring(self, width, height):
        cx, cy = width / 2, height / 2 - 15
        bass, beat = self.motion.bass, self.motion.beat
        radius = min(height * .195, width * .17) + bass * 7 + beat * 7
        for layer in (3, 2, 1):
            halo = radius + 21 + layer * 8 + beat * 13
            color = mix_color(self.cget("bg"), ACCENT, (.20 + beat * .20) / layer)
            self.create_oval(cx - halo, cy - halo, cx + halo, cy + halo,
                             outline=color, width=7 if layer == 1 else 4)
        for index, value in enumerate(self.motion.bands):
            angle = index / 48 * math.tau - math.pi / 2 + self.phase
            start = radius + 12
            end = start + 3 + value ** .8 * (32 + beat * 8)
            cosine, sine = math.cos(angle), math.sin(angle)
            color = mix_color(LINE, self.palette[index], .30 + value * .70)
            self.create_line(cx + cosine * start, cy + sine * start,
                             cx + cosine * end, cy + sine * end,
                             fill=color, width=4, capstyle="round")
            if value > .35:
                point = end + 5 + beat * 5
                x, y = cx + cosine * point, cy + sine * point
                size = 1.3 + value * .6
                self.create_oval(x - size, y - size, x + size, y + size,
                                 fill=mix_color(self.cget("bg"), color, .55), outline="")
        self.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                         fill=mix_color(self.cget("bg"), ACCENT, .06), outline=LINE, width=1)
        inner = radius - 6
        self.create_oval(cx - inner, cy - inner, cx + inner, cy + inner,
                         fill=self.cget("bg"), outline=LINE)
        for index, value in enumerate((self.motion.bass, self.motion.mid, self.motion.treble)):
            x = cx + (index - 1) * 15
            length = 8 + value * 24 + beat * (5 if index == 0 else 2)
            self.create_line(x, cy - length / 2 - 10, x, cy + length / 2 - 10,
                             fill=self.palette[index * 20], width=6, capstyle="round")
        active = bool(self.frame and self.frame.get("rms", 0) > .00001)
        self.create_text(cx, cy + 23, text="音乐律动" if active else "等待声音",
                         fill=FG if active else MUTED, font=(FONT, 10, "bold"))

    def _bars(self, width, height):
        left, right = (1, width - 1) if self.mini else (20, width - 20)
        middle = height / 2 - (0 if self.mini else 14)
        self.create_line(left, middle, right, middle, fill=LINE)
        step = (right - left) / 48
        gap = 2 if self.mini else 4
        maximum = height * (.42 if self.mini else .32)
        for index, value in enumerate(self.motion.bands):
            x = left + index * step
            half = 1.5 + value ** .9 * maximum
            color = mix_color(LINE, self.palette[index], .25 + value * .75)
            rounded_rect(self, x, middle - half, x + max(1, step - gap), middle + half,
                         2 if self.mini else 4, fill=color, outline="")
        if not self.mini:
            self.create_text(left, height - 48, text="40 Hz", fill=MUTED, anchor="w", font=(FONT, 8))
            self.create_text(right, height - 48, text="16 kHz", fill=MUTED, anchor="e", font=(FONT, 8))

    def _wave(self, width, height):
        left, right, middle = 20, width - 20, height / 2 - 15
        self.create_line(left, middle, right, middle, fill=LINE, dash=(3, 5))
        values = self.frame["wave"] if self.frame else [0.0] * 128
        peak = self.frame.get("peak", 0) if self.frame else 0
        gain = min(20, .65 / max(.03, peak))
        coords = []
        for index, value in enumerate(values):
            coords.extend((left + index / 127 * (right - left), middle - value * gain * height * .35))
        self.create_line(*coords, fill=mix_color(self.cget("bg"), ACCENT, .18), width=8, joinstyle="round")
        self.create_line(*coords, fill=ACCENT, width=2, joinstyle="round")

    def _energies(self, width, height):
        group_width = min(130, width / 3 - 15)
        for index, (caption, value, color) in enumerate((("低频", self.motion.bass, self.palette[0]),
                                                        ("中频", self.motion.mid, self.palette[23]),
                                                        ("高频", self.motion.treble, self.palette[47]))):
            x = width / 2 + (index - 1) * (group_width + 18) - group_width / 2
            self.create_text(x, height - 23, text=caption, fill=MUTED, anchor="w", font=(FONT, 8))
            rounded_rect(self, x + 36, height - 26, x + group_width, height - 20,
                         3, fill=CARD if self.cget("bg") != CARD else LINE, outline="")
            if value > .01:
                rounded_rect(self, x + 36, height - 26, x + 36 + max(6, (group_width - 36) * value),
                             height - 20, 3, fill=color, outline="")
