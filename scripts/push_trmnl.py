#!/usr/bin/env python3
"""Schickt Kennzahlen aus data/dashboard.json an einen TRMNL-Webhook (ePaper-Anzeige).

Braucht das Secret TRMNL_WEBHOOK_URL (URL des privaten TRMNL-Plugins mit Strategie "Webhook").
Ohne das Secret passiert nichts. Es werden nur wenige Kennzahlen gesendet, keine Zugangsdaten.
Warnhinweis: nur solange der Aufstellungsschluss des nächsten Spieltags noch nicht erreicht ist.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
MAX_BYTES = 1900  # TRMNL Free erlaubt 2 KB je Webhook-Aufruf


def mio(v, digits=2):
    return f"{v / 1e6:.{digits}f}".replace(".", ",")


def clean_name(name):
    """ePaper-Schriften haben keine Emojis: Namen ohne Buchstaben/Ziffern werden ersetzt."""
    name = (name or "").strip()
    known = {"\U0001F985": "Eagle"}  # Adler-Emoji eines Managers
    if name in known:
        return known[name]
    return name if re.search(r"\w", name) else "Manager"


def short(text, n=48):
    return text if len(text) <= n else text[: n - 1] + "…"


WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
BERLIN = ZoneInfo("Europe/Berlin")


def parse_ts(v):
    """ISO-Zeitpunkt (auch +0200 ohne Doppelpunkt) als aware datetime, sonst None."""
    if not v:
        return None
    v = str(v).replace("Z", "+00:00")
    v = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", v)
    try:
        t = datetime.fromisoformat(v)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=BERLIN)


def next_kickoff(d, raw):
    """Anstoß des ersten Spiels des nächsten noch nicht begonnenen Spieltags: (Spieltag, datetime)."""
    nm = d.get("nextMatch") or {}
    cal = sorted([c for c in (raw or {}).get("matchdayCalendar") or []
                  if str(c.get("key", "")).isdigit() and not c.get("started") and not c.get("finished")],
                 key=lambda c: int(c["key"]))
    if cal:
        md, ko = int(cal[0]["key"]), parse_ts(cal[0].get("date"))
    elif not nm.get("started"):
        md, ko = nm.get("md"), parse_ts(nm.get("date"))
    else:
        return None, None
    # Genauer: frühester Anstoß aus den Paarungen. Der Comunio-Kalender liefert für spätere
    # Spieltage nur einen Platzhalter, deshalb sonst den Spielplan bei OpenLigaDB abfragen.
    fixtures = None
    if md is not None and str(nm.get("md")) == str(md):
        fixtures = nm.get("fixtures")
    elif md is not None and cal:
        season = str(cal[0].get("season") or "")
        year = 2000 + int(season.split("/")[0]) if re.match(r"^\d{2}/\d{2}$", season) else (ko or datetime.now()).year
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import next_match as NM
            fixtures = NM.fetch_fixtures(year, md)
        except Exception as e:  # Netz/Import: dann bleibt der Kalenderwert
            print(f"TRMNL: Spielplan nicht abrufbar ({type(e).__name__})")
    kos = [parse_ts(f.get("kickoff")) for f in fixtures or []]
    kos = [k for k in kos if k]
    if kos:
        ko = min(kos)
    return md, ko


def names(lst, n=3):
    lst = [str(x) for x in (lst or [])]
    return ", ".join(lst[:n]) + (f" +{len(lst) - n}" if len(lst) > n else "")


def build_payload(d, raw=None):
    me = d["me"]
    standings = d["standings"]
    fc = {r["id"]: r for r in d.get("forecast") or []}
    me_fc = fc.get(me["id"], {})

    last_md = next((m for m in reversed(d.get("matchdays") or []) if m.get("best") is not None), None)
    last_pts = (last_md or {}).get("points", {})

    rows = []
    for s in standings:
        rows.append({"r": s["rank"], "m": clean_name(s["name"]), "p": int(s["points"]),
                     "l": last_pts.get(s["id"]), "tv": mio(s["teamValue"], 1), "me": s["id"] == me["id"]})

    status = d.get("status") or {}
    checks = d.get("checks") or {}
    issues = checks.get("issues") or []
    started = bool(checks.get("started"))
    warn = []
    if not started:  # nach Aufstellungsschluss wären Hinweise wertlos
        for i in issues:
            if i["level"] == "warn" or i["title"].startswith("Bessere Aufstellung"):
                warn.append(short(i["title"]))
    warn = warn[:3]

    # Checkliste für den nächsten Spieltag: Konto, leere Positionen, beste Aufstellung
    check = []
    if not started and checks:
        balance = me["balance"]
        check.append({"ok": balance >= 0,
                      "t": "Konto nicht im Minus" if balance >= 0 else f"Konto im Minus: {mio(balance)} Mio"})
        empty = next((i for i in issues if "unbesetzte Position" in i["title"] or i["title"].startswith("Keine Aufstellung")), None)
        m = re.match(r"(\d+) unbesetzte", empty["title"]) if empty else None
        check.append({"ok": empty is None,
                      "t": "Keine leere Position" if empty is None
                      else (f"{m.group(1)} Position(en) leer" if m else "Aufstellung fehlt")})
        best = checks.get("best")
        if best is None:
            check.append({"ok": False, "t": "Aufstellung nicht prüfbar"})
        else:
            fnow, fbest = best.get("formationNow") or "?", best.get("formation") or "?"
            if best.get("optimal"):
                check.append({"ok": True, "t": "Beste Aufstellung gewählt", "d": f"Formation {fnow}"})
            else:
                item = {"ok": False, "t": "Bessere Aufstellung: +" + f"{best['gain']:.1f}".replace(".", ",") + " Pkt",
                        "d": f"Formation {fnow}" + (f" \u2192 {fbest}" if fbest != fnow else " (beste)")}
                if best.get("in") or best.get("out"):
                    item["s"] = f"rein: {names(best.get('in')) or '-'} \u00b7 raus: {names(best.get('out')) or '-'}"
                check.append(item)
    next_md = status.get("nextMd")
    check_title = ("Spieltag " + str(next_md) + (" läuft" if started else ": bereit?")) if next_md else "Saison beendet"

    ko_md, ko = next_kickoff(d, raw)
    ko_b = ko.astimezone(BERLIN) if ko else None

    generated = datetime.fromisoformat(d["generatedAt"].replace("Z", "+00:00")).astimezone(BERLIN)
    leader = clean_name(standings[0]["name"]) if standings else ""
    payload = {
        "stand": generated.strftime("%d.%m. %H:%M"),
        "next": f"Spieltag {ko_md}" if ko_md else ("Saison beendet" if not next_md else f"Spieltag {next_md}"),
        "leader": leader,
        "me": {"rank": me["rank"], "pts": int(me["points"]), "gap": int(me["gap"]),
               "top3": f"{(me_fc.get('p_top3') or 0) * 100:.1f}".replace(".", ",") + " %",
               "title": f"{(me_fc.get('p_title') or 0) * 100:.1f}".replace(".", ",") + " %"},
        "ko": {"md": ko_md, "ts": int(ko.timestamp()) if ko else None,
               "text": f"{WEEKDAYS[ko_b.weekday()]} {ko_b:%d.%m.}, {ko_b:%H:%M} Uhr" if ko_b else None},
        "rows": rows,
        "check": check,
        "check_title": check_title,
        "warn": warn,
        "warn_short": "Punkte in Gefahr" if any(w for w in warn if not w.startswith("Bessere")) else "Aufstellung prüfen",
    }
    return payload


POS = {"keeper": "TW", "defender": "AB", "midfielder": "MF", "striker": "ST"}


def build_market(d):
    """Transfermarkt-Tipps: beste Kaufkandidaten mit empfohlenem Gebot."""
    me = d["me"]
    rows = []
    cand = [m for m in d.get("market") or [] if not m.get("mine") and str(m.get("ownerId") or "") != str(me["id"])
            and m.get("recommendation") in ("ja", "vielleicht")]
    cand.sort(key=lambda m: (m.get("recommendation") != "ja", -(m.get("rank") or 0)))
    for m in cand[:5]:
        rest = m.get("remainingDays")
        rows.append({"n": short(m.get("name") or "?", 20), "pos": POS.get(m.get("position"), ""),
                     "club": short(m.get("club") or "", 14), "pts": int(m.get("points") or 0),
                     "mw": mio(m.get("marketValue") or 0), "bid": mio(m.get("bid") or 0),
                     "rec": m.get("recommendation"), "ok": bool(m.get("affordable")),
                     "rest": f"{rest} T" if rest not in (None, "") else "-",
                     "von": "PC" if m.get("fromComputer") else short(clean_name(m.get("owner")), 10)})
    generated = datetime.fromisoformat(d["generatedAt"].replace("Z", "+00:00")).astimezone(BERLIN)
    return {"stand": generated.strftime("%d.%m. %H:%M"), "cash": mio(me["balance"]) + " Mio",
            "n_market": len(d.get("market") or []), "n_ja": sum(1 for m in cand if m.get("recommendation") == "ja"),
            "rows": rows}


def normalize_url(value):
    """Webhook-URL aus dem Secret: ergänzt fehlendes https:// oder baut sie aus der reinen Kennung."""
    v = (value or "").strip().strip('"').strip("'").strip()
    if re.match(r"^https?://", v):
        return v
    if v.startswith("trmnl.com/") or v.startswith("usetrmnl.com/") or v.startswith("www."):
        return "https://" + v
    if v.startswith("/api/custom_plugins/"):
        return "https://trmnl.com" + v
    if v.startswith("api/custom_plugins/"):
        return "https://trmnl.com/" + v
    if re.fullmatch(r"[A-Za-z0-9_-]{8,}", v):
        return "https://trmnl.com/api/custom_plugins/" + v
    return v


def send(url, payload, label="TRMNL"):
    """Schickt merge_variables an einen TRMNL-Webhook. Die URL wird nie ausgegeben."""
    body = json.dumps({"merge_variables": payload}, ensure_ascii=False, separators=(",", ":")).encode()
    print(f"{label}: {len(body)} Bytes")
    if os.environ.get("DRY_RUN"):
        print(body.decode())
        return 0
    from urllib.parse import urlparse
    url = normalize_url(url)
    u = urlparse(url)
    path_ok = u.path.startswith("/api/custom_plugins/") and len(u.path) > len("/api/custom_plugins/") + 8
    print(f"{label}: Ziel {u.scheme}://{u.netloc}, Pfad {'ok' if path_ok else 'UNERWARTET (erwartet /api/custom_plugins/<id>)'}")
    if not (u.scheme and u.netloc):
        print(f"{label}: Secret enthält keine gültige Webhook-URL (Länge {len(url)}, "
              f"Schrägstriche {url.count('/')}, Leerzeichen {url.count(' ')}) – übersprungen")
        return 1
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "comunio-zentrale/1.0 (+github actions)"}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"{label}: HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        detail = e.read(300).decode("utf-8", "replace").replace(url, "<url>")
        print(f"{label}: HTTP {e.code} – {detail}")
        return 1
    return 0


def main():
    d = json.loads((DATA / "dashboard.json").read_text())
    raw_p = DATA / "raw.json"
    raw = json.loads(raw_p.read_text()) if raw_p.exists() else None
    rc = 0

    url = os.environ.get("TRMNL_WEBHOOK_URL", "").strip()
    if url:
        payload = build_payload(d, raw)
        body = json.dumps({"merge_variables": payload}, ensure_ascii=False).encode()
        if len(body) > MAX_BYTES:  # zuerst die Teamwert-Spalte weglassen
            for r in payload["rows"]:
                r.pop("tv", None)
            for c in payload["check"]:
                c.pop("s", None)
        print(f"TRMNL: {len(payload['warn'])} Hinweis(e)")
        rc |= send(url, payload, "TRMNL")
    else:
        print("TRMNL_WEBHOOK_URL nicht gesetzt – übersprungen")

    murl = os.environ.get("TRMNL_MARKET_WEBHOOK_URL", "").strip()
    if murl:
        rc |= send(murl, build_market(d), "TRMNL-Markt")
    else:
        print("TRMNL_MARKET_WEBHOOK_URL nicht gesetzt – übersprungen")
    return rc


if __name__ == "__main__":
    sys.exit(main())
