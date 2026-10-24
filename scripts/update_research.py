#!/usr/bin/env python3
"""Aktualisiert research/rumors.json und research/injuries.json per Claude-API mit Websuche.

Benötigt das Secret ANTHROPIC_API_KEY. Die Spielerliste ist immer der aktuelle Kader aus data/raw.json. Fehlt es oder schlägt ein Aufruf fehl, bleiben die
bisherigen Einträge unverändert (Exit-Code 0, Warnung als Annotation).
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = os.environ.get("RESEARCH_MODEL", "claude-sonnet-5-5")
KEY = os.environ.get("ANTHROPIC_API_KEY", "")
BATCH = 5
TODAY = dt.datetime.now(dt.timezone(dt.timedelta(hours=2))).strftime("%d.%m.%Y")

RUMOR_FIELDS = ["target", "probability", "note", "source", "url", "status", "pWinter", "pSummer"]
INJURY_FIELDS = ["return", "note", "source", "url"]

RUMOR_TASK = """Recherchiere per Websuche (transfermarkt.de, kicker.de, bild.de, sport1, sky, Vereinsseiten) die aktuellen Transfergerüchte
für diese Bundesliga-Spieler. Heutiges Datum: {today}. Schätze für jeden die Wahrscheinlichkeit eines Vereinswechsels im
kommenden Winter (pWinter) und im kommenden Sommer (pSummer) als Zahl 0..1, vorsichtig und aus den Quellen begründet;
"probability" ist der höhere der beiden Werte. Ohne belastbare neue Quelle den bisherigen Eintrag beibehalten.
Felder je Spieler: target (Zielverein/e oder ""), probability, pWinter, pSummer, note (2-3 Sätze, deutsch, mit Datum der Meldung),
source (Medium + Datum), url (echte, aufgerufene URL), status ("Gerücht", "Altes Gerücht", "Konkret", "Kein Gerücht").
Spieler (ID: Name, bisheriger Eintrag):
{players}"""

INJURY_TASK = """Recherchiere per Websuche (transfermarkt.de Verletzungshistorie, kicker.de, ligainsider, Vereinsseiten) den aktuellen
Verletzungs-/Reha-/Sperrenstand dieser Bundesliga-Spieler. Heutiges Datum: {today}. Felder je Spieler: return (voraussichtliche Rückkehr,
kurz, z. B. "ca. 08.11."), note (1-2 Sätze deutsch: Art der Verletzung, seit wann, Stand), source (Medium + Datum), url (echte URL).
Ist ein Spieler wieder fit und im Kader, setze "fit": true (dann ohne weitere Felder).
Spieler (ID: Name, bisheriger Eintrag):
{players}"""

OUT = 'Antworte ausschließlich mit einem JSON-Objekt {"<ID>": {...Felder...}, ...} ohne Erklärtext und ohne Markdown-Zaun.'


def call(prompt):
    body = {"model": MODEL, "max_tokens": 6000,
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 12}],
            "messages": [{"role": "user", "content": prompt + "\n\n" + OUT}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                 headers={"x-api-key": KEY, "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as r:
        data = json.loads(r.read().decode())
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("keine JSON-Antwort")
    return json.loads(m.group(0))


def clamp(x, default=None):
    try:
        return round(min(1.0, max(0.0, float(x))), 2)
    except (TypeError, ValueError):
        return default


def load_squad():
    """Aktueller Kader des Nutzers aus data/raw.json (von fetch_comunio.py): {id: {name, club, status}}."""
    p = Path(os.environ.get("DATA_DIR", ROOT / "data")) / "raw.json"
    if not p.exists():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))
    me = str((raw.get("me") or {}).get("id", ""))
    sq = (raw.get("squads") or {}).get(me) or []
    return {str(x["id"]): {"name": x.get("name"), "club": x.get("club"), "status": x.get("status"),
                           "statusInfo": x.get("statusInfo")} for x in sq if isinstance(x, dict) and x.get("id")} or None


DEFAULT = {"rumors": {"target": "", "probability": 0.0, "pWinter": 0.0, "pSummer": 0.0, "note": "Noch nicht recherchiert.",
                      "source": "", "url": "", "status": "Keine Angabe"},
           "injuries": {"return": "", "note": "", "source": "", "url": ""}}


def run(name, task, fields, squad):
    path = ROOT / "research" / f"{name}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    old = doc.get("players", {})
    if squad:
        # Gerüchte: alle Spieler des aktuellen Kaders. Ausfälle: Kaderspieler, die schon gelistet oder bei Comunio nicht ACTIVE sind.
        keep = [pid for pid, v in squad.items() if name == "rumors" or pid in old or (v.get("status") or "ACTIVE") != "ACTIVE"]
        players = {}
        for pid in keep:
            base = old.get(pid) or {"name": squad[pid]["name"], **DEFAULT[name]}
            if name == "injuries" and pid not in old and squad[pid].get("statusInfo"):
                base["note"] = str(squad[pid]["statusInfo"])
            players[pid] = base
        print(f"{name}: {len(players)} Spieler aus dem aktuellen Kader ({len(old.keys() - players.keys())} entfernt)")
    else:
        players = old
        print(f"::warning title=Recherche::data/raw.json fehlt – nur bereits gelistete Spieler")
    doc["players"] = players
    ids = list(players)
    changed = 0
    for i in range(0, len(ids), BATCH):
        part = ids[i:i + BATCH]
        listing = "\n".join(f"{pid}: {players[pid].get('name')} – {json.dumps({k: players[pid].get(k) for k in fields}, ensure_ascii=False)}"
                            for pid in part)
        try:
            res = call(task.format(today=TODAY, players=listing))
        except Exception as e:  # Netz, Limit, Format
            print(f"::warning title=Recherche {name}::{type(e).__name__} bei {','.join(part)} – alte Werte bleiben")
            continue
        for pid in part:
            new = res.get(pid)
            if not isinstance(new, dict):
                continue
            if name == "injuries" and new.get("fit"):
                del players[pid]
                changed += 1
                continue
            upd = {k: new[k] for k in fields if new.get(k) not in (None, "")}
            for k in ("probability", "pWinter", "pSummer"):
                if k in upd:
                    upd[k] = clamp(upd[k], players[pid].get(k))
            if upd.get("url") and not str(upd["url"]).startswith("http"):
                del upd["url"]
            if upd and any(players[pid].get(k) != v for k, v in upd.items()):
                players[pid].update(upd)
                changed += 1
    doc["asOf"] = TODAY
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{name}: {changed} Einträge geändert")


def main():
    if not KEY:
        print("::warning title=Recherche::ANTHROPIC_API_KEY fehlt – nichts aktualisiert")
        return 0
    squad = load_squad()
    run("rumors", RUMOR_TASK, RUMOR_FIELDS, squad)
    run("injuries", INJURY_TASK, INJURY_FIELDS, squad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
