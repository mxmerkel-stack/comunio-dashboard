#!/usr/bin/env python3
"""Einmalige Erkundung der Comunio-Schnittstelle für Live-Daten. Gibt nur Strukturen (Schlüssel/Typen) aus, keine Werte."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fetch_comunio as F


def shape(o, d=0):
    if d > 4:
        return "…"
    if isinstance(o, dict):
        return {k: shape(v, d + 1) for k, v in list(o.items())[:25]}
    if isinstance(o, list):
        return [shape(o[0], d + 1), f"len={len(o)}"] if o else []
    if isinstance(o, str) and o.startswith("http"):
        return o.replace(F.BASE, "")[:90]
    return type(o).__name__


def show(title, o):
    print(f"::notice title={title}::" + json.dumps(shape(o), ensure_ascii=False)[:3500])


tok = F.login()
get = lambda p: F._request(F.BASE + p, token=tok, retries=1)
root = get("/api/")
links = {k: v["href"].replace(F.BASE, "") for k, v in root["_links"].items()}
print("::notice title=root links::" + ", ".join(f"{k}={v}" for k, v in links.items())[:3500])
cid = root["community"]["id"]
me = root["user"]["id"]
st = get(f"/api/communities/{cid}/standings?period=total&wpe=true")
show("standings total", st)
for path in [f"/api/communities/{cid}/standings?period=matchday&wpe=true",
             f"/api/communities/{cid}/standings?period=live",
             f"/api/communities/{cid}/livestandings",
             f"/api/communities/{cid}/users/{me}/lineup",
             f"/api/users/{me}/lineup", f"/api/users/{me}/livelineup",
             f"/api/communities/{cid}/matchday", "/api/matchdays/current", "/api/live", "/api/livescores"]:
    try:
        show("GET " + path.replace(str(cid), "CID").replace(str(me), "ME"), get(path))
    except SystemExit as e:
        print(f"::notice title=GET {path.replace(str(cid), 'CID').replace(str(me), 'ME')}::{str(e)[:120]}")
    except Exception as e:
        print(f"::notice title=ERR::{type(e).__name__}")
try:
    lu = get(links["game:lineup"])
    show("lineup", lu)
except Exception as e:
    print(f"::notice title=lineup err::{e}")
for k, v in links.items():
    if any(w in k.lower() for w in ("live", "matchday", "point", "lineup", "standing")):
        try:
            show("link " + k, get(v))
        except BaseException as e:
            print(f"::notice title=link {k}::{str(e)[:100]}")
# eine Spielstatistik eines eigenen Spielers: letzte Zeile
sq = get(f"/api/users/{me}/squad")
pid = next((p["id"] for p in sq.get("items", []) if isinstance(p, dict)), None)
if pid:
    h = get(f"/api/players/{pid}/match-statistics-history")
    coll = h.get("matchStatisticsCollection") or []
    show("matchstats entry", coll[0] if coll else h)
    show("matchstats top", {k: v for k, v in h.items() if k != "matchStatisticsCollection"})
