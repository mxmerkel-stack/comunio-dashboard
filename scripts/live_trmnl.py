#!/usr/bin/env python3
"""Live-Spieltag für TRMNL: Spieltagspunkte aller Manager und deine Elf mit Live-Ergebnissen.

Läuft nur, wenn laut OpenLigaDB gerade ein Bundesliga-Spiel läuft (oder vor höchstens 30 Minuten
endete). Erst dann meldet es sich bei Comunio an (nur lesen). Braucht COMUNIO_USER, COMUNIO_PASS
und TRMNL_LIVE_WEBHOOK_URL. Mit FORCE_LIVE=1 läuft es auch ohne laufendes Spiel (Test).
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import next_match as NM  # noqa: E402
import push_trmnl as PT  # noqa: E402

BERLIN = ZoneInfo("Europe/Berlin")
MATCH_MINUTES = 115  # Anpfiff bis Abpfiff inkl. Pause und Nachspielzeit
GRACE_MINUTES = 30   # nach Abpfiff noch aktualisieren, bis Comunio die Noten vergeben hat


def current_fixtures():
    """Paarungen des aktuellen Bundesliga-Spieltags (OpenLigaDB) mit Live-Stand."""
    data = NM._get("https://api.openligadb.de/getmatchdata/bl1")
    out = []
    for m in data or []:
        if not isinstance(m, dict):
            continue
        res = [r for r in (m.get("matchResults") or []) if isinstance(r, dict)]
        final = next((r for r in res if r.get("resultTypeID") == 2), None)
        goals = [g for g in (m.get("goals") or []) if isinstance(g, dict)]
        last = max(goals, key=lambda g: g.get("goalID") or 0) if goals else None
        gh = final.get("pointsTeam1") if final else (last.get("scoreTeam1") if last else 0)
        ga = final.get("pointsTeam2") if final else (last.get("scoreTeam2") if last else 0)
        out.append({"home": (m.get("team1") or {}).get("teamName"), "away": (m.get("team2") or {}).get("teamName"),
                    "kickoff": PT.parse_ts(m.get("matchDateTimeUTC") or m.get("matchDateTime")),
                    "finished": bool(m.get("matchIsFinished")), "gh": gh, "ga": ga})
    return out


def is_live(fixtures, now):
    for f in fixtures:
        ko = f["kickoff"]
        if not ko or ko > now:
            continue
        end = ko + timedelta(minutes=MATCH_MINUTES + GRACE_MINUTES)
        if now <= end:
            return True
    return False


def fixture_state(f, now):
    if f["finished"]:
        return "Ende"
    if f["kickoff"] and f["kickoff"] > now:
        return f["kickoff"].astimezone(BERLIN).strftime("%a %H:%M").replace("Sat", "Sa").replace("Sun", "So") \
            .replace("Fri", "Fr").replace("Mon", "Mo").replace("Tue", "Di").replace("Wed", "Mi").replace("Thu", "Do")
    mins = int((now - f["kickoff"]).total_seconds() // 60) if f["kickoff"] else 0
    return "live" if mins <= MATCH_MINUTES else "Ende"


def main():
    url = os.environ.get("TRMNL_LIVE_WEBHOOK_URL", "").strip()
    if not url and not os.environ.get("DRY_RUN"):
        print("TRMNL_LIVE_WEBHOOK_URL nicht gesetzt – übersprungen")
        return 0
    now = datetime.now(timezone.utc)
    try:
        fixtures = current_fixtures()
    except Exception as e:
        print(f"OpenLigaDB nicht erreichbar ({type(e).__name__}) – übersprungen")
        return 0
    if not is_live(fixtures, now) and not os.environ.get("FORCE_LIVE"):
        print("Gerade läuft kein Bundesliga-Spiel – kein Comunio-Abruf")
        return 0

    import fetch_comunio as FC
    tok = FC.login()
    get = lambda path: FC._request(FC.BASE + path, token=tok)  # noqa: E731
    root = get("/api/")
    links = {k: v["href"].replace(FC.BASE, "") for k, v in root["_links"].items()}
    me_id = str(root["user"]["id"])
    cid = root["community"]["id"]

    # Laufender Spieltag laut Comunio
    mdays = [m for m in (get("/api/matchdays") or []) if isinstance(m, dict)]
    running = [m for m in mdays if m.get("started") and not m.get("finished")]
    md_key = str((running or sorted([m for m in mdays if m.get("started")],
                                    key=lambda m: int(m.get("matchdayKey") or 0))[-1:] or [{}])[0].get("matchdayKey") or "")

    # Spieltagspunkte je Manager: Ereignis des laufenden Spieltags, sonst Gesamt minus Vortag
    total = get(f"/api/communities/{cid}/standings?period=total&wpe=true")
    names, tot = {}, {}
    for it in total.get("items", []):
        u = FC.D(FC.D(it.get("_embedded")).get("user"))
        names[str(u.get("id"))] = PT.clean_name((u.get("name") or "").strip())
        tot[str(u.get("id"))] = int(it.get("totalPoints") or 0)
    events = [e for e in total.get("_embedded", {}).get("formerEventsWithPoints", {}).get("events", [])
              if e.get("type") == "MATCHDAY"]
    ev = next((e for e in events if str(e.get("matchdayKey")) == md_key), None)
    md_pts = {}
    if ev:
        st = get(f"/api/communities/{cid}/standings?period={ev['id']}&wpe=true")
        for it in st.get("items", []):
            uid = str(FC.D(FC.D(it.get("_embedded")).get("user")).get("id"))
            md_pts[uid] = int(it.get("lastPoints") or 0)
    else:
        prev = next((e for e in events if str(e.get("matchdayKey")) == str(int(md_key or 1) - 1)), None)
        if prev:
            st = get(f"/api/communities/{cid}/standings?period={prev['id']}&wpe=true")
            for it in st.get("items", []):
                uid = str(FC.D(FC.D(it.get("_embedded")).get("user")).get("id"))
                md_pts[uid] = tot.get(uid, 0) - int(it.get("totalPoints") or 0)
    table = sorted(names, key=lambda u: (-md_pts.get(u, 0), -tot.get(u, 0)))
    rows = [{"m": names[u], "md": md_pts.get(u, 0), "tot": tot.get(u, 0), "me": u == me_id} for u in table]

    # Eigene Elf: Punkte am laufenden Spieltag und Live-Stand ihres Spiels
    lu = get(links["game:lineup"])
    items = FC.D(FC.D(lu.get("items")).get("lineup"))
    squad = {str(p.get("id")): p for p in (get(f"/api/users/{me_id}/squad").get("items") or []) if isinstance(p, dict)}
    teams = [t for f in fixtures for t in (f["home"], f["away"]) if t]
    xi = []
    for k in range(1, 12):
        v = FC.D(items.get(str(k)))
        if not v.get("id"):
            xi.append({"n": "(leer)", "pos": "", "pts": None, "game": "", "st": ""})
            continue
        pid = str(v["id"])
        pts = None
        try:
            hist = get(f"/api/players/{pid}/match-statistics-history")
            for e in hist.get("matchStatisticsCollection") or []:
                md = FC.D(FC.D(e).get("matchday"))
                if md.get("seasonEnd") == 9999999 and str(md.get("key")) == md_key:
                    pts = e.get("points")
        except SystemExit:
            pass
        club = FC.D(FC.D(squad.get(pid)).get("club")).get("name") or ""
        team = NM.match_name(club, teams) if club else None
        f = next((f for f in fixtures if team in (f["home"], f["away"])), None) if team else None
        game, st = "", ""
        if f:
            home = f["home"] == team
            game = f"{f['gh']}:{f['ga']} {'vs' if home else '@'} {PT.short(f['away'] if home else f['home'], 12)}"
            st = fixture_state(f, now)
        xi.append({"n": PT.short(v.get("name") or "?", 16), "pos": PT.POS.get(v.get("type"), ""),
                   "pts": pts, "game": game, "st": st})
        time.sleep(0.15)
    mine = sum(x["pts"] or 0 for x in xi)

    payload = {"md": md_key, "stand": now.astimezone(BERLIN).strftime("%d.%m. %H:%M"),
               "tactic": "-".join(str(lu.get("tactic") or "")) if str(lu.get("tactic") or "").isdigit() else (lu.get("tactic") or ""),
               "mine": mine, "rows": rows, "xi": xi,
               "live": sum(1 for f in fixtures if fixture_state(f, now) == "live")}
    return PT.send(url, payload, "TRMNL-Live")


if __name__ == "__main__":
    sys.exit(main())
