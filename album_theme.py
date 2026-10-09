"""Album-derived matte palettes, with readable text in every color family."""
import colorsys
from music_visualizer import mix_color


def valid_hex(value):
    if isinstance(value, dict):
        value = value.get("rgb")
    if not isinstance(value, str):
        return None
    value = value.removeprefix("#")
    try:
        int(value, 16)
        return "#" + value.lower() if len(value) == 6 else None
    except ValueError:
        return None


def luminance(color):
    values = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    values = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
    return sum(v * weight for v, weight in zip(values, (.2126, .7152, .0722)))


def extract_color(image):
    palette = image.resize((64, 64)).quantize(colors=8).convert("RGB").getcolors(4096)
    # Favor colors occupying the artwork, rather than a tiny saturated logo.
    count, color = max(palette, key=lambda item: item[0])
    h, s, v = colorsys.rgb_to_hsv(*(channel / 255 for channel in color))
    rgb = colorsys.hsv_to_rgb(h, min(.38, s * .65), max(.24, min(.44, v * .60)))
    return "#" + "".join(f"{round(channel * 255):02x}" for channel in rgb)


def palette_for(image, metadata=None):
    metadata = metadata or {}
    base = valid_hex(metadata.get("background_color")) or extract_color(image)
    base = mix_color(base, "#202024", .20)
    while luminance(base) > .10:
        base = mix_color(base, "#000000", .08)
    card2 = mix_color(base, "#ffffff", .10)
    muted = mix_color(base, "#ffffff", .72)
    while (luminance(muted) + .05) / (luminance(card2) + .05) < 4.5:
        muted = mix_color(muted, "#ffffff", .12)
    accent = valid_hex(metadata.get("playing_wave_color")) or mix_color(base, "#ffffff", .74)
    while (luminance(accent) + .05) / (luminance(card2) + .05) < 4.5:
        accent = mix_color(accent, "#ffffff", .15)
    return {"BG": base, "CARD": mix_color(base, "#ffffff", .045),
            "CARD2": card2,
            "LINE": mix_color(base, "#ffffff", .16),
            "FG": "#f5f5f7", "MUTED": muted,
            "ACCENT": accent}
