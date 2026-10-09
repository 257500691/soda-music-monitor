"""Read matching cover metadata from Soda's queue; fetch only public artwork."""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import threading
import time
import unicodedata
from urllib.parse import urlparse
from urllib.request import urlopen


def normalize(value):
    return unicodedata.normalize("NFKC", str(value or "")).casefold().replace("’", "'").strip()


def match_track(queues, title, artist, duration=0):
    if not isinstance(queues, dict):
        return None
    candidates = []
    for queue in queues.values():
        if not isinstance(queue, dict):
            continue
        for playable in queue.get("playables", []):
            if not isinstance(playable, dict):
                continue
            track = playable.get("track", playable)
            if normalize(track.get("name")) != normalize(title):
                continue
            artists = {normalize(a.get("name")) for a in track.get("artists", [])}
            if artist and normalize(artist) not in artists and normalize(artist) != normalize(" / ".join(a.get("name", "") for a in track.get("artists", []))):
                continue
            if duration and abs(track.get("duration", 0) / 1000 - duration) > 2:
                continue
            candidates.append((queue.get("savedAt", 0), playable))
    identities = {p.get("id", p.get("track", {}).get("id")) for _, p in candidates}
    if len(identities) != 1:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def cover_urls(info, size=720):
    if not isinstance(info, dict):
        return []
    urls = info.get("urls", [])
    uri = info.get("uri")
    if uri and not info.get("need_complete_url"):
        template = info.get("template_prefix")
        suffix = f"{template}-crop-center:{size}:{size}.jpg" if template else f"c5_{size}x{size}.jpg"
        urls = [base + uri + "~" + suffix for base in urls]
    # Artwork metadata never becomes an arbitrary network/file request.
    return [url for url in urls if isinstance(url,str) and urlparse(url).scheme == "https" and
            (urlparse(url).hostname or "").endswith(".douyinpic.com")]


class ArtworkSource:
    def __init__(self, directory, queue_path=None):
        self.directory = Path(directory) / "artwork"
        self.queue_path = Path(queue_path) if queue_path else Path(os.environ.get("APPDATA", "")) / "SodaMusic/LunaStorage/QueueCache"
        self.result, self.wanted = None, None
        self.busy, self.last_try, self.closed = False, 0, False
        self.tried_key = None

    def request(self, state):
        key = (state.get("title") or "", state.get("artist") or "")
        self.wanted = key
        if not key[0] or self.closed:
            return None
        current = self.result if self.result and self.result["key"] == key else None
        if not self.busy and (not current or not current.get("high_resolution")) and (key != self.tried_key or time.monotonic() - self.last_try > 4):
            self.busy, self.last_try = True, time.monotonic()
            self.tried_key = key
            threading.Thread(target=self._load, args=(dict(state), key), daemon=True).start()
        return current

    def _load(self, state, key):
        from PIL import Image
        result = None
        try:
            playable = None
            try:
                data = self.queue_path.read_bytes()
                if len(data) < 8 * 1024 * 1024:
                    if data.startswith(b"LUNA"):
                        data = gzip.decompress(data[4:])
                    playable = match_track(json.loads(data), *key, state.get("duration", 0))
            except (OSError, ValueError, EOFError):
                pass
            colors = playable.get("colors", {}) if playable else {}
            info = playable.get("cover_url", playable.get("track", {}).get("album", {}).get("url_cover", {})) if playable else {}
            urls = cover_urls(info)
            path, high_resolution = state.get("cover_path"), False
            # Show the system thumbnail and native colors while HD artwork loads.
            if path:
                try:
                    with Image.open(path) as image:
                        preview = {"key": key, "image": image.convert("RGB").copy(),
                                   "colors": colors, "path": path, "high_resolution": False}
                    if not self.closed and key == self.wanted:
                        self.result = preview
                except (OSError, ValueError):
                    pass
            if urls:
                self.directory.mkdir(parents=True, exist_ok=True)
                cached = self.directory / (hashlib.sha256(urls[0].encode()).hexdigest()[:24] + ".jpg")
                if not cached.exists():
                    for url in urls[:2]:
                        try:
                            with urlopen(url, timeout=4) as response:
                                data = response.read(5 * 1024 * 1024 + 1)
                            if len(data) > 5 * 1024 * 1024:
                                continue
                            with Image.open(io.BytesIO(data)) as image:
                                image.convert("RGB").save(str(cached) + ".tmp", format="JPEG", quality=95)
                            os.replace(str(cached) + ".tmp", cached)
                            break
                        except (OSError, ValueError):
                            continue
                if cached.exists():
                    path, high_resolution = str(cached), True
            if path:
                with Image.open(path) as image:
                    image = image.convert("RGB").copy()
                result = {"key": key, "image": image, "colors": colors,
                          "path": path, "high_resolution": high_resolution}
        except (OSError, ValueError):
            pass
        finally:
            if not self.closed and key == self.wanted and result:
                self.result = result
            self.busy = False

    def close(self):
        self.closed = True
