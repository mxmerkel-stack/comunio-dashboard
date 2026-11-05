#!/usr/bin/env python3
"""Erzeugt App-Symbole und Manifest für „Zum Home-Bildschirm“ (enthält keine Daten)."""
import json
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DIST = Path(os.environ.get("DIST_DIR", Path(__file__).resolve().parent.parent / "dist"))


def icon(size):
    """App-Symbol: Spielfeld von oben mit 4-3-3-Aufstellung, ein Spieler (du) in Bernstein."""
    S = 4 * size                      # vierfach gezeichnet, dann verkleinert (glatte Kanten)
    k = S / 512
    img = Image.new("RGB", (S, S), "#1b4414")
    d = ImageDraw.Draw(img)
    for i in range(S):  # Verlauf von oben (hell) nach unten (dunkel), wie der Seitenkopf
        t = i / S
        if t < .55:
            u = t / .55
            col = tuple(int(a + (b - a) * u) for a, b in zip((0x2f, 0x6b, 0x24), (0x1b, 0x44, 0x14)))
        else:
            u = (t - .55) / .45
            col = tuple(int(a + (b - a) * u) for a, b in zip((0x1b, 0x44, 0x14), (0x0f, 0x2d, 0x0c)))
        d.line([(0, i), (S, i)], fill=col)
    ov = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    o = ImageDraw.Draw(ov)
    line = (234, 245, 226)
    w = lambda v: max(1, int(v * k))
    o.rounded_rectangle([50 * k, 62 * k, 462 * k, 450 * k], radius=10 * k, outline=line + (140,), width=w(7))
    o.line([(50 * k, 256 * k), (462 * k, 256 * k)], fill=line + (90,), width=w(5))
    o.rectangle([170 * k, 62 * k, 342 * k, 124 * k], outline=line + (90,), width=w(5))
    o.rectangle([170 * k, 388 * k, 342 * k, 450 * k], outline=line + (90,), width=w(5))
    for x, y in [(256, 420), (90, 335), (184, 345), (328, 345), (422, 335), (150, 245), (256, 255), (362, 245),
                 (112, 150), (256, 128), (400, 150)]:
        o.ellipse([(x - 17) * k, (y - 17) * k, (x + 17) * k, (y + 17) * k], fill=line + (255,))
    o.ellipse([(256 - 26) * k, (255 - 26) * k, (256 + 26) * k, (255 + 26) * k], fill=(0xc8, 0x88, 0x2a, 255))
    img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    return img.resize((size, size), Image.LANCZOS)


def main():
    DIST.mkdir(parents=True, exist_ok=True)
    for name, s in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
        icon(s).save(DIST / name)
    (DIST / "manifest.webmanifest").write_text(json.dumps({
        "name": "ComDash", "short_name": "ComDash", "start_url": "./", "scope": "./", "display": "standalone",
        "background_color": "#1b4414", "theme_color": "#1b4414", "lang": "de",
        "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"}]}))
    print("ok: Symbole und Manifest")


if __name__ == "__main__":
    main()
