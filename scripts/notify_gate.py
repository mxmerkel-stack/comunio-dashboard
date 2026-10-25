#!/usr/bin/env python3
"""Zeitprüfung ohne Comunio-Zugang. Schreibt run=true/false nach $GITHUB_OUTPUT.

  MODE=eve    true um 20 Uhr (Berlin) am Tag vor dem ersten Anpfiff des nächsten Spieltags
  MODE=alarm  true, wenn der erste Anpfiff des Spieltags in 2 bis 3,2 Stunden liegt
  MODE=result true, wenn alle Spiele des letzten Spieltags beendet sind (höchstens 48 Std. her) und
              dafür noch keine Ergebnis-Nachricht gesendet wurde; schreibt außerdem md=<Spieltag>
Erster Anpfiff = frühestes Spiel der Spieltagsgruppe des nächsten angesetzten Spiels (OpenLigaDB).
"""
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
MODE = os.environ.get("MODE", "eve")


def first_kickoff(now):
    year = now.year if now.month >= 7 else now.year - 1
    req = urllib.request.Request(f"https://api.openligadb.de/getmatchdata/bl1/{year}", headers={"User-Agent": "comunio-dashboard"})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
    ms = []
    for m in data:
        if m.get("matchDateTimeUTC"):
            ms.append((datetime.fromisoformat(m["matchDateTimeUTC"].replace("Z", "+00:00")), (m.get("group") or {}).get("groupOrderID")))
    upcoming = sorted(x for x in ms if x[0] > now)
    if not upcoming:
        return None
    grp = upcoming[0][1]
    return min(t for t, g in ms if g == grp)


def finished_matchday(now):
    """(Spieltag, Ende des letzten Spiels) des jüngsten Spieltags, dessen Spiele alle beendet sind."""
    year = now.year if now.month >= 7 else now.year - 1
    req = urllib.request.Request(f"https://api.openligadb.de/getmatchdata/bl1/{year}", headers={"User-Agent": "comunio-dashboard"})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
    groups = {}
    for m in data:
        g = (m.get("group") or {}).get("groupOrderID")
        if g and m.get("matchDateTimeUTC"):
            groups.setdefault(g, []).append(m)
    done = [g for g, ms in groups.items() if all(x.get("matchIsFinished") for x in ms)]
    if not done:
        return None, None
    g = max(done)
    last = max(datetime.fromisoformat(x["matchDateTimeUTC"].replace("Z", "+00:00")) for x in groups[g])
    return g, last


def main():
    run = False
    md_out = ""
    now = datetime.now(timezone.utc)
    if MODE == "result":
        try:
            g, last = finished_matchday(now)
            sent = {}
            try:
                sent = json.loads((Path(os.environ.get("DATA_DIR", "data")) / "notified.json").read_text())
            except Exception:
                pass
            if g and now - last < timedelta(hours=48) and f"result-{g}" not in sent:
                run, md_out = True, str(g)
            print(f"Letzter beendeter Spieltag: {g}, Ende ca. {last} -> result: run={run}")
        except Exception as e:
            print(f"::warning title=Gate::{type(e).__name__}")
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write(f"run={'true' if run else 'false'}\nmd={md_out}\n")
        return
    try:
        first = first_kickoff(now)
        if first and first > now:
            local = now.astimezone(TZ)
            if MODE == "eve":
                run = local.hour == 20 and first.astimezone(TZ).date() == local.date() + timedelta(days=1)
            else:
                run = timedelta(minutes=120) < first - now <= timedelta(minutes=192)
            print(f"Erster Anpfiff {first.astimezone(TZ):%a %d.%m. %H:%M}, jetzt {local:%a %H:%M} -> {MODE}: run={run}")
        else:
            print("Kein anstehender Spieltag gefunden.")
    except Exception as e:
        print(f"::warning title=Gate::{type(e).__name__}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"run={'true' if run else 'false'}\n")


if __name__ == "__main__":
    main()
