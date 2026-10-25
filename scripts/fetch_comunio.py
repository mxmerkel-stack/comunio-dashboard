#!/usr/bin/env python3
"""Holt die Community-Daten von Comunio (nur lesend) und schreibt data/raw.json.

Zugangsdaten kommen ausschließlich aus Umgebungsvariablen:
  COMUNIO_USER, COMUNIO_PASS
Es wird nichts gepostet, geboten oder verändert – nur GET-Abrufe nach dem Login.
"""
import json
import re
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


def D(x):
    """Nur Objekte weiterverwenden – Comunio liefert manchmal Text statt Objekt."""
    return x if isinstance(x, dict) else {}


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


def download_image(url, token):
    """Wappen als data:-URI laden (mit Anmeldung). Gibt None zurück, wenn es kein kleines Bild ist."""
    import base64
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*", "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            raw = r.read(150_001)
    except Exception as e:
        print(f"::warning title=Vereinswappen::{type(e).__name__}: {str(e)[:100]}")
        return None
    if not ctype.startswith("image/") or len(raw) > 150_000 or not raw:
        return None
    return f"data:{ctype};base64," + base64.b64encode(raw).decode()


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

    club_logos = {}

    def logo_scan(club, base="https://www.comunio.de"):
        """Sucht in einem Vereinsobjekt nach einer Bildadresse (Wappen) und merkt sie je Vereins-ID."""
        if not isinstance(club, dict) or not club.get("id"):
            return
        def walk(o, depth=0):
            if depth > 4:
                return
            if isinstance(o, dict):
                for k, v in o.items():
                    yield from walk(v, depth + 1) if isinstance(v, (dict, list)) else ([(k, v)] if isinstance(v, str) else [])
            elif isinstance(o, list):
                for v in o[:5]:
                    yield from walk(v, depth + 1)
        for k, v in walk(club):
            kl, vl = str(k).lower(), v.lower()
            if (vl.startswith(("http", "/")) and any(w in kl + vl for w in ("logo", "badge", "crest", "emblem", "image", "icon", ".png", ".svg"))):
                club_logos.setdefault(str(club["id"]), v if v.startswith("http") else base + v)
                return

    probe = {"done": False}
    squads = {}
    for m in members.get("members", []):
        sq = get(f"/api/users/{m['id']}/squad")
        for p in sq.get("items", []):
            if isinstance(p, dict):
                logo_scan(p.get("club"))
                if not probe["done"] and isinstance(p.get("club"), dict):
                    probe["done"] = True
                    def shape(o):
                        return {k: (shape(v) if isinstance(v, dict) else type(v).__name__ + (":" + v[:70] if isinstance(v, str) and v.startswith(("http", "/")) else "")) for k, v in o.items()}
                    print("::notice title=Vereinsobjekt (Struktur)::" + json.dumps(shape(p["club"]), ensure_ascii=False)[:900])
        squads[str(m["id"])] = [{
            "id": p.get("id"), "name": p.get("name"), "club": D(p.get("club")).get("name"),
            "position": p.get("position"), "points": p.get("points"), "avg": p.get("averagePoints"),
            "marketValue": p.get("quotedprice"), "trend": p.get("trend"),
            "status": p.get("status"), "statusInfo": p.get("statusInfo"),
            "purchasePrice": D(p.get("purchaseInfo")).get("price"),
            "purchaseDate": D(p.get("purchaseInfo")).get("date"),
            "clubId": D(p.get("club")).get("id"), "linedup": p.get("linedup"), "onMarket": p.get("onMarket"),
            "minBid": p.get("recommendedprice"),
            "nextMatch": {"homeId": D(D(p.get("nextMatch")).get("home")).get("id"),
                          "home": D(D(p.get("nextMatch")).get("home")).get("name"),
                          "guestId": D(D(p.get("nextMatch")).get("guest")).get("id"),
                          "guest": D(D(p.get("nextMatch")).get("guest")).get("name"),
                          "kickoff": D(p.get("nextMatch")).get("kickoff")},
        } for p in sq.get("items", []) if isinstance(p, dict)]
        time.sleep(0.3)

    market = get(links["game:exchangemarket"])
    for it in market.get("items", []):
        if isinstance(it, dict):
            logo_scan(D(D(it.get("_embedded")).get("player")).get("club"))
    market_items = [{
        "id": it["_embedded"]["player"].get("id"),
        "name": it["_embedded"]["player"].get("name"),
        "club": D(it["_embedded"]["player"].get("club")).get("name") or it["_embedded"]["player"].get("club"),
        "statusInfo": it["_embedded"]["player"].get("statusInfo"),
        "position": it["_embedded"]["player"].get("position"),
        "points": it["_embedded"]["player"].get("points"),
        "marketValue": it["_embedded"]["player"].get("quotedPrice"),
        "recommendedPrice": it["_embedded"]["player"].get("recommendedPrice"),
        "trend": it["_embedded"]["player"].get("trend"),
        "status": it["_embedded"]["player"].get("status"),
        "owner": D(it["_embedded"].get("owner")).get("name"),
        "ownerId": D(it["_embedded"].get("owner")).get("id"),
        "clubId": D(it["_embedded"]["player"].get("club")).get("id"),
        "remainingDays": it.get("remaining"),
        "date": it.get("date"),
    } for it in market.get("items", []) if isinstance(it, dict)]
    # Vorsaison-Punkte je Marktspieler aus den Spielerdetails
    for mi in market_items:
        try:
            det = get(f"/api/communities/{cid}/users/{me.get('id')}/players/{mi['id']}")
            hist = D(det.get("historical")).get("points") or []
            mi["seasonPoints"] = {h.get("season"): h.get("points") for h in hist if isinstance(h, dict)}
            mi["dob"] = D(det.get("extendedInfo")).get("dob")
            nm = [x for x in (det.get("nextMatches") or []) if isinstance(x, dict)]
            if nm:
                mi["nextMatch"] = {"homeId": D(nm[0].get("homeClub")).get("id"), "home": D(nm[0].get("homeClub")).get("name"),
                                   "guestId": D(nm[0].get("guestClub")).get("id"), "guest": D(nm[0].get("guestClub")).get("name"),
                                   "kickoff": nm[0].get("kickoff")}
        except SystemExit:
            mi["seasonPoints"] = {}
        time.sleep(0.2)

    # Spielerdetails des eigenen Kaders (Alter, Vorsaisons)
    own_details = {}
    for p in squads.get(str(me.get("id")), []):
        try:
            det = get(f"/api/communities/{cid}/users/{me.get('id')}/players/{p['id']}")
            hist = D(det.get("historical")).get("points") or []
            own_details[str(p["id"])] = {"dob": D(det.get("extendedInfo")).get("dob"),
                                         "seasonPoints": {h.get("season"): h.get("points") for h in hist if isinstance(h, dict)}}
        except SystemExit:
            pass
        time.sleep(0.2)

    # Wappen herunterladen (nur kleine Bilder) und direkt in die Daten einbetten
    for cid_, url in list(club_logos.items()):
        img = download_image(url, tok)
        if img:
            club_logos[cid_] = img
        else:
            del club_logos[cid_]
        time.sleep(0.2)
    print(f"::notice title=Vereinswappen::{len(club_logos)} Wappen geladen")

    # Geburtsdaten der übrigen Kaderspieler (für die Altersanzeige im Kader aller Manager)
    dobs = {pid: d.get("dob") for pid, d in own_details.items() if d.get("dob")}
    dobs.update({str(m["id"]): m["dob"] for m in market_items if m.get("dob")})
    for uid, ps in squads.items():
        for p in ps:
            pid = str(p["id"])
            if pid in dobs:
                continue
            try:
                det = get(f"/api/communities/{cid}/users/{me.get('id')}/players/{pid}")
                dob = D(det.get("extendedInfo")).get("dob")
                if dob:
                    dobs[pid] = dob
            except SystemExit:
                pass
            time.sleep(0.2)

    # Trend-Pfeile: Comunio liefert den Trend (ganze Zahl) für alle Spieler der Liga in der Spielersuche
    trends, start = {}, 0
    while start < 1500:
        try:
            page = get(f"/api/communities/{cid}/players?start={start}&limit=50")
        except SystemExit:
            break
        rows = [t for t in (page.get("tradables") or []) if isinstance(t, dict)]
        for t in rows:
            if t.get("trend") is not None and t.get("id") is not None:
                trends[str(t["id"])] = t["trend"]
        if len(rows) < 50:
            break
        start += 50
        time.sleep(0.2)

    # Spielstatistik je Spieltag (alle Kader- und Marktspieler) – Grundlage der Prognosen
    player_ids = {str(p["id"]) for ps in squads.values() for p in ps} | {str(m["id"]) for m in market_items}
    stats = {}
    for pid in sorted(player_ids):
        try:
            res = get(f"/api/players/{pid}/match-statistics-history")
        except SystemExit:
            continue
        rows = []
        for e in res.get("matchStatisticsCollection") or []:
            if not isinstance(e, dict):
                continue
            md, mt = D(e.get("matchday")), D(e.get("match"))
            rows.append({"season": "cur" if md.get("seasonEnd") == 9999999 else md.get("seasonEnd"),
                         "md": md.get("key"), "date": (md.get("timestamp") or "")[:10],
                         "points": e.get("points"), "in": e.get("in"), "out": e.get("out"),
                         "status": e.get("status"), "yellow": e.get("yellow"), "yellowRed": e.get("yellowRed"),
                         "red": e.get("red"), "goals": e.get("goals"), "clubId": e.get("clubId"),
                         "homeId": D(mt.get("home")).get("id"), "guestId": D(mt.get("guest")).get("id")})
        stats[pid] = rows
        time.sleep(0.15)

    # Marktwertverlauf (alle Kader + Markt) – Grundlage der Trend-Pfeile, falls Comunio keinen Trend liefert
    quotes = {}
    for pid in sorted({str(p["id"]) for ps in squads.values() for p in ps} | {str(m["id"]) for m in market_items}):
        try:
            q = get(f"/api/players/{pid}/quote-history")
            quotes[pid] = [[(x.get("datetime_string") or "")[:10], x.get("quotedPrice")]
                           for x in (q.get("quoteCollection") or [])[:40] if isinstance(x, dict)]
        except SystemExit:
            pass
        time.sleep(0.15)

    # Welche Trend-Werte liefert Comunio? (nur die Wertemenge, keine Spielerdaten)
    seen = sorted({str(v) for v in trends.values()} | {str(m.get("trend")) for m in market_items})
    print(f"::notice title=Trend-Werte von Comunio::{', '.join(seen)}")

    # Eigene Aufstellung, Angebote an dich, Spieltagskalender
    lineup_raw = get(links["game:lineup"])
    lu_items = D(D(lineup_raw.get("items")).get("lineup"))
    slots = []
    for k in range(1, 12):
        v = D(lu_items.get(str(k)))
        slots.append({"slot": k, "id": str(v.get("id")) if v.get("id") else None, "name": v.get("name"),
                      "type": v.get("type"), "status": v.get("status"), "statusInfo": v.get("statusInfo"),
                      "matchStatus": v.get("matchStatus"), "exchangeable": v.get("isExchangeable")})
    lineup = {"tactic": lineup_raw.get("tactic"), "slots": slots,
              "ids": [sl["id"] for sl in slots if sl["id"]]}
    off = get(links["game:readOffers"] + "?current")
    offers = [{"id": o.get("id"), "type": o.get("type"), "state": o.get("state"), "price": o.get("price"),
               "date": o.get("datecreated"), "playerId": D(o.get("tradable")).get("id"),
               "player": D(o.get("tradable")).get("name"), "marketValue": D(o.get("tradable")).get("quotedPrice"),
               "fromId": D(o.get("user")).get("id"), "from": (D(o.get("user")).get("name") or "").strip(),
               "toId": D(o.get("tradingPartner")).get("id")}
              for o in (off.get("items") or []) if isinstance(o, dict)]
    credit = off.get("credit")
    mdays = [{"key": m.get("matchdayKey"), "date": m.get("timestamp"), "started": m.get("started"),
              "finished": m.get("finished"), "season": m.get("season")}
             for m in (get("/api/matchdays") or []) if isinstance(m, dict)]

    # Nächster Spieltag: Paarungen (OpenLigaDB) und Tipico-Quoten (The Odds API, optional)
    # Solange ein Spieltag noch läuft, ist die eigene Aufstellung für ihn schon gesperrt – fürs
    # Planen zählt der nächste noch NICHT begonnene Spieltag (Aufstellungsschluss liegt in der Zukunft).
    import next_match as NM
    open_mds = sorted([m for m in mdays if not m.get("finished") and str(m.get("key", "")).isdigit()],
                      key=lambda m: int(m["key"]))
    upcoming_mds = [m for m in open_mds if not m.get("started")]
    nm_info = {}
    if upcoming_mds or open_mds:
        nmd = (upcoming_mds or open_mds)[0]
        season = str(nmd.get("season") or "")
        year = 2000 + int(season.split("/")[0]) if re.match(r"^\d{2}/\d{2}$", season) else datetime.now().year
        fixtures = NM.fetch_fixtures(year, int(nmd["key"]))
        nm_info = {"md": int(nmd["key"]), "date": nmd.get("date"), "started": nmd.get("started"),
                   "fixtures": fixtures, "odds": NM.fetch_odds()}

    # Eigener Kontoauszug (seitenweise)
    statement, start = [], 0
    while start < 500:
        page = get(links["game:statement"] + f"?start={start}&limit=50")
        entries = page.get("entries", [])
        statement += [{"date": e.get("date"), "type": e.get("type"), "amount": e.get("amount"),
                       "text": D(e.get("details")).get("name")} for e in entries]
        if len(entries) < 50:
            break
        start += 50

    # Transfer-News (seitenweise)
    transfers, start = [], 0
    while start < MAX_NEWS:
        page = get(links["game:news"] + f"?group=true&originaltypes=true&start={start}&limit=50&type=TRANSACTION_TRANSFER")
        groups = D(page.get("newsList")).get("groups") or {}
        entries = [e for g in (groups.values() if isinstance(groups, dict) else groups) for e in D(g).get("entries", []) if isinstance(e, dict)]
        for e in entries:
            for kind, rows in D(e.get("message")).items():
                if not isinstance(rows, list):
                    continue
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    transfers.append({
                        "newsId": e.get("id"), "date": e.get("date"), "kind": kind,
                        "player": D(r.get("tradable")).get("name"),
                        "playerId": D(r.get("tradable")).get("id"),
                        "price": r.get("price"),
                        "fromId": D(r.get("from")).get("id"), "from": D(r.get("from")).get("name"),
                        "toId": D(r.get("to")).get("id"), "to": D(r.get("to")).get("name"),
                    })
        if not D(page.get("newsList")).get("hasMore") or not entries:
            break
        start += 50
        time.sleep(0.3)

    # Strafen & Gutschriften (Communityleiter)
    bonuses, start = [], 0
    pat = re.compile(r"(Disziplinarstrafe|Gutschrift):\s*([\d.]+)\s+wurden\s+(.+?)\s+vom Communityleiter.*?(?:Begründung:\s*(.*))?$", re.S)
    while start < 300:
        page = get(links["game:news"] + f"?group=true&originaltypes=true&start={start}&limit=50&type=TRANSACTION_BONUS")
        groups = D(page.get("newsList")).get("groups") or {}
        entries = [e for g in (groups.values() if isinstance(groups, dict) else groups) for e in D(g).get("entries", []) if isinstance(e, dict)]
        for e in entries:
            text = D(e.get("message")).get("text") or ""
            m = pat.search(text.strip())
            if m:
                amount = int(m.group(2).replace(".", ""))
                bonuses.append({"newsId": e.get("id"), "date": e.get("date"),
                                "amount": -amount if m.group(1) == "Disziplinarstrafe" else amount,
                                "name": m.group(3).strip(), "reason": (m.group(4) or "").strip()})
        if not D(page.get("newsList")).get("hasMore") or not entries:
            break
        start += 50

    raw = {
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "me": {"id": me.get("id"), "name": me.get("firstName") or me.get("name"),
               "budget": me.get("budget"), "teamValue": me.get("teamValue"), "points": me.get("points")},
        "community": {"id": cid, "name": community.get("name")},
        "members": [{"id": m["id"], "name": (m.get("firstName") or m.get("login") or "").strip(), "login": m.get("login")}
                    for m in members.get("members", [])],
        "standings": [{
            "userId": i["_embedded"]["user"]["id"], "name": (i["_embedded"]["user"].get("name") or "").strip(),
            "points": i.get("totalPoints"), "lastPoints": i.get("lastPoints"),
            "teamValue": D(i["_embedded"].get("teamInfo")).get("teamValue"),
        } for i in standings.get("items", [])],
        "perennial": [{
            "userId": i["_embedded"]["user"]["id"], "name": (i["_embedded"]["user"].get("name") or "").strip(),
            "seasons": D(i["_embedded"].get("teamInfo")).get("seasons") or [],
        } for i in perennial.get("items", [])],
        "matchdays": matchdays,
        "squads": squads,
        "market": market_items,
        "nextTransfers": market.get("nextTransfersDateTime"),
        "statement": statement,
        "transfers": transfers,
        "bonuses": bonuses,
        "ownDetails": own_details,
        "clubLogos": club_logos,
        "dobs": dobs,
        "stats": stats,
        "quotes": quotes,
        "trends": trends,
        "lineup": lineup,
        "offers": offers,
        "credit": credit,
        "matchdayCalendar": mdays,
        "nextMatch": nm_info,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(raw, ensure_ascii=False, indent=1))
    # Bewusst keine Inhalte ausgeben – die Action-Logs eines öffentlichen Repos sind öffentlich.
    print(f"ok: {len(stats)} Spielerhistorien, {len(offers)} Angebote, {len(bonuses)} Strafen/Gutschriften, {len(raw['members'])} Manager, {len(matchdays)} Spieltage, {len(market_items)} Marktspieler, {len(transfers)} Transfers")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        print(f"Fehler: {e}", file=sys.stderr)
        print(f"::error title=Comunio-Abruf::{e}")  # als Annotation sichtbar, ohne Daten
        sys.exit(1)
    except Exception as e:
        import traceback
        tb = traceback.extract_tb(e.__traceback__)[-1]
        print(f"::error title=Comunio-Abruf::{type(e).__name__} in Zeile {tb.lineno}: {str(e)[:200]}")
        raise
