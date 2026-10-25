#!/usr/bin/env python3
"""Push-Nachrichten aufs iPhone über ntfy (https://ntfy.sh). Secret NTFY_TOPIC = dein geheimer Themenname.

  --mode eve      Abend vor dem Spieltag (20 Uhr): alle Warnungen und Tausch-Vorschläge, sonst "alles in Ordnung"
  --mode alarm    3 Std. vor Anpfiff: nur wenn das Konto im Minus ist oder eine Position unbesetzt/falsch besetzt ist
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


def eve(d):
    chk, nm = d.get("checks") or {}, d.get("nextMatch") or {}
    md = nm.get("md") or chk.get("md")
    warn = [i for i in chk.get("issues", []) if i["level"] == "warn"]
    swaps = [s for s in (nm.get("swaps") or []) if s.get("gain", 0) >= 1.0 and s.get("in")]
    lines = ["• " + (w["title"].split(":")[0] if w["title"].startswith("Kontostand") else w["title"]) for w in warn]
    for s in swaps[:3]:
        lines.append(f"• Tausch: {s.get('out') or 'freie Position'} → {s['in']} ({f1(s['gain'])} Pkt.)")
    if not lines:
        lines = ["✓ Keine Warnungen, Aufstellung und Konto in Ordnung."]
    text = "\n".join(lines)
    if not once(f"eve-{md}", text):
        return
    title = f"Comunio {md}. Spieltag morgen: " + (f"{len(warn)} Warnung{'en' if len(warn) != 1 else ''}" if warn else
                                                  "Aufstellung verbessern" if swaps else "alles in Ordnung")
    ok = ntfy(title, text, priority=4 if warn else 3, click=SITE + "#nextmatch", tags=["warning" if warn else "soccer"])
    print("Push gesendet." if ok else "Kein Push (NTFY_TOPIC fehlt oder Fehler).")


def alarm(d):
    chk, nm = d.get("checks") or {}, d.get("nextMatch") or {}
    md = nm.get("md") or chk.get("md")
    keys = ("Kontostand im Minus", "unbesetzte Position", "falschen Position", "Keine Aufstellung")
    hot = [i for i in chk.get("issues", []) if i["level"] == "warn" and any(k in i["title"] for k in keys)]
    if not hot:
        print("Kein Minus, keine leere Position – keine Nachricht.")
        return
    text = "\n".join("• " + (w["title"].split(":")[0] if w["title"].startswith("Kontostand") else w["title"]) for w in hot)
    text += "\nAufstellungsschluss ist in ca. 3 Stunden."
    if not once(f"alarm-{md}", text):
        return
    ok = ntfy(f"⚠️ Comunio {md}. Spieltag: jetzt handeln", text, priority=5, click=SITE + "#nextmatch", tags=["rotating_light"])
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
    mode = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "eve"
    d = json.loads((DATA / "dashboard.json").read_text())
    {"eve": eve, "alarm": alarm, "auctions": auctions}[mode](d)


if __name__ == "__main__":
    main()
