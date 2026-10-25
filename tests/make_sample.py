#!/usr/bin/env python3
"""Erzeugt ein erfundenes data/raw.json im Format von fetch_comunio.py – zum Testen ohne Comunio-Zugang."""
import json
import os
import random
from pathlib import Path

random.seed(7)
DATA = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
members = [{"id": 5695415, "name": "Testmanager", "login": "test1"}, {"id": 101, "name": "Adler"}, {"id": 102, "name": "Bauer"},
           {"id": 103, "name": "Lindner"}, {"id": 104, "name": "Peters"}, {"id": 105, "name": "Kaiser"}]
md = []
tot = {m["id"]: 0 for m in members}
for k in range(1, 7):
    items = []
    for m in members:
        p = random.randint(15, 70)
        tot[m["id"]] += p
        items.append({"userId": m["id"], "points": p, "total": tot[m["id"]]})
    md.append({"id": 400000 + k, "key": k, "name": f"{k}. Spieltag", "items": items})
seasons = ["25/26", "24/25", "23/24", "22/23", "21/22"]
perennial = [{"userId": m["id"], "name": m["name"],
              "seasons": [{"eventId": 0, "year": s, "points": random.randint(800, 1250)} for s in seasons]}
             for m in members]
players = ["Müller", "Kimmich", "Wirtz", "Musiala", "Tah", "Baumann", "Undav", "Schlotterbeck", "Raum", "Andrich"]
market = [{"id": 30000 + i, "name": n, "club": "Testverein", "position": random.choice(["keeper", "defender", "midfielder", "striker"]),
           "points": str(random.randint(0, 60)), "marketValue": str(random.randint(5, 150) * 100000),
           "recommendedPrice": "-1", "trend": random.choice([-5, -3, -1, 1, 2, 4, 5, None]), "status": random.choice(["ACTIVE"] * 5 + ["INJURED"]),
           "statusInfo": "", "seasonPoints": {"24/25": str(random.randint(0, 150)), "25/26": str(random.randint(0, 180))},
           "owner": random.choice([None, None, "Adler"]), "remainingDays": random.randint(1, 13), "date": "2026-10-21T04:34:45+0200"}
          for i, n in enumerate(players)]
transfers = []
for i in range(25):
    buyer, seller = random.sample(members, 2)
    kind = random.choice(["FROM_COMPUTER", "TO_COMPUTER", "USER_TO_USER"])
    frm = {"id": 1, "name": "Computer"} if kind == "FROM_COMPUTER" else {"id": seller["id"], "name": seller["name"]}
    to = {"id": 1, "name": "Computer"} if kind == "TO_COMPUTER" else {"id": buyer["id"], "name": buyer["name"]}
    transfers.append({"newsId": 900 + i, "date": f"2026-{random.choice(['08','09','10'])}-{random.randint(1,28):02d}T04:55:00+02:00",
                      "kind": kind, "player": random.choice(players), "playerId": 30000 + i,
                      "price": random.randint(3, 120) * 100000, "fromId": frm["id"], "from": frm["name"],
                      "toId": to["id"], "to": to["name"]})
squads = {str(m["id"]): [{"id": 31000 + j, "name": random.choice(players), "club": "Testverein", "position": "midfielder",
                          "points": str(random.randint(0, 50)), "avg": "3.5", "marketValue": random.randint(5, 150) * 100000,
                          "status": random.choice(["ACTIVE"]*6+["REHABILITATION"]), "statusInfo": "Reha nach Muskelverletzung", "purchasePrice": random.randint(5, 150) * 100000, "purchaseDate": "2026-08-01T00:00:00+02:00"}
                         for j in range(11)] for m in members}
# Spielstatistiken, Aufstellung, Angebote, Kalender
pos_cycle = ["keeper", "defender", "defender", "defender", "defender", "midfielder", "midfielder", "midfielder",
             "midfielder", "striker", "striker", "striker"]
clubs = [(1, "FC Bayern München"), (3, "Borussia Mönchengladbach"), (6, "SV Werder Bremen"), (13, "1. FC Köln"),
         (14, "VfB Stuttgart"), (21, "Sport-Club Freiburg")]
stats = {}
for uid, sq in squads.items():
    for j, p in enumerate(sq):
        p["id"] = int(uid) * 100 + j
        p["position"] = pos_cycle[j % len(pos_cycle)]
        cid, cname = random.choice(clubs)
        oid, oname = random.choice([c for c in clubs if c[0] != cid])
        p.update(clubId=cid, club=cname, onMarket=False, minBid=-1, linedup=j < 11,
                 nextMatch={"homeId": cid, "home": cname, "guestId": oid, "guest": oname, "kickoff": "2026-10-24T15:30:00+02:00"})
sq_extra = {"id": 5695415 * 100 + 11, "name": "Ersatzmann", "club": "Testverein", "position": "striker", "points": "5",
            "avg": "1", "marketValue": 900000, "status": "ACTIVE", "statusInfo": "", "purchasePrice": 1500000,
            "purchaseDate": "2026-08-01", "clubId": 1, "onMarket": True, "minBid": 800000, "linedup": False, "nextMatch": {}}
squads["5695415"].append(sq_extra)
all_players = [p for sq in squads.values() for p in sq] + market
for i, m in enumerate(market):
    m["clubId"], m["club"] = clubs[i % len(clubs)]
    m["ownerId"] = 101 if m["owner"] == "Adler" else 1
# Spielplan: Jeder gegen jeden (Rundenverfahren), Tore nach Vereinsstärke; Punkte hängen an Tordifferenz
import math
strength = {cid: random.gauss(0, 0.6) for cid, _ in clubs}
def rounds(ids):
    ids = list(ids); out = []
    for r in range(len(ids) - 1):
        out.append([(ids[i], ids[-1 - i]) if r % 2 else (ids[-1 - i], ids[i]) for i in range(len(ids) // 2)])
        ids = [ids[0]] + [ids[-1]] + ids[1:-1]
    return out
rr = rounds([c for c, _ in clubs])
def pois(l):
    k, t, L = 0, random.random(), math.exp(-l)
    while t > L:
        k += 1; t *= random.random()
    return k
games = {}
mid = 1
for season, n in (("cur", 7), (397004, 34)):
    for mdk in range(1, n + 1):
        for h, g in rr[(mdk - 1) % len(rr)]:
            d = strength[h] - strength[g] + 0.3
            games[(season, mdk, h)] = games[(season, mdk, g)] = (mid, h, g, pois(1.5 + d / 2), pois(1.5 - d / 2))
            mid += 1
club_shock = {k: random.gauss(0, 1.5) for k in games}
for p in all_players:
    rows = []
    talent = random.gauss(0, 1.5)
    p_start = random.choice([0.2, 0.5, 0.8, 0.9])
    cid = p.get("clubId", 1)
    for season, n in (("cur", 7), (397004, 34)):
        for mdk in range(n, 0, -1):
            if season == "cur" and mdk == 7:
                continue  # 7. Spieltag läuft noch
            m_id, h, g, hg, gg = games[(season, mdk, cid)]
            gd = (hg - gg) if cid == h else (gg - hg)
            r = random.random()
            unav = r < 0.1
            start = not unav and random.random() < p_start
            sub = not unav and not start and random.random() < 0.4
            pts = None
            if start:
                pts = round(4 + talent + 0.8 * (gd - strength[cid]) + club_shock[(season, mdk, cid)] + random.gauss(0, 4))
            elif sub:
                pts = round(1 + 0.4 * talent + 0.25 * gd + random.gauss(0, 2))
            rows.append({"season": season, "md": str(mdk), "date": "2026-10-01", "points": None if pts is None else str(pts),
                         "in": "60" if sub else None, "out": None, "status": "INJURED" if unav else "ACTIVE",
                         "yellow": "1" if random.random() < 0.1 else None, "yellowRed": None, "red": None,
                         "goals": None, "clubId": cid, "homeId": h, "guestId": g, "matchId": m_id,
                         "homeGoals": hg, "guestGoals": gg, "kickoff": "2026-10-01T15:30:00+02:00"})
    stats[str(p["id"])] = rows
season_fixtures = [{"md": mdk, "home": dict(clubs)[h], "away": dict(clubs)[g], "kickoff": "", "finished": mdk < 7,
                    "goalsHome": None, "goalsAway": None}
                   for mdk in range(1, 35) for (h, g) in (rr[(mdk - 1) % len(rr)] if mdk <= 17 else [(b_, a_) for a_, b_ in rr[(mdk - 18) % len(rr)]])]
quotes = {str(p["id"]): [[f"2026-10-{23 - k:02d}", int(p["marketValue"]) + k * 10000] for k in range(23)]
          for p in squads["5695415"] + market}
own_ids = [str(p["id"]) for p in squads["5695415"][:11]]
offers = [{"id": k, "type": "SALE", "state": "PENDING", "price": int(int(p["marketValue"]) * random.uniform(0.95, 1.1)),
           "date": "2026-10-23T06:26:10+02:00", "playerId": p["id"], "player": p["name"], "marketValue": p["marketValue"],
           "fromId": 1, "from": "Computer", "toId": 5695415} for k, p in enumerate(squads["5695415"][:6])]
offers.append({**offers[0], "id": 99, "fromId": 102, "from": "Bauer", "price": offers[0]["price"] + 200000})
calendar = [{"key": str(k), "date": f"2026-{8 + (k - 1) // 3:02d}-{(k * 7) % 27 + 1:02d}T20:30:00+02:00",
             "started": k <= 7, "finished": k <= 6, "season": "26/27"} for k in range(1, 11)]
raw = {
    "fetchedAt": "2026-10-23T18:30:00+00:00",
    "me": {"id": "5695415", "name": "Testmanager", "budget": "496499", "teamValue": "61540000", "points": str(tot[5695415])},
    "community": {"id": "1", "name": "Testliga"},
    "members": members,
    "standings": [{"userId": m["id"], "name": m["name"], "points": tot[m["id"]], "lastPoints": 30,
                   "teamValue": random.randint(40, 80) * 1000000} for m in members],
    "perennial": perennial, "matchdays": md, "squads": squads, "market": market,
    "nextTransfers": "2026-10-24T03:00:00+02:00",
    "statement": [{"date": "2026-10-23T04:55:03+02:00", "type": "TRANSFER", "amount": 3224000, "text": "Müller"}],
    "transfers": transfers,
    "bonuses": [{"newsId": 1, "date": "2026-05-25T15:31:00+02:00", "amount": -9000000, "name": "Testmanager", "reason": "Saisonübergang"},
                {"newsId": 2, "date": "2026-09-02T10:00:00+02:00", "amount": -500000, "name": "Adler", "reason": "Aufstellung vergessen"},
                {"newsId": 3, "date": "2026-09-03T10:00:00+02:00", "amount": 250000, "name": "Bauer", "reason": "Tippspiel"}],
    "ownDetails": {str(p["id"]): {"dob": "1999-05-01T00:00:00+02:00", "seasonPoints": {}} for p in squads["5695415"]},
    "dobs": {str(p["id"]): f"{1990 + (p["id"] % 14)}-0{1 + p["id"] % 9}-15T00:00:00+02:00" for sq in squads.values() for p in sq},
    "clubLogos": {"21": "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxMCAxMCI+PHJlY3Qgd2lkdGg9IjEwIiBoZWlnaHQ9IjEwIiBmaWxsPSJibHVlIi8+PC9zdmc+", "1": "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxMCAxMCI+PGNpcmNsZSBjeD0iNSIgY3k9IjUiIHI9IjQiIGZpbGw9InJlZCIvPjwvc3ZnPg=="},
    "stats": stats, "quotes": quotes, "lineup": {"tactic": "433", "ids": own_ids}, "offers": offers,
    "credit": 15881499, "matchdayCalendar": calendar,
    "clubs": {str(c): n for c, n in clubs}, "seasonFixtures": season_fixtures,
    "playerPositions": {str(p["id"]): p["position"] for p in all_players},
    "nextMatch": {"md": 7, "date": "2026-10-23T20:30:00+02:00", "started": True,
                  "fixtures": [{"home": h, "away": a, "kickoff": f"2026-10-2{4 + i // 3}T15:30:00Z", "finished": i == 0,
                                "goalsHome": 2 if i == 0 else None, "goalsAway": 1 if i == 0 else None, "source": "OpenLigaDB"}
                               for i, (h, a) in enumerate([("FC Bayern München", "Sport-Club Freiburg"), ("1. FC Köln", "VfB Stuttgart"),
                                                          ("SV Werder Bremen", "Borussia Mönchengladbach")])],
                  "odds": [{"home": "Bayern Munich", "away": "SC Freiburg", "kickoff": "", "bookmaker": "Tipico (DE)", "odd1": 1.3, "oddX": 6.0, "odd2": 9.0},
                           {"home": "FC Koln", "away": "VfB Stuttgart", "kickoff": "", "bookmaker": "Tipico (DE)", "odd1": 3.1, "oddX": 3.5, "odd2": 2.2}]},
}
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1))
print("sample raw.json geschrieben")
