#!/usr/bin/env python3
"""Erzeugt ein erfundenes data/raw.json im Format von fetch_comunio.py – zum Testen ohne Comunio-Zugang."""
import json
import os
import random
from pathlib import Path

random.seed(7)
DATA = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
members = [{"id": 5695415, "name": "Testmanager"}, {"id": 101, "name": "Adler"}, {"id": 102, "name": "Bauer"},
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
           "recommendedPrice": "-1", "trend": "UP", "status": random.choice(["ACTIVE"] * 5 + ["INJURED"]),
           "owner": random.choice([None, "Adler"]), "remainingDays": random.randint(1, 13), "date": "2026-10-21T04:34:45+0200"}
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
                          "status": "ACTIVE", "purchasePrice": random.randint(5, 150) * 100000, "purchaseDate": "2026-08-01T00:00:00+02:00"}
                         for j in range(11)] for m in members}
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
}
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1))
print("sample raw.json geschrieben")
