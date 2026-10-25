#!/usr/bin/env python3
"""Push-Nachrichten aufs iPhone über ntfy (https://ntfy.sh). Secret NTFY_TOPIC = dein geheimer Themenname.

  --mode lineup   Erinnerung vor dem Aufstellungsschluss: Warnungen und Tausch-Vorschläge
  --mode auctions Abend-Hinweis: welche empfohlenen Angebote in den nächsten ~12 Stunden enden
Doppelte Meldungen werden über data/notified.json verhindert. Keine Beträge in den Nachrichten.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alert import ntfy  # noqa: E402

DATA = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
SITE = "https://mxmerkel-stack.github.io/comunio-dashboard/"
FORCE = os.environ.get("FORCE", "").lower() == "true"


def f1(v):
    return f"{v:.1f}".replace(".", ",")


def load_sent():
    try:
        return json.loads((DATA / "notified.json").read_text())
    except Exception:
        return {}


def once(key, text):
    sent = load_sent()
    h = hashlib.sha1(text.encode()).hexdigest()[:12]
    if not FORCE and sent.get(key) == h:
        print(f"{key}: schon gemeldet.")
        return False
    sent[key] = h
    (DATA / "notified.json").write_text(json.dumps(sent))
    return True


def lineup(d):
    chk, nm = d.get("checks") or {}, d.get("nextMatch") or {}
    md = nm.get("md") or chk.get("md")
    warn = [i for i in chk.get("issues", []) if i["level"] == "warn"]
    swaps = [s for s in (nm.get("swaps") or []) if s.get("gain", 0) >= 1.0 and s.get("in")]
    if not warn and not swaps:
        print("Alles in Ordnung, keine Nachricht.")
        return
    lines = []
    for w in warn:
        t = w["title"]
        lines.append("• " + (t.split(":")[0] if t.startswith("Kontostand") else t))
    for s in swaps[:3]:
        lines.append(f"• {s.get('out') or 'freie Position'} → {s['in']} ({f1(s['gain'])} Pkt.)")
    text = "\n".join(lines)
    if not once(f"lineup-{md}", text):
        return
    title = f"Comunio {md}. Spieltag: " + (f"{len(warn)} Warnung{'en' if len(warn) != 1 else ''}" if warn else "Aufstellung verbessern")
    ok = ntfy(title, text, priority=5 if warn else 3, click=SITE + "#nextmatch", tags=["warning" if warn else "soccer"])
    print("Push gesendet." if ok else "Kein Push (NTFY_TOPIC fehlt oder Fehler).")


def auctions(d):
    now = datetime.now(timezone.utc)
    rows = []
    for m in d.get("market") or []:
        try:
            t = datetime.fromisoformat((m.get("endsAt") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        h = (t - now).total_seconds() / 3600
        if 0 < h <= 14 and m.get("recommendation") == "ja" and m.get("affordable"):
            rows.append((h, m))
    if not rows:
        print("Keine empfohlenen Auktionen in Sicht.")
        return
    rows.sort(key=lambda r: -(r[1].get("ePerMio") or 0))
    text = "\n".join(f"• {m['name']} ({m.get('club') or '–'}), noch {int(h)} Std." for h, m in rows[:5])
    if not once("auctions-" + now.strftime("%Y-%m-%d"), text):
        return
    ok = ntfy(f"{len(rows)} empfohlene Angebot{'e' if len(rows) != 1 else ''} laufen bald ab", text, click=SITE + "#uebersicht", tags=["hammer"])
    print("Push gesendet." if ok else "Kein Push (NTFY_TOPIC fehlt oder Fehler).")


def main():
    mode = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "lineup"
    d = json.loads((DATA / "dashboard.json").read_text())
    {"lineup": lineup, "auctions": auctions}[mode](d)


if __name__ == "__main__":
    main()
