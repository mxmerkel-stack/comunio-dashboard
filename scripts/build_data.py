#!/usr/bin/env python3
"""Bereitet data/raw.json für die Website auf und pflegt die Verlaufsdatei data/history.json.

Ergebnis: data/dashboard.json (wird in die Seite eingebettet und dann verschlüsselt).
"""
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
CFG = json.loads((ROOT / "config.json").read_text())
COMPUTER_ID = 1


def num(x, default=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


def season_key(label):
    """'25/26' -> 2026, '09/10' -> 2010 (Endjahr der Saison, für Sortierung)."""
    try:
        end = int(str(label).split("/")[-1])
        return 2000 + end if end < 80 else 1900 + end
    except ValueError:
        return 0


def build(raw, history):
    ids = [str(m["id"]) for m in raw["members"]]
    names = {str(m["id"]): m["name"] for m in raw["members"]}
    for s in raw["standings"]:
        names.setdefault(str(s["userId"]), s["name"])
    me_id = str(raw["me"]["id"])
    season_start = CFG["season_start"]
    bonus = CFG.get("points_bonus_eur", 10000)

    # --- Aktuelle Tabelle ---
    table = sorted(raw["standings"], key=lambda s: -num(s["points"]))
    standings = [{"rank": i + 1, "id": str(s["userId"]), "name": names[str(s["userId"])],
                  "points": num(s["points"]), "teamValue": num(s["teamValue"])}
                 for i, s in enumerate(table)]
    leader_pts = standings[0]["points"] if standings else 0
    for s in standings:
        s["gap"] = leader_pts - s["points"]

    # --- Spieltage & Spieltagssiege ---
    md_wins = defaultdict(int)
    md_rows = []
    for md in sorted(raw["matchdays"], key=lambda m: num(m["key"])):
        pts = {str(i["userId"]): num(i["points"], None) for i in md["items"]}
        valid = {k: v for k, v in pts.items() if v is not None}
        best = max(valid.values()) if valid else None
        winners = [k for k, v in valid.items() if v == best] if best is not None else []
        for w in winners:
            md_wins[w] += 1
        md_rows.append({"key": num(md["key"]), "name": md["name"], "points": pts,
                        "winners": winners, "best": best})

    # --- Ewige Tabelle & Saisonsiege ---
    season_points = defaultdict(dict)  # label -> {id: pts}
    for p in raw["perennial"]:
        for s in p["seasons"]:
            season_points[s["year"]][str(p["userId"])] = num(s["points"])
    season_titles = defaultdict(int)
    seasons = []
    for label in sorted(season_points, key=season_key):
        pts = season_points[label]
        played = {k: v for k, v in pts.items() if v > 0}
        if not played:
            continue
        best = max(played.values())
        winners = [k for k, v in played.items() if v == best]
        for w in winners:
            season_titles[w] += 1
        seasons.append({"label": label, "points": pts, "winners": winners, "best": best})

    alltime = []
    for i in ids:
        past = sum(v.get(i, 0) for v in season_points.values())
        cur = next((s["points"] for s in standings if s["id"] == i), 0)
        n_played = sum(1 for v in season_points.values() if v.get(i, 0) > 0)
        alltime.append({"id": i, "name": names[i], "points": past + cur, "past": past,
                        "current": cur, "seasons": n_played,
                        "avg": round(past / n_played) if n_played else None})
    alltime.sort(key=lambda r: -r["points"])
    for n, r in enumerate(alltime):
        r["rank"] = n + 1

    wins = [{"id": i, "name": names[i], "titles": season_titles.get(i, 0),
             "matchdayWins": md_wins.get(i, 0)} for i in ids]
    wins.sort(key=lambda r: (-r["titles"], -r["matchdayWins"]))

    # --- Kontostände (Schätzung aus Startkapital, Transfers, Punkteprämie) ---
    tx = [t for t in raw["transfers"] if (t.get("date") or "")[:10] >= season_start]
    seen, tx_unique = set(), []
    for t in tx:
        key = (t["newsId"], t["playerId"], t["fromId"], t["toId"], t["price"])
        if key not in seen:
            seen.add(key)
            tx_unique.append(t)
    sales, buys = defaultdict(int), defaultdict(int)
    for t in tx_unique:
        f, to, price = str(t["fromId"]), str(t["toId"]), num(t["price"])
        if f in names and num(f) != COMPUTER_ID:
            sales[f] += price
        if to in names and num(to) != COMPUTER_ID:
            buys[to] += price
    accounts = []
    for i in ids:
        start = num(CFG["start_capital"].get(i, CFG["start_capital"]["default"]))
        pts = next((s["points"] for s in standings if s["id"] == i), 0)
        est = start + sales[i] - buys[i] + pts * bonus
        real = num(raw["me"]["budget"]) if i == me_id else None
        tv = next((s["teamValue"] for s in standings if s["id"] == i), 0)
        accounts.append({"id": i, "name": names[i], "start": start, "sales": sales[i], "buys": buys[i],
                         "bonus": pts * bonus, "estimate": est, "real": real,
                         "balance": real if real is not None else est,
                         "teamValue": tv, "total": (real if real is not None else est) + tv})
    accounts.sort(key=lambda a: -a["balance"])
    me_acc = next(a for a in accounts if a["id"] == me_id)
    me_acc["deviation"] = me_acc["real"] - me_acc["estimate"]

    transfers_out = [{"date": t["date"], "player": t["player"], "price": num(t["price"]),
                      "from": names.get(str(t["fromId"]), "Computer"),
                      "to": names.get(str(t["toId"]), "Computer")}
                     for t in sorted(tx_unique, key=lambda t: t["date"] or "", reverse=True)]

    # --- Transfermarkt mit Gebotsempfehlung (Faustregel) ---
    bidcfg = CFG.get("bid", {})
    base_p, max_p = bidcfg.get("base_premium", 0.02), bidcfg.get("max_premium", 0.12)
    rivals_cash = sorted((a["balance"] for a in accounts if a["id"] != me_id), reverse=True)
    best_rival = rivals_cash[0] if rivals_cash else 0
    market = []
    ppm = [num(m["points"]) / max(num(m["marketValue"]), 1) * 1e6 for m in raw["market"]]
    ppm_sorted = sorted(ppm)
    for m, v in zip(raw["market"], ppm):
        mv = num(m["marketValue"])
        rank = ppm_sorted.index(v) / max(len(ppm_sorted) - 1, 1)  # 0..1 (1 = beste Punkte/Mio.)
        premium = base_p + (max_p - base_p) * rank
        if (m.get("status") or "ACTIVE") != "ACTIVE":
            premium = 0
        bid = int(round(mv * (1 + premium) / 1000) * 1000)
        own_cash = me_acc["balance"] - bidcfg.get("keep_reserve_eur", 0)
        market.append({**m, "marketValue": mv, "points": num(m["points"]),
                       "pointsPerMio": round(v, 1), "premium": round(premium * 100, 1),
                       "bid": bid, "affordable": bid <= own_cash,
                       "rivalCanOutbid": best_rival >= bid})
    market.sort(key=lambda m: -m["pointsPerMio"])

    # --- Verlauf (ein Eintrag pro Tag) ---
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snap = {"date": today,
            "balance": {a["id"]: a["balance"] for a in accounts},
            "points": {s["id"]: s["points"] for s in standings},
            "teamValue": {s["id"]: s["teamValue"] for s in standings}}
    history = [h for h in history if h["date"] != today] + [snap]
    history.sort(key=lambda h: h["date"])

    me_rank = next(s for s in standings if s["id"] == me_id)
    return {
        "generatedAt": raw["fetchedAt"],
        "season": CFG.get("season_label"),
        "community": raw["community"]["name"],
        "me": {"id": me_id, "name": names[me_id], "rank": me_rank["rank"], "points": me_rank["points"],
               "gap": me_rank["gap"], "balance": me_acc["balance"], "teamValue": me_rank["teamValue"],
               "deviation": me_acc["deviation"]},
        "managers": [{"id": i, "name": names[i]} for i in ids],
        "standings": standings,
        "matchdays": md_rows,
        "alltime": alltime,
        "seasons": seasons,
        "wins": wins,
        "accounts": accounts,
        "transfers": transfers_out[:200],
        "market": market,
        "nextTransfers": raw.get("nextTransfers"),
        "statement": raw["statement"][:100],
        "squads": raw["squads"],
        "history": history,
    }, history


def _fernet():
    """Verlauf wird verschlüsselt zwischengespeichert (Schlüssel aus PAGE_PASSWORD)."""
    pw = os.environ.get("PAGE_PASSWORD")
    if not pw:
        return None
    import base64
    import hashlib
    from cryptography.fernet import Fernet
    key = hashlib.pbkdf2_hmac("sha256", pw.encode(), b"comunio-zentrale-history", 200_000)
    return Fernet(base64.urlsafe_b64encode(key))


def load_history():
    f, enc, plain = _fernet(), DATA / "history.enc", DATA / "history.json"
    try:
        if f and enc.exists():
            return json.loads(f.decrypt(enc.read_bytes()))
        if plain.exists():
            return json.loads(plain.read_text())
    except Exception:
        print("Hinweis: Verlauf nicht lesbar (z. B. Passwort geändert) – starte neu.")
    return []


def save_history(history):
    f, data = _fernet(), json.dumps(history, ensure_ascii=False).encode()
    if f:
        (DATA / "history.enc").write_bytes(f.encrypt(data))
    else:
        (DATA / "history.json").write_bytes(data)


def main():
    raw = json.loads((DATA / "raw.json").read_text())
    history = load_history()
    dash, history = build(raw, history)
    save_history(history)
    (DATA / "dashboard.json").write_text(json.dumps(dash, ensure_ascii=False))
    print(f"ok: dashboard.json gebaut, {len(history)} Verlaufstage")


if __name__ == "__main__":
    main()
