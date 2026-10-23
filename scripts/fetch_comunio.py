#!/usr/bin/env python3
"""Holt die Community-Daten von Comunio (nur lesend) und schreibt data/raw.json.

Zugangsdaten kommen ausschließlich aus Umgebungsvariablen:
  COMUNIO_USER, COMUNIO_PASS
Es wird nichts gepostet, geboten oder verändert – nur GET-Abrufe nach dem Login.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://www.comunio.de"
UA = "Mozilla/5.0 (comunio-dashboard; private read-only)"
OUT = Path(os.environ.get("DATA_DIR", "data")) / "raw.json"
MAX_NEWS = int(os.environ.get("MAX_NEWS", "400"))


def _request(url, data=None, token=None, retries=3):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # Bei Login-Fehlern nicht wiederholen (Account-Sperre vermeiden)
            if e.code in (400, 401, 403):
                try:
                    detail = json.loads(e.read().decode("utf-8"))
                    detail = detail.get("error_description") or detail.get("error") or detail.get("message") or ""
                except Exception:
                    detail = ""
                raise SystemExit(f"Comunio antwortet mit HTTP {e.code} für {url.replace(BASE, '').split('?')[0]} {str(detail)[:120]}")
            last = e
        except Exception as e:  # Netzwerk
            last = e
            print(f"::warning title=Comunio-Abruf::Versuch {attempt + 1}: {type(e).__name__}: {str(e)[:150]}")
        time.sleep(2 * (attempt + 1))
    raise SystemExit(f"Abruf fehlgeschlagen: {url.split('?')[0]} ({type(last).__name__})")


def login():
    user = os.environ.get("COMUNIO_USER")
    pw = os.environ.get("COMUNIO_PASS")
    if not user or not pw:
        raise SystemExit("COMUNIO_USER / COMUNIO_PASS fehlen (GitHub Secrets).")
    tz = -time.timezone // 3600 if not time.daylight else -time.altzone // 3600
    res = _request(BASE + "/api/login", data={"username": user, "password": pw.strip(), "tzoffset": tz})
    tok = res.get("access_token")
    if not tok:
        raise SystemExit("Login lieferte kein Token.")
    return tok


def main():
    tok = login()
    get = lambda path: _request(BASE + path, token=tok)

    root = get("/api/")
    links = {k: v["href"].replace(BASE, "") for k, v in root["_links"].items()}
    me = root["user"]
    community = root["community"]
    cid = community["id"]

    standings = get(f"/api/communities/{cid}/standings?period=total&wpe=true")
    perennial = get(f"/api/communities/{cid}/standings?period=perennial")
    members = get(f"/api/communities/{cid}/members")

    # Spieltage der laufenden Saison (Ereignisse bis zum ersten SEASON_END)
    events = []
    for ev in standings.get("_embedded", {}).get("formerEventsWithPoints", {}).get("events", []):
        if ev.get("type") == "SEASON_END":
            break
        if ev.get("type") == "MATCHDAY":
            events.append(ev)
    matchdays = []
    for ev in events:
        md = get(f"/api/communities/{cid}/standings?period={ev['id']}&wpe=true")
        matchdays.append({
            "id": ev["id"], "key": ev.get("matchdayKey"), "name": ev.get("event"),
            "items": [{"userId": i["_embedded"]["user"]["id"],
                       "points": i.get("lastPoints"), "total": i.get("totalPoints")}
                      for i in md.get("items", [])],
        })
        time.sleep(0.3)

    squads = {}
    for m in members.get("members", []):
        sq = get(f"/api/users/{m['id']}/squad")
        squads[str(m["id"])] = [{
            "id": p.get("id"), "name": p.get("name"), "club": (p.get("club") or {}).get("name"),
            "position": p.get("position"), "points": p.get("points"), "avg": p.get("averagePoints"),
            "marketValue": p.get("quotedprice"), "status": p.get("status"),
            "purchasePrice": (p.get("purchaseInfo") or {}).get("price"),
            "purchaseDate": (p.get("purchaseInfo") or {}).get("date"),
        } for p in sq.get("items", [])]
        time.sleep(0.3)

    market = get(links["game:exchangemarket"])
    market_items = [{
        "id": it["_embedded"]["player"].get("id"),
        "name": it["_embedded"]["player"].get("name"),
        "club": it["_embedded"]["player"].get("club"),
        "position": it["_embedded"]["player"].get("position"),
        "points": it["_embedded"]["player"].get("points"),
        "marketValue": it["_embedded"]["player"].get("quotedPrice"),
        "recommendedPrice": it["_embedded"]["player"].get("recommendedPrice"),
        "trend": it["_embedded"]["player"].get("trend"),
        "status": it["_embedded"]["player"].get("status"),
        "owner": (it["_embedded"].get("owner") or {}).get("name"),
        "remainingDays": it.get("remaining"),
        "date": it.get("date"),
    } for it in market.get("items", [])]

    # Eigener Kontoauszug (seitenweise)
    statement, start = [], 0
    while start < 500:
        page = get(links["game:statement"] + f"?start={start}&limit=50")
        entries = page.get("entries", [])
        statement += [{"date": e.get("date"), "type": e.get("type"), "amount": e.get("amount"),
                       "text": (e.get("details") or {}).get("name")} for e in entries]
        if len(entries) < 50:
            break
        start += 50

    # Transfer-News (seitenweise)
    transfers, start = [], 0
    while start < MAX_NEWS:
        page = get(links["game:news"] + f"?group=true&originaltypes=true&start={start}&limit=50&type=TRANSACTION_TRANSFER")
        groups = (page.get("newsList") or {}).get("groups") or {}
        entries = [e for g in (groups.values() if isinstance(groups, dict) else groups) for e in g.get("entries", [])]
        for e in entries:
            for kind, rows in (e.get("message") or {}).items():
                if not isinstance(rows, list):
                    continue
                for r in rows:
                    transfers.append({
                        "newsId": e.get("id"), "date": e.get("date"), "kind": kind,
                        "player": (r.get("tradable") or {}).get("name"),
                        "playerId": (r.get("tradable") or {}).get("id"),
                        "price": r.get("price"),
                        "fromId": (r.get("from") or {}).get("id"), "from": (r.get("from") or {}).get("name"),
                        "toId": (r.get("to") or {}).get("id"), "to": (r.get("to") or {}).get("name"),
                    })
        if not (page.get("newsList") or {}).get("hasMore") or not entries:
            break
        start += 50
        time.sleep(0.3)

    raw = {
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "me": {"id": me.get("id"), "name": me.get("firstName") or me.get("name"),
               "budget": me.get("budget"), "teamValue": me.get("teamValue"), "points": me.get("points")},
        "community": {"id": cid, "name": community.get("name")},
        "members": [{"id": m["id"], "name": (m.get("firstName") or m.get("login") or "").strip()}
                    for m in members.get("members", [])],
        "standings": [{
            "userId": i["_embedded"]["user"]["id"], "name": (i["_embedded"]["user"].get("name") or "").strip(),
            "points": i.get("totalPoints"), "lastPoints": i.get("lastPoints"),
            "teamValue": (i["_embedded"].get("teamInfo") or {}).get("teamValue"),
        } for i in standings.get("items", [])],
        "perennial": [{
            "userId": i["_embedded"]["user"]["id"], "name": (i["_embedded"]["user"].get("name") or "").strip(),
            "seasons": (i["_embedded"].get("teamInfo") or {}).get("seasons") or [],
        } for i in perennial.get("items", [])],
        "matchdays": matchdays,
        "squads": squads,
        "market": market_items,
        "nextTransfers": market.get("nextTransfersDateTime"),
        "statement": statement,
        "transfers": transfers,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(raw, ensure_ascii=False, indent=1))
    # Bewusst keine Inhalte ausgeben – die Action-Logs eines öffentlichen Repos sind öffentlich.
    print(f"ok: {len(raw['members'])} Manager, {len(matchdays)} Spieltage, {len(market_items)} Marktspieler, {len(transfers)} Transfers")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        print(f"Fehler: {e}", file=sys.stderr)
        print(f"::error title=Comunio-Abruf::{e}")  # als Annotation sichtbar, ohne Daten
        sys.exit(1)
    except Exception as e:
        print(f"::error title=Comunio-Abruf::{type(e).__name__}: {str(e)[:200]}")
        raise
