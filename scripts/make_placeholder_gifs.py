#!/usr/bin/env python3
"""Generate placeholder animated GIFs for Gifs Against Humanity.

These stand in for real reaction GIFs so the game is playable today. Every card is
visually distinct (different animation, palette and caption) so you can actually tell
your hand apart while testing.

    python scripts/make_placeholder_gifs.py            # 80 gifs
    python scripts/make_placeholder_gifs.py --count 200 --clean

To use real GIFs instead: drop .gif files into app/static/gifs/ and run
scripts/scan_gifs.py to rebuild the manifest.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
GIF_DIR = ROOT / "app" / "static" / "gifs"

W, H = 320, 240
FRAMES = 12
FRAME_MS = 80

# (background, primary, secondary, ink)
PALETTES = [
    ("#1B1035", "#FF2E88", "#22E6F2", "#FFF8E7"),
    ("#FFF3D6", "#FF4B3E", "#7B4BFF", "#1E1B18"),
    ("#0F3B47", "#FFC14D", "#FF6B5B", "#FFFDF5"),
    ("#2B1B4A", "#B6FF3C", "#FFD23F", "#FFFFFF"),
    ("#FDE2E4", "#3D5AFE", "#FF9505", "#221F1F"),
    ("#08313A", "#64E3B0", "#FF5C8A", "#F4FFFD"),
    ("#301B3F", "#FF9F1C", "#2EC4B6", "#FFFFFF"),
    ("#F7F3E8", "#12B5A5", "#E63946", "#14110F"),
    ("#101820", "#FEE715", "#FF3864", "#F7F7F7"),
    ("#3A0CA3", "#F72585", "#4CC9F0", "#FFFFFF"),
]

CAPTIONS = [
    "NOPE", "OOF", "YIKES", "BRUH", "SHOOK", "WOW", "HUH?", "SEND IT",
    "PANIC", "SMUG", "CHAOS", "SLOW CLAP", "CRINGE", "BIG MOOD", "SPRINT",
    "MELTDOWN", "SPARKLE", "DEAD", "SHRUG", "OH NO", "WHEEZE", "FLEX",
    "SWEAT", "BLINK", "SCREAM", "VIBING", "TRIUMPH", "REGRET", "SUSPICIOUS",
    "GLITCH", "SOGGY", "MAJESTIC", "GREMLIN", "CONFUSED", "FEAST", "TWIRL",
    "STOMP", "WOBBLE", "ZOOM", "FIZZLE",
]

STYLES = [
    "bounce", "spin", "pulse", "stripes", "zoom",
    "shake", "rings", "star", "checker", "confetti",
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def _centered(draw: ImageDraw.ImageDraw, xy, text, font, fill, outline=None):
    x, y = xy
    if outline:
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2)):
            draw.text((x + dx, y + dy), text, font=font, fill=outline, anchor="mm")
    draw.text((x, y), text, font=font, fill=fill, anchor="mm")


def _draw_frame(style: str, t: float, palette, caption: str, index: int, rng: random.Random) -> Image.Image:
    bg, c1, c2, ink = palette
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    cx, cy = W / 2, H / 2 - 12
    tau = t * 2 * math.pi

    if style == "bounce":
        bx = W * (0.15 + 0.7 * t)
        by = cy + 55 - abs(math.sin(tau)) * 90
        d.ellipse((0, H - 28, W, H + 40), fill=c2)
        d.ellipse((bx - 26, by - 26, bx + 26, by + 26), fill=c1, outline=ink, width=4)

    elif style == "spin":
        size = 62
        pts = []
        for k in range(4):
            a = tau + k * math.pi / 2
            pts.append((cx + math.cos(a) * size, cy + math.sin(a) * size))
        d.polygon(pts, fill=c1, outline=ink)
        d.ellipse((cx - 12, cy - 12, cx + 12, cy + 12), fill=c2)

    elif style == "pulse":
        r = 40 + 34 * (0.5 + 0.5 * math.sin(tau))
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=c1, outline=ink, width=5)
        d.ellipse((cx - r / 2, cy - r / 2, cx + r / 2, cy + r / 2), fill=c2)

    elif style == "stripes":
        band = 34
        offset = int(t * band * 2)
        for i in range(-2, W // band + 3):
            x0 = i * band * 2 + offset
            d.polygon([(x0, 0), (x0 + band, 0), (x0 + band - 60, H), (x0 - 60, H)], fill=c1)
        d.rectangle((0, cy - 34, W, cy + 34), fill=c2)

    elif style == "zoom":
        scale = 0.55 + 0.75 * (0.5 + 0.5 * math.sin(tau))
        f = _font(int(58 * scale))
        d.ellipse((cx - 100, cy - 70, cx + 100, cy + 70), fill=c2)
        _centered(d, (cx, cy), caption, f, c1, outline=ink)
        return img

    elif style == "shake":
        jx = math.sin(tau * 3) * 14
        jy = math.cos(tau * 2) * 9
        d.rounded_rectangle((cx - 84 + jx, cy - 56 + jy, cx + 84 + jx, cy + 56 + jy), 18, fill=c1, outline=ink, width=5)
        for k, ex in ((0, -32), (1, 32)):
            d.ellipse((cx + ex - 16 + jx, cy - 24 + jy, cx + ex + 16 + jx, cy + 8 + jy), fill=ink if k else ink)
        d.arc((cx - 40 + jx, cy + 4 + jy, cx + 40 + jx, cy + 48 + jy), 200, 340, fill=c2, width=6)

    elif style == "rings":
        for k in range(4):
            r = ((t + k / 4) % 1.0) * 110
            d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=c1 if k % 2 else c2, width=7)

    elif style == "star":
        pts = []
        spin = tau / 5
        for k in range(10):
            a = spin + k * math.pi / 5
            r = 78 if k % 2 == 0 else 34
            r += math.sin(tau) * 8
            pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
        d.polygon(pts, fill=c1, outline=ink)

    elif style == "checker":
        sq = 40
        off = int(t * sq)
        for row in range(-1, H // sq + 2):
            for col in range(-1, W // sq + 2):
                if (row + col) % 2 == 0:
                    x0 = col * sq + off
                    y0 = row * sq + off
                    d.rectangle((x0, y0, x0 + sq, y0 + sq), fill=c1)
        d.rounded_rectangle((28, cy - 40, W - 28, cy + 40), 14, fill=c2, outline=ink, width=4)

    else:  # confetti
        for k in range(26):
            a = rng.random()
            x = (a * W + t * 60 * (1 if k % 2 else -1)) % W
            y = ((rng.random() + t) % 1.0) * H
            s = 8 + (k % 3) * 5
            col = c1 if k % 2 else c2
            d.rectangle((x, y, x + s, y + s * 0.6), fill=col)

    f = _font(40)
    _centered(d, (cx, H - 34), caption, f, ink, outline=bg)
    small = _font(16)
    d.text((8, 6), f"#{index:03d}", font=small, fill=ink)
    return img


def build(count: int, clean: bool) -> dict:
    GIF_DIR.mkdir(parents=True, exist_ok=True)
    if clean:
        for old in GIF_DIR.glob("gif_*.gif"):
            old.unlink()

    entries = []
    for i in range(1, count + 1):
        rng = random.Random(i * 7919)
        style = STYLES[(i - 1) % len(STYLES)]
        palette = PALETTES[((i - 1) // len(STYLES)) % len(PALETTES)]
        caption = CAPTIONS[(i - 1) % len(CAPTIONS)]

        frames = [
            _draw_frame(style, k / FRAMES, palette, caption, i, random.Random(rng.random()))
            for k in range(FRAMES)
        ]
        gif_id = f"gif_{i:03d}"
        path = GIF_DIR / f"{gif_id}.gif"
        frames[0].save(
            path,
            save_all=True,
            append_images=frames[1:],
            duration=FRAME_MS,
            loop=0,
            optimize=True,
        )
        entries.append({"id": gif_id, "file": f"{gif_id}.gif", "label": f"{caption} #{i:03d}"})
        if i % 20 == 0:
            print(f"  ... {i}/{count}")

    manifest = {"generated": True, "count": len(entries), "gifs": entries}
    (GIF_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=80)
    ap.add_argument("--clean", action="store_true", help="delete existing gif_*.gif first")
    a = ap.parse_args()
    m = build(a.count, a.clean)
    print(f"wrote {m['count']} gifs + manifest.json to {GIF_DIR}")
