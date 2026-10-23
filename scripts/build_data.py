#!/usr/bin/env python3
"""Bereitet data/raw.json für die Website auf und pflegt den (verschlüsselten) Verlauf.

Ergebnis: data/dashboard.json (wird in die Seite eingebettet und dann verschlüsselt).
Zusätzliche Recherche (Gerüchte, Verletzungen) kommt aus research/*.json.
"""
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
CFG = json.loads((ROOT / "config.json").read_text())
COMPUTER_ID = 1
POSITIONS = {"keeper": "Torwart", "defender": "Abwehr", "midfielder": "Mittelfeld", "striker": "Sturm"}
STATUS = {"ACTIVE": "fit", "INJURED": "verletzt", "WEAKENED": "angeschlagen", "REHABILITATION": "Reha",
          "SUSPENDED": "gesperrt/freigestellt", "REDBANNED": "Rot-Sperre", "YELLOWREDBANNED": "Gelb-Rot-Sperre",
          "YELLOWBANNED": "Gelb-Sperre", "AWAY": "abwesend", "RETIRED": "nicht mehr in der Liga"}


def num(x, default=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


def norm(name):
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


def season_key(label):
    """'25/26' -> 2026, '09/10' -> 2010 (Endjahr der Saison, für Sortierung)."""
    try:
        end = int(str(label).split("/")[-1])
        return 2000 + end if end < 80 else 1900 + end
    except ValueError:
        return 0


def load_research(name):
    p = ROOT / "research" / f"{name}.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text())
    return {str(k): v for k, v in data.get("players", {}).items()}, data.get("asOf")


def build(raw, history):
    ids = [str(m["id"]) for m in raw["members"]]
    names = {str(m["id"]): m["name"] for m in raw["members"]}
    for s in raw["standings"]:  # Tabelle führt den vollen Anzeigenamen (z. B. "Dr. Schmitt")
        if s.get("name"):
            names[str(s["userId"])] = s["name"].strip()
    me_id = str(raw["me"]["id"])
    bonus_per_point = CFG.get("points_bonus_eur", 10000)

    def id_for(name):
        n = norm(name)
        for i in ids:
            if norm(names[i]) == n:
                return i
        for m in raw["members"]:
            if n in (norm(m.get("name")), norm(m.get("login"))):
                return str(m["id"])
        for i in ids:  # Kurzformen wie "Dr." / "Dr. Schmitt"
            if n and (norm(names[i]).startswith(n) or n.startswith(norm(names[i]))):
                return i
        return None

    # --- Aktuelle Tabelle ---
    table = sorted(raw["standings"], key=lambda s: -num(s["points"]))
    standings = [{"rank": i + 1, "id": str(s["userId"]), "name": names[str(s["userId"])],
                  "points": num(s["points"]), "teamValue": num(s["teamValue"])}
                 for i, s in enumerate(table)]
    leader_pts = standings[0]["points"] if standings else 0
    for s in standings:
        s["gap"] = leader_pts - s["points"]
    pts_of = {s["id"]: s["points"] for s in standings}
    tv_of = {s["id"]: s["teamValue"] for s in standings}

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

    # --- Ewige Tabelle, Meister & Saisonsiege ---
    season_points = defaultdict(dict)
    for p in raw["perennial"]:
        for s in p["seasons"]:
            if isinstance(s, dict):
                season_points[s.get("year")][str(p["userId"])] = num(s.get("points"))
    champions_cfg = {k: v for k, v in CFG.get("champions", {}).items() if not k.startswith("_")}
    labels = set(season_points) | set(champions_cfg)
    titles = defaultdict(int)  # Schlüssel: Manager-ID oder "ehemalig:Name"
    seasons = []
    for label in sorted(labels, key=season_key):
        pts = season_points.get(label, {})
        if label in champions_cfg:
            champ = champions_cfg[label]
            cid = id_for(champ)
            winners = [cid] if cid else []
            winner_names = [names[cid] if cid else champ]
            key = cid or "ehemalig:" + champ
            titles[key] += 1
            source = "manuell"
        else:
            played = {k: v for k, v in pts.items() if v > 0}
            if not played:
                continue
            best = max(played.values())
            winners = [k for k, v in played.items() if v == best]
            winner_names = [names[w] for w in winners if w in names]
            for w in winners:
                titles[w] += 1
            source = "Punkte"
        seasons.append({"label": label, "points": pts, "winners": winners,
                        "winnerNames": winner_names, "source": source})

    alltime = []
    for i in ids:
        past = sum(v.get(i, 0) for v in season_points.values())
        cur = pts_of.get(i, 0)
        n_played = sum(1 for v in season_points.values() if v.get(i, 0) > 0)
        alltime.append({"id": i, "name": names[i], "points": past + cur, "past": past, "current": cur,
                        "seasons": n_played, "titles": titles.get(i, 0),
                        "avg": round(past / n_played) if n_played else None})
    alltime.sort(key=lambda r: -r["points"])
    for n, r in enumerate(alltime):
        r["rank"] = n + 1

    wins = [{"id": i, "name": names[i], "titles": titles.get(i, 0), "matchdayWins": md_wins.get(i, 0),
             "former": False} for i in ids]
    for key, n in titles.items():
        if key.startswith("ehemalig:"):
            wins.append({"id": key, "name": key.split(":", 1)[1] + " (ehemalig)", "titles": n,
                         "matchdayWins": 0, "former": True})
    wins.sort(key=lambda r: (-r["titles"], -r["matchdayWins"]))

    # --- Transfers: Startlücke aus config + Comunio-News, ohne Doppelte ---
    tx_from = CFG.get("news_transfers_from", CFG.get("season_start", "2000-01-01"))
    tx = [dict(t, newsId="seed", playerId=None) for t in CFG.get("seed_transfers", [])]
    tx += [t for t in raw["transfers"] if (t.get("date") or "")[:10] >= tx_from]
    seen, tx_unique = set(), []
    for t in tx:
        key = (t.get("newsId"), t.get("player"), str(t.get("fromId")), str(t.get("toId")), num(t.get("price")))
        if key not in seen:
            seen.add(key)
            tx_unique.append(t)

    sales, buys = defaultdict(int), defaultdict(int)
    tf_stats = {i: {"id": i, "name": names[i], "buys": 0, "sells": 0, "buySum": 0, "sellSum": 0,
                    "fromComputer": 0, "toComputer": 0, "betweenManagers": 0} for i in ids}
    for t in tx_unique:
        f, to, price = str(t.get("fromId")), str(t.get("toId")), num(t.get("price"))
        if f in tf_stats:
            sales[f] += price
            st = tf_stats[f]
            st["sells"] += 1
            st["sellSum"] += price
            st["toComputer" if to == str(COMPUTER_ID) else "betweenManagers"] += 1
        if to in tf_stats:
            buys[to] += price
            st = tf_stats[to]
            st["buys"] += 1
            st["buySum"] += price
            st["fromComputer" if f == str(COMPUTER_ID) else "betweenManagers"] += 1
    for st in tf_stats.values():
        st["total"] = st["buys"] + st["sells"]
        st["net"] = st["sellSum"] - st["buySum"]
    transfer_stats = sorted(tf_stats.values(), key=lambda s: -s["total"])

    # --- Strafen & Gutschriften nach dem Saisonübergang ---
    ref = CFG.get("balance_reference", {})
    ref_after = ref.get("after", "1970-01-01")
    base = num(ref.get("base_eur", 15000000))
    bonus_sum = defaultdict(int)
    bonus_rows = []
    for b in raw.get("bonuses", []):
        bid = id_for(b.get("name"))
        counted = (b.get("date") or "") > ref_after
        if bid and counted:
            bonus_sum[bid] += num(b.get("amount"))
        bonus_rows.append({"date": b.get("date"), "name": names.get(bid, b.get("name")), "amount": num(b.get("amount")),
                           "reason": b.get("reason"), "counted": bool(bid and counted)})
    bonus_rows.sort(key=lambda r: r["date"] or "", reverse=True)

    accounts = []
    for i in ids:
        pts = pts_of.get(i, 0)
        est = base + sales[i] - buys[i] + pts * bonus_per_point + bonus_sum[i]
        real = num(raw["me"]["budget"]) if i == me_id else None
        balance = real if real is not None else est
        accounts.append({"id": i, "name": names[i], "base": base, "sales": sales[i], "buys": buys[i],
                         "pointsBonus": pts * bonus_per_point, "penalties": bonus_sum[i], "estimate": est,
                         "real": real, "balance": balance, "teamValue": tv_of.get(i, 0),
                         "total": balance + tv_of.get(i, 0)})
    accounts.sort(key=lambda a: -a["balance"])
    me_acc = next(a for a in accounts if a["id"] == me_id)
    me_acc["deviation"] = me_acc["real"] - me_acc["estimate"]
    balance_of = {a["id"]: a["balance"] for a in accounts}

    transfers_out = [{"date": t.get("date"), "player": t.get("player"), "price": num(t.get("price")),
                      "from": names.get(str(t.get("fromId")), "Computer"),
                      "to": names.get(str(t.get("toId")), "Computer")}
                     for t in sorted(tx_unique, key=lambda t: t.get("date") or "", reverse=True)]

    # --- Recherche (Gerüchte, Verletzungen) ---
    rumors, rumors_asof = load_research("rumors") or ({}, None)
    injuries, injuries_asof = load_research("injuries") or ({}, None)

    def enrich(p):
        pid = str(p.get("id"))
        out = {"positionName": POSITIONS.get(p.get("position"), p.get("position") or "–"),
               "statusName": STATUS.get(p.get("status") or "ACTIVE", p.get("status"))}
        if pid in rumors:
            out["rumor"] = rumors[pid]
        if pid in injuries:
            out["injury"] = injuries[pid]
        return out

    # --- Transfermarkt mit Gebots- und Kaufempfehlung ---
    bidcfg = CFG.get("bid", {})
    base_p, max_p = bidcfg.get("base_premium", 0.02), bidcfg.get("max_premium", 0.12)
    own_cash = me_acc["balance"] - bidcfg.get("keep_reserve_eur", 0)
    prev_label = None
    all_seasons = set()
    for m in raw["market"]:
        all_seasons |= set((m.get("seasonPoints") or {}).keys())
    if all_seasons:
        prev_label = max(all_seasons, key=season_key)

    market = []
    for m in raw["market"]:
        mv = num(m.get("marketValue"))
        pts = num(m.get("points"))
        prev = (m.get("seasonPoints") or {}).get(prev_label) if prev_label else None
        prev_n = num(prev, None)
        # Wert: aktuelle Punkte doppelt gewichtet + Vorsaison auf 6 Spieltage heruntergerechnet
        md_played = max(len(md_rows), 1)
        form = pts * 2 + (prev_n or 0) * md_played / 34
        market.append({**m, "marketValue": mv, "points": pts, "prevPoints": prev_n, "form": form,
                       "pointsPerMio": round(pts / max(mv, 1) * 1e6, 1), **enrich(m)})
    ranked = sorted(market, key=lambda m: m["form"] / max(m["marketValue"], 1))
    for idx, m in enumerate(ranked):
        r = idx / max(len(ranked) - 1, 1)  # 0..1, 1 = bestes Verhältnis
        status = m.get("status") or "ACTIVE"
        premium = 0 if status not in ("ACTIVE", "WEAKENED") else base_p + (max_p - base_p) * r
        bid = int(round(m["marketValue"] * (1 + premium) / 1000) * 1000)
        rivals = [names[i] for i in ids if i != me_id and balance_of.get(i, 0) >= bid]
        computer = (m.get("owner") or "Computer") == "Computer"
        reasons = []
        if status in ("INJURED", "REHABILITATION", "SUSPENDED") or status.endswith("BANNED"):
            rec = "nein"
            reasons.append(STATUS.get(status, status))
        elif r >= 0.66 and m["points"] > 0:
            rec = "ja"
            reasons.append("starkes Punkte/Preis-Verhältnis")
        elif r >= 0.33:
            rec = "vielleicht"
            reasons.append("durchschnittliches Punkte/Preis-Verhältnis")
        else:
            rec = "nein"
            reasons.append("schwaches Punkte/Preis-Verhältnis")
        if status == "WEAKENED" and rec == "ja":
            rec = "vielleicht"
            reasons.append("angeschlagen")
        if bid > own_cash:
            reasons.append("übersteigt dein Guthaben")
            if rec == "ja":
                rec = "vielleicht"
        m.update({"premium": round(premium * 100, 1), "bid": bid, "affordable": bid <= own_cash,
                  "rivals": rivals, "recommendation": rec, "reason": ", ".join(reasons), "fromComputer": computer,
                  "rank": r})
    market.sort(key=lambda m: -m["rank"])

    # --- Kader ---
    squads = {}
    for uid, players in raw["squads"].items():
        squads[uid] = [{**p, **enrich(p), "marketValue": num(p.get("marketValue")),
                        "purchasePrice": num(p.get("purchasePrice"), None)} for p in players]

    # --- Verlauf (ein Eintrag pro Tag) ---
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snap = {"date": today, "balance": dict(balance_of), "points": dict(pts_of), "teamValue": dict(tv_of)}
    history = [h for h in history if h["date"] != today] + [snap]
    history.sort(key=lambda h: h["date"])

    me_rank = next(s for s in standings if s["id"] == me_id)
    return {
        "generatedAt": raw["fetchedAt"],
        "season": CFG.get("season_label"),
        "prevSeason": prev_label,
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
        "bonuses": bonus_rows,
        "transfers": transfers_out,
        "transferStats": transfer_stats,
        "market": market,
        "nextTransfers": raw.get("nextTransfers"),
        "statement": raw["statement"][:100],
        "squads": squads,
        "research": {"rumorsAsOf": rumors_asof, "injuriesAsOf": injuries_asof},
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
