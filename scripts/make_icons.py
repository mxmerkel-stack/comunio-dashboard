#!/usr/bin/env python3
"""Erzeugt App-Symbole und Manifest für „Zum Home-Bildschirm“ (enthält keine Daten)."""
import json
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DIST = Path(os.environ.get("DIST_DIR", Path(__file__).resolve().parent.parent / "dist"))


def icon(size):
    img = Image.new("RGB", (size, size), "#1b4414")
    d = ImageDraw.Draw(img)
    for i in range(size):  # Verlauf
        c = int(0x2a - 0x1a * i / size)
        d.line([(0, i), (size, i)], fill=(c, 0x5c - int(0x2c * i / size), int(0x20 - 0x12 * i / size)))
    m = size * 0.16
    d.ellipse([m, m, size - m, size - m], outline="#eaf5e2", width=max(2, size // 40))
    d.line([(size / 2, m), (size / 2, size - m)], fill="#eaf5e2", width=max(2, size // 60))
    d.ellipse([size * .38, size * .38, size * .62, size * .62], outline="#eaf5e2", width=max(2, size // 60))
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", int(size * .26))
    except OSError:
        font = ImageFont.load_default()
    d.text((size / 2, size * .5), "CZ", font=font, fill="#fff", anchor="mm", stroke_width=max(1, size // 120), stroke_fill="#10300c")
    return img


def main():
    DIST.mkdir(parents=True, exist_ok=True)
    for name, s in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
        icon(s).save(DIST / name)
    (DIST / "manifest.webmanifest").write_text(json.dumps({
        "name": "Comunio Zentrale", "short_name": "Comunio", "start_url": "./", "scope": "./", "display": "standalone",
        "background_color": "#1b4414", "theme_color": "#1b4414", "lang": "de",
        "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"}]}))
    print("ok: Symbole und Manifest")


if __name__ == "__main__":
    main()
