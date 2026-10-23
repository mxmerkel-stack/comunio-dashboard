#!/usr/bin/env python3
"""Bettet data/dashboard.json in site/template.html ein und schreibt dist/index.html (noch unverschlüsselt)."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
DIST = Path(os.environ.get("DIST_DIR", ROOT / "dist"))


def main():
    data = json.loads((DATA / "dashboard.json").read_text())
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = (ROOT / "site" / "template.html").read_text().replace("__DATA__", payload, 1)
    DIST.mkdir(parents=True, exist_ok=True)
    (DIST / "index.html").write_text(html)
    print(f"ok: {DIST / 'index.html'}")


if __name__ == "__main__":
    main()
