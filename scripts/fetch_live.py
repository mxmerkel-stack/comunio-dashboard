#!/usr/bin/env python3
"""Live-Daten des laufenden Spieltags: Livepunkte je Manager, Aufstellungen mit Livepunkten je Spieler,
Tore und Karten aus der Spielstatistik, Spielstände. Schreibt $DIST_DIR/live.json (danach verschlüsselt)."""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fetch_comunio as F

D = F.D
DIST = Path(os.environ.get("DIST_DIR", "dist"))


def num(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None


def main():
    tok = F.login()
    get = lambda p: F._request(F.BASE + p, token=tok)
    root = get("/api/")
    links = {k: v["href"].replace(F.BASE, "") for k, v in root["_links"].items()}
    cid, me = root["community"]["id"], root["user"]["id"]

    cur = get("/api/matchdays/current")
    md_key = cur.get("matchdayKey")
    fixtures = [{"home": D(m.get("home")).get("name"), "away": D(m.get("guest")).get("name"),
                 "goalsHome": D(m.get("home")).get("goals"), "goalsAway": D(m.get("guest")).get("goals"),
                 "kickoff": m.get("kickoff"), "started": m.get("started")}
                for m in cur.get("items") or [] if isinstance(m, dict)]

    live = get(f"/api/communities/{cid}/standings?period=live")
    managers = []
    for it in live.get("items") or []:
        u = D(D(it.get("_embedded")).get("user"))
        managers.append({"id": str(u.get("id")), "name": u.get("name"), "livePoints": it.get("livePoints"),
                         "total": it.get("totalPoints"), "scored": it.get("playersPossiblyScoredAmount"), "players": []})

    tmpl = links.get("game:lineup:matchday", f"/api/communities/{cid}/users/{me}/lineup/matchday/:matchdayId/:ownerId")
    md_ids = [x for x in (cur.get("id"), D(cur.get("eventInfo")).get("id")) if x]
    stats_cache = {}
    for m in managers:
        lu = None
        for mid in md_ids:
            try:
                lu = get(tmpl.replace(":matchdayId", str(mid)).replace(":ownerId", m["id"]))
                break
            except SystemExit as e:
                print(f"::warning title=Live-Aufstellung::{m['name']}: {str(e)[:120]}")
        if not lu:
            continue
        m["tactic"] = lu.get("tactic")
        slots = D(D(lu.get("items")).get("lineup"))
        for slot, p in sorted(slots.items(), key=lambda kv: num(kv[0]) or 0):
            if not isinstance(p, dict) or not p.get("id"):
                continue
            pid = str(p["id"])
            if pid not in stats_cache:
                g = y = r = yr = pts = None
                try:
                    h = get(f"/api/players/{pid}/match-statistics-history")
                    for e in h.get("matchStatisticsCollection") or []:
                        md = D(e.get("matchday"))
                        if md.get("seasonEnd") == 9999999 and str(md.get("key")) == str(md_key):
                            g, y, r, yr, pts = num(e.get("goals")), num(e.get("yellow")), num(e.get("red")), num(e.get("yellowRed")), num(e.get("points"))
                            break
                except SystemExit:
                    pass
                stats_cache[pid] = {"goals": g or 0, "yellow": y or 0, "red": r or 0, "yellowRed": yr or 0, "points": pts}
                time.sleep(0.12)
            m["players"].append({"id": pid, "name": p.get("name"), "club": D(p.get("club")).get("name"),
                                 "slot": num(slot), "type": p.get("type"),
                                 "matchStatus": p.get("matchStatus"), "status": p.get("status"),
                                 "subbed": p.get("wasLiveSubstituted"), **stats_cache[pid]})
            pl = m["players"][-1]
            # Comunio liefert hier keine Livepunkte je Spieler: Punkte aus der Spielstatistik, nur wenn das Spiel läuft/gespielt ist
            pl["livePoints"] = num(p.get("livePoints")) if num(p.get("livePoints")) is not None else (pl.pop("points") if pl.get("matchStatus") not in (None, "pre") else None)
            pl.pop("points", None)
        ps = m["players"]
        m["goals"] = sum(p["goals"] for p in ps)
        m["red"] = sum(p["red"] + p["yellowRed"] for p in ps)
        m["yellow"] = sum(p["yellow"] for p in ps)
        m["counted"] = m.get("scored") if m.get("scored") is not None else sum(1 for p in ps if p.get("matchStatus") not in (None, "pre"))
        m["lineupSize"] = 11
        m["empty"] = max(0, 11 - len(ps))
        time.sleep(0.2)

    out = {"fetchedAt": datetime.now(timezone.utc).isoformat(), "matchday": md_key,
           "started": cur.get("started"), "finished": cur.get("finished"), "fixtures": fixtures,
           "managers": sorted(managers, key=lambda m: -(m["livePoints"] or 0)), "me": str(me)}
    DIST.mkdir(parents=True, exist_ok=True)
    (DIST / "live.json").write_text(json.dumps(out, ensure_ascii=False))
    print(f"live.json: Spieltag {md_key}, {len(managers)} Manager, {sum(len(m['players']) for m in managers)} Spieler")


if __name__ == "__main__":
    main()
