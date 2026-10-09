"""Manage fast WASAPI feature capture independently of slow metadata polling."""
import json
import math
import os
import subprocess
import time


def decode_frame(payload, now=None):
    """Reject stale/corrupt frames instead of inventing a dancing animation."""
    now = time.time() if now is None else now
    try:
        age = now - float(payload["timestamp"]) / 1000
        if not math.isfinite(age) or not -.2 <= age <= 1:
            return None
        if not payload.get("ready"):
            return None
        bands, wave = payload["bands"], payload["wave"]
        if len(bands) != 48 or len(wave) != 128:
            return None
        clean = {"bands": [], "wave": []}
        for key, values, lower in (("bands", bands, 0), ("wave", wave, -1)):
            for value in values:
                value = float(value)
                if not math.isfinite(value):
                    return None
                clean[key].append(max(lower, min(1, value)))
        for key in ("bass", "mid", "treble", "beat", "rms", "peak"):
            value = float(payload.get(key, 0))
            if not math.isfinite(value) or value < 0:
                return None
            clean[key] = min(1, value)
        clean["sequence"] = payload.get("sequence")
        clean["sample_rate"] = int(payload.get("sample_rate", 0))
        clean["timestamp"] = float(payload["timestamp"]) / 1000
        return clean
    except (TypeError, ValueError, KeyError, OverflowError):
        return None


class ReactiveAudio:
    def __init__(self, powershell, state_dir):
        self.powershell = powershell
        self.path = os.path.join(state_dir, "spectrum.json")
        self.helper = os.path.join(os.path.dirname(__file__), "audio_reactive.ps1")
        self.proc, self.last_start = None, 0
        self.cached_frame = None

    def start(self):
        if os.name != "nt" or (self.proc and self.proc.poll() is None):
            return
        self.last_start = time.monotonic()
        command = [self.powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-WindowStyle", "Hidden", "-File", self.helper,
                   "-OutPath", self.path, "-ParentPid", str(os.getpid())]
        try:
            self.proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            self.proc = None

    def ensure_alive(self):
        if (not self.proc or self.proc.poll() is not None) and time.monotonic() - self.last_start >= 5:
            self.start()

    def read(self):
        try:
            with open(self.path, encoding="utf-8") as source:
                frame = decode_frame(json.load(source))
            self.cached_frame = frame
            return frame
        except (OSError, ValueError):
            # An atomic replacement can momentarily share-lock the JSON on Windows.
            if self.cached_frame and time.time() - self.cached_frame["timestamp"] <= .25:
                return self.cached_frame
            return None

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass
