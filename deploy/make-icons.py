#!/usr/bin/env python3
"""Generate the PWA icons in ../icons from scratch — no image libraries
needed, just the standard library. The design mirrors the app: a
wheel-of-fortune circle in the segment colours from app.js on the site's
purple gradient. Run it after tweaking colours or sizes:

    python3 deploy/make-icons.py
"""
import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).parent.parent / "icons"

# SEGMENT_COLORS from app.js (first eight)
SEGMENTS = ["#ff5e7e", "#ffb84d", "#4dabff", "#6ee7a8",
            "#c084fc", "#f97362", "#38d0e0", "#facc15"]
BG_TOP = "#1b1035"    # --bg-1
BG_BOTTOM = "#3b1d5e"  # --bg-2
HUB = "#f5f0ff"       # --text


def rgb(hexcolor):
    h = hexcolor.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def wheel_sample(x, y, size, radius_frac, transparent_bg):
    """Colour (r, g, b, a) of one sample point. Coordinates are floats in
    pixel space; the wheel is centred with radius radius_frac * size."""
    cx = cy = size / 2
    dx, dy = x - cx, y - cy
    dist = math.hypot(dx, dy)
    radius = size * radius_frac
    hub = radius * 0.22

    if transparent_bg:
        # Badge: a white wheel silhouette (Android renders it as a mask).
        if dist > radius:
            return (0, 0, 0, 0)
        if dist < hub:
            return (0, 0, 0, 0)
        # transparent seams between the eight wedges keep it readable tiny
        angle = math.atan2(dy, dx) % (math.pi / 4)
        seam = min(angle, math.pi / 4 - angle) * dist
        if seam < size * 0.018:
            return (0, 0, 0, 0)
        return (255, 255, 255, 255)

    if dist <= radius:
        if dist < hub:
            base = rgb(HUB)
        elif dist > radius * 0.96:
            base = rgb(HUB)  # thin rim
        else:
            angle = (math.atan2(dy, dx) + math.pi / 2) % (2 * math.pi)
            base = rgb(SEGMENTS[int(angle / (2 * math.pi) * 8) % 8])
    else:
        base = lerp(rgb(BG_TOP), rgb(BG_BOTTOM), (x + y) / (2 * size))
        # pointer: a small hub-coloured triangle dipping into the wheel top
        px, py = dx / size, (y - (cy - radius)) / size
        if -0.06 < py < 0.0 and abs(px) < (0.0 - py) * 0.9 + 0.012:
            base = rgb(HUB)
    return (*base, 255)


def render(size, radius_frac, transparent_bg=False, oversample=3):
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(oversample):
                for sx in range(oversample):
                    px = x + (sx + 0.5) / oversample
                    py = y + (sy + 0.5) / oversample
                    c = wheel_sample(px, py, size, radius_frac, transparent_bg)
                    for i in range(4):
                        acc[i] += c[i]
            n = oversample * oversample
            row.extend(v // n for v in acc)
        rows.append(bytes(row))
    return rows


def write_png(path, size, rows):
    raw = b"".join(b"\x00" + r for r in rows)  # filter type 0 per scanline

    def chunk(tag, data):
        block = tag + data
        return struct.pack(">I", len(data)) + block + struct.pack(">I", zlib.crc32(block))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    print(f"wrote {path}")


def main():
    OUT.mkdir(exist_ok=True)
    write_png(OUT / "icon-192.png", 192, render(192, 0.42))
    write_png(OUT / "icon-512.png", 512, render(512, 0.42))
    # maskable: the wheel stays inside the 40%-radius safe zone so
    # circular launcher masks don't clip it
    write_png(OUT / "icon-maskable-512.png", 512, render(512, 0.34))
    # iOS home-screen icon (opaque background — iOS blackens transparency)
    write_png(OUT / "apple-touch-icon.png", 180, render(180, 0.42))
    # Android status-bar badge: white-on-transparent silhouette
    write_png(OUT / "badge-96.png", 96, render(96, 0.46, transparent_bg=True))


if __name__ == "__main__":
    main()
