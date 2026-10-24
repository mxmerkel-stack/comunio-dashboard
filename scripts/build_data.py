#!/usr/bin/env python3
"""Bereitet data/raw.json für die Website auf und pflegt den (verschlüsselten) Verlauf.

Ergebnis: data/dashboard.json (wird in die Seite eingebettet und dann verschlüsselt).
Zusätzliche Recherche (Gerüchte, Verletzungen) kommt aus research/*.json.
"""
import json
import os
import re
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import model as M

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
        dob = (raw.get("dobs") or {}).get(pid) or p.get("dob") or ((raw.get("ownDetails") or {}).get(pid) or {}).get("dob")
        if dob:
            out["age"] = M.age(dob)
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

    # =============== Analysen aus der Comunio Zentrale ===============
    an = analyze(raw, names, ids, me_id, squads, market, md_rows, pts_of, balance_of, injuries, tx_unique, own_cash)
    # Prüfung vor dem Aufstellungsschluss (auch Grundlage der Morgen-Mail)
    import checks as C
    an["checks"] = C.lineup_checks(raw, an.get("nextMatch") or {}, me_acc["balance"], an.get("offers"), an.get("ownSquad") or [])
    warn_actions = [{"tag": "Warnung", "title": i["title"], "detail": i["detail"]}
                    for i in an["checks"]["issues"] if i["level"] == "warn"]
    an["actions"] = warn_actions + [a for a in an["actions"] if not (warn_actions and a["tag"] == "Konto")]
    for n_, a_ in enumerate(an["actions"]):
        a_["prio"] = n_ + 1

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
        "clubLogos": {p.get("club"): (raw.get("clubLogos") or {}).get(str(p.get("clubId")))
                      for ps in list(raw["squads"].values()) + [raw["market"]] for p in ps
                      if p.get("club") and (raw.get("clubLogos") or {}).get(str(p.get("clubId")))},
        "history": history,
        **an,
    }, history


def analyze(raw, names, ids, me_id, squads, market, md_rows, pts_of, balance_of, injuries, tx, own_cash):
    """Prognosen, Aufstellung, Angebote, Flips, Käufer, Pläne, Saisonprognose, Monatswertung, Empfehlungen."""
    fetched = raw.get("fetchedAt") or datetime.now(timezone.utc).isoformat()
    cal = [c for c in (raw.get("matchdayCalendar") or []) if isinstance(c, dict)]
    finished = [int(c["key"]) for c in cal if c.get("finished") and str(c.get("key", "")).isdigit()]
    played = max(finished) if finished else len(md_rows)
    remaining = max(M.SEASON_MDS - played, 0)
    next_md = played + 1 if remaining else None
    next_date = next((c.get("date") for c in cal if str(c.get("key")) == str(next_md)), None)

    # --- Spieler-Modell ---
    players = {}
    for uid, sq in squads.items():
        for p in sq:
            players[str(p["id"])] = {**p, "ownerId": uid}
    for m in market:
        players.setdefault(str(m["id"]), {**m})
    positions = {pid: p.get("position") for pid, p in players.items()}
    histories = {pid: M.parse_history(rows) for pid, rows in (raw.get("stats") or {}).items()}
    means = M.position_means(histories, positions)
    base = {pid: M.player_base(histories.get(pid, []), positions.get(pid), means, players[pid].get("status"))
            for pid in players}
    opp, venue = M.opponent_factors(histories, base)
    model = M.enrich_players(players, histories, means, opp, venue, remaining,
                             {k: v for k, v in (injuries or {}).items()})

    def mrow(p):
        pid = str(p["id"])
        mm = model.get(pid, {})
        return {"id": pid, "name": p.get("name"), "position": p.get("position"),
                "e_next": mm.get("e_next", 0.0), "e_base": mm.get("e_base", 0.0),
                "marketValue": num(p.get("marketValue"))}

    squads_model = {uid: [mrow(p) for p in sq] for uid, sq in squads.items()}

    # Kader aller Manager um Prognosen ergänzen, beste Elf je Manager markieren
    xi_of = {}
    for uid, sq in squads.items():
        _, total, xi_ids, _ = M.best_lineup(squads_model[uid]) if squads_model[uid] else (None, 0, [], {})
        xi_of[uid] = round(total, 1)
        for p in sq:
            mm = model.get(str(p["id"]), {})
            p.update({k: mm.get(k) for k in ("e_next", "e_base", "p_start", "p_sub", "p_unav", "proj",
                                             "exp_rest", "next_opp", "next_venue", "adj", "p_inj5", "p_ban")})
            p["inXI"] = str(p["id"]) in xi_ids

    # --- Eigene Aufstellung ---
    own = squads_model.get(me_id, [])
    lu = raw.get("lineup") or {}
    cur_ids = [i for i in lu.get("ids", []) if i and i != "None"]
    lineup_now = M.lineup_total(own, cur_ids)
    best_form, best_total, best_ids, forms = M.best_lineup(own) if own else (None, 0, [], {})
    look = {s["id"]: s for s in own}
    club_of = {str(p.get("id")): p.get("club") for p in raw["squads"].get(str(me_id), [])}
    pos_de = {"keeper": "Tor", "defender": "Abwehr", "midfielder": "Mittelfeld", "striker": "Sturm"}

    def xi_rows(id_list):
        rows = [look[i] for i in id_list if i in look]
        rows.sort(key=lambda s: M.POS_ORDER.index(s["position"]) if s["position"] in M.POS_ORDER else 9)
        return [{"id": s["id"], "name": s["name"], "club": club_of.get(str(s["id"])), "pos": pos_de.get(s["position"], s["position"]),
                 "e": round(s["e_next"], 2)} for s in rows]

    formations = sorted([{"formation": k, "total": v["total"], "complete": v["complete"]} for k, v in forms.items()],
                        key=lambda r: -r["total"])
    lineup_loss = {}
    for s in own:
        _, t, _, _ = M.best_lineup(own, exclude=(s["id"],))
        lineup_loss[s["id"]] = round(best_total - t, 2)

    # --- Angebote an dich ---
    offers = [o for o in (raw.get("offers") or []) if str(o.get("toId")) == me_id or o.get("type") == "SALE"]
    comp = [o for o in offers if str(o.get("fromId")) == M.COMPUTER_ID and num(o.get("marketValue"))]
    ratios = [num(o["price"]) / num(o["marketValue"]) for o in comp if num(o.get("price"))]
    r_mean = statistics.fmean(ratios) if ratios else 1.0
    r_min = min(ratios) if ratios else 0.95
    r_max = max(ratios) if ratios else 1.05
    own_by_id = {str(p["id"]): p for p in squads.get(me_id, [])}
    offers_out = []
    for o in sorted(offers, key=lambda o: -(num(o.get("price"), 0) or 0)):
        p = own_by_id.get(str(o.get("playerId")), {})
        mv = num(o.get("marketValue"), 0) or num(p.get("marketValue"), 0)
        buy = num(p.get("purchasePrice"))
        offers_out.append({"player": o.get("player"), "from": names.get(str(o.get("fromId")), o.get("from") or "Computer"),
                           "price": num(o.get("price"), 0), "marketValue": mv,
                           "pct": round((num(o.get("price"), 0) / mv - 1) * 100, 1) if mv else None,
                           "profit": (num(o.get("price"), 0) - buy) if buy else None, "date": o.get("date"),
                           "lineupLoss": lineup_loss.get(str(o.get("playerId")))})
    best_offer = {}
    for o in offers:
        pid = str(o.get("playerId"))
        best_offer[pid] = max(best_offer.get(pid, 0), num(o.get("price"), 0))

    # --- Eigener Kader mit Kennzahlen ---
    quotes = raw.get("quotes") or {}
    det = raw.get("ownDetails") or {}
    own_rows = []
    for p in squads.get(me_id, []):
        pid = str(p["id"])
        mm = model.get(pid, {})
        mv = num(p.get("marketValue"), 0)
        buy = num(p.get("purchasePrice"))
        sp = num(p.get("points"), 0)
        own_rows.append({"id": pid, "name": p.get("name"), "club": p.get("club"), "position": p.get("position"),
                         "positionName": pos_de.get(p.get("position"), p.get("position")),
                         "status": p.get("status"), "statusName": p.get("statusName"), "statusInfo": p.get("statusInfo"),
                         "age": M.age(det.get(pid, {}).get("dob")), "inLineup": pid in cur_ids, "inBest": pid in best_ids,
                         "next_opp": mm.get("next_opp"), "next_venue": mm.get("next_venue"), "adj": mm.get("adj"),
                         "e_next": mm.get("e_next"), "p_start": round(mm.get("p_start", 0), 3),
                         "p_sub": round(mm.get("p_sub", 0), 3), "p_unav": round(mm.get("p_unav", 0), 3),
                         "season_pts": sp, "proj": mm.get("proj"), "exp_rest": mm.get("exp_rest"),
                         "p_inj5": mm.get("p_inj5"), "yellow": mm.get("yellow"), "p_ban": mm.get("p_ban"),
                         "marketValue": mv, "purchasePrice": buy, "pnl": (mv - buy) if buy else None,
                         "pnl_pct": round((mv / buy - 1) * 100, 1) if buy else None,
                         "mv_tr7": M.trend(quotes.get(pid), 7), "mv_tr30": M.trend(quotes.get(pid), 30),
                         "mv_per_pt": round(mv / sp) if sp > 0 else None,
                         "pts_per_mio": round((mm.get("proj") or 0) / max(mv, 1) * 1e6, 1),
                         "offer": best_offer.get(pid) or None, "lineupLoss": lineup_loss.get(pid),
                         "onMarket": p.get("onMarket"), "minBid": num(p.get("minBid")),
                         "rumor": p.get("rumor"), "injury": p.get("injury")})
    own_rows.sort(key=lambda r: -(r["e_next"] or 0))

    # Punkte und Rollen je Spieltag (eigener Kader, laufende Saison)
    games = []
    for r in own_rows:
        for g in histories.get(r["id"], []):
            if g.get("season") == "cur":
                games.append({"id": r["id"], "player": r["name"], "md": int(num(g.get("md"), 0)),
                              "pts": g["pts"], "role": g["role"], "status": g.get("status"),
                              "minute": g.get("in") or g.get("out"), "goals": num(g.get("goals"), 0),
                              "yellow": num(g.get("yellow"), 0)})

    # --- Transfermarkt: Prognose, Flip, mögliche Käufer ---
    since = (datetime.fromisoformat(fetched.replace("Z", "+00:00")) - timedelta(days=30)).isoformat()
    buys30 = defaultdict(int)
    for t in tx:
        if (t.get("date") or "") >= since[:19] and str(t.get("toId")) in ids:
            buys30[str(t.get("toId"))] += 1
    max30 = max(buys30.values()) if buys30 else 1
    buyers = []
    from datetime import datetime as _dt, timedelta as _td

    def iso(x):
        """Comunio-Zeitstempel (z. B. +0200 ohne Doppelpunkt) in ISO 8601, das jeder Browser versteht."""
        if not x:
            return None
        try:
            return _dt.strptime(x, "%Y-%m-%dT%H:%M:%S%z").isoformat()
        except ValueError:
            try:
                return _dt.fromisoformat(x).isoformat()
            except ValueError:
                return None

    def ends_at(hours):
        try:
            base = _dt.fromisoformat(raw.get("fetchedAt"))
            return (base + _td(hours=float(hours))).isoformat()
        except (TypeError, ValueError):
            return None

    for m in market:
        pid = str(m["id"])
        mm = model.get(pid, {})
        mv = num(m.get("marketValue"), 0)
        rec = num(m.get("recommendedPrice"))
        min_bid = rec if rec and rec > 0 else mv
        mine = str(m.get("ownerId")) == me_id
        m.update({"e_next": mm.get("e_next"), "e_base": mm.get("e_base"), "proj": mm.get("proj"),
                  "exp_rest": mm.get("exp_rest"), "p_start": round(mm.get("p_start", 0), 3),
                  "p_unav": round(mm.get("p_unav", 0), 3), "p_inj5": mm.get("p_inj5"),
                  "next_opp": mm.get("next_opp"), "next_venue": mm.get("next_venue"),
                  "minBid": min_bid, "discPct": round((min_bid / mv - 1) * 100, 1) if mv else None,
                  "mine": mine, "remainingText": M.hours_text(m.get("remainingDays")),
                  "projPerMio": round((mm.get("proj") or 0) / max(mv, 1) * 1e6, 1),
                  "age": M.age(m.get("dob")),
                  "listedAt": iso(m.get("date")), "endsAt": ends_at(m.get("remainingDays"))})
        if not mine:
            m["flipEv"] = round(mv * r_mean - min_bid)
            m["flipWorst"] = round(mv * r_min - min_bid)
            likely = []
            for uid in ids:
                if uid == me_id:
                    continue
                bal = balance_of.get(uid, 0)
                act = buys30.get(uid, 0) / max30
                afford = bal >= min_bid
                score = act * (1.0 if afford else 0.15)
                hi = min(bal, round(min_bid * 1.12, -3)) if afford else None
                buyers.append({"playerId": pid, "player": m.get("name"), "manager": names[uid], "balance": bal,
                               "affordable": afford, "buys30": buys30.get(uid, 0), "activity": round(act, 2),
                               "score": round(score, 2), "bidLow": round(min_bid), "bidMid": round(min_bid * 1.04, -3),
                               "bidHigh": hi})
                if afford and score > 0:
                    likely.append((score, names[uid]))
            m["likelyBuyers"] = [n for _, n in sorted(likely, reverse=True)[:3]]

    # --- Transfer-Pläne ---
    def sale_price(s):
        return best_offer.get(s["id"]) or (s["marketValue"] or 0) * r_mean

    def buy_price(mk):
        return round((num(mk.get("minBid"), 0) or num(mk.get("marketValue"), 0)) * 1.03, -3)

    cands = []
    for m in market:
        if m.get("mine") or (m.get("status") or "ACTIVE") not in ("ACTIVE", "WEAKENED"):
            continue
        cands.append({**mrow(m), "minBid": m.get("minBid"), "marketValue": num(m.get("marketValue"))})
    plans, _ = M.transfer_plans(own, cands, own_cash, sale_price, buy_price) if own else ([], {})
    for p in plans:
        p["delta_rest"] = round(p["delta_md"] * remaining, 1)

    # --- Saisonprognose ---
    md_points = defaultdict(list)
    for md in md_rows:
        for uid in ids:
            md_points[uid].append(md["points"].get(uid))
    fc = M.forecast(ids, squads_model, md_points, pts_of, remaining)
    for r in fc:
        r["name"] = names[r["id"]]

    # --- Monatswertung ---
    monthly = M.monthly(md_rows, cal, names)

    # --- Mindestgebote eigener Marktspieler unter Marktwert ---
    stale = [m for m in market if m.get("mine") and num(m.get("minBid"), 0) < num(m.get("marketValue"), 0)]
    stale_gap = sum(num(m.get("marketValue"), 0) - num(m.get("minBid"), 0) for m in stale)

    # --- Handlungsempfehlungen ---
    actions = []
    eur = lambda v: f"{v / 1e6:.2f}".replace(".", ",") + " Mio. €"
    f1 = lambda v: f"{v:.1f}".replace(".", ",")
    if own_cash < 0:
        top = max(offers_out, key=lambda o: o["price"]) if offers_out else None
        detail = f"Dein Konto steht bei {eur(own_cash)}."
        if top:
            detail += f" Verkaufst du {top['player']} zum Angebot von {top['from']} ({eur(top['price'])}), bleiben {eur(own_cash + top['price'])}."
        actions.append({"tag": "Konto", "title": "Kontostand ausgleichen", "detail": detail})
    if own and best_total - lineup_now >= 0.5:
        missing = [look[i]["name"] for i in best_ids if i not in cur_ids and i in look]
        dropped = [look[i]["name"] for i in cur_ids if i not in best_ids and i in look]
        actions.append({"tag": "Aufstellung", "title": f"Aufstellung für Spieltag {next_md} ändern",
                        "detail": f"{best_form} mit {', '.join(missing) or '–'}, ohne {', '.join(dropped) or '–'} bringt "
                                  f"{f1(best_total)} statt {f1(lineup_now)} erwartete Punkte (+{f1(best_total - lineup_now)})."})
    if plans:
        p = plans[0]
        actions.append({"tag": "Transfer", "title": f"Plan {p['id']}: " + (f"{', '.join(p['sell'])} verkaufen, " if p['sell'] else "") + f"{', '.join(p['buy'])} kaufen",
                        "detail": f"Kosten etwa {eur(p['cost'])}, Erlös {eur(p['proceeds'])}. Erwartet +{f1(p['delta_md'])} Punkte je Spieltag "
                                  f"in der besten Aufstellung, Konto danach {eur(p['cash_after'])}."})
    if stale:
        actions.append({"tag": "Risiko", "title": "Mindestgebote unter Marktwert prüfen",
                        "detail": f"Bei {len(stale)} deiner gelisteten Spieler liegt das Mindestgebot zusammen {eur(stale_gap)} unter dem Marktwert. Nimm Spieler vom Markt, die du behalten willst."})
    flips = sorted([m for m in market if not m.get("mine") and (m.get("flipEv") or 0) > 0], key=lambda m: -m["flipEv"])[:2]
    for m in flips:
        actions.append({"tag": "Flip", "title": f"Flip-Chance: {m['name']}",
                        "detail": f"Mindestgebot {eur(m['minBid'])} bei Marktwert {eur(m['marketValue'])}. Erwarteter Gewinn beim Weiterverkauf an den Computer etwa {round(m['flipEv'] / 1000)} T €, schlechtester beobachteter Fall {round(m['flipWorst'] / 1000)} T €. Restzeit: {m['remainingText']}"})
    above = [o for o in offers_out if (o["pct"] or 0) > 0]
    if offers_out:
        actions.append({"tag": "Verkauf", "title": "Angebote nur mit Prämie annehmen",
                        "detail": f"{len(offers_out)} Angebote liegen zwischen {f1(min(o['pct'] or 0 for o in offers_out))} % und {f1(max(o['pct'] or 0 for o in offers_out))} % des Marktwerts."
                                  + (f" Über Marktwert: {', '.join(sorted(set(o['player'] for o in above)))}." if above else "")})
    losers = sorted([r for r in own_rows if r["pnl"] is not None and r["pnl"] < -500000], key=lambda r: r["pnl"])[:1]
    for r in losers:
        actions.append({"tag": "Verlust", "title": f"{r['name']} liegt {eur(-r['pnl'])} unter Kaufpreis",
                        "detail": f"{int(r['season_pts'])} Punkte, Startelf-Chance {round(r['p_start'] * 100)} %. Ein Verkauf lohnt vor allem, wenn du das Geld für einen Startspieler brauchst."})
    for i, a in enumerate(actions):
        a["prio"] = i + 1

    # --- Next Match: Paarungen, Quoten, Kader-Erwartung, Aufstellung ---
    import next_match as NM
    nmi = raw.get("nextMatch") or {}
    fixtures = nmi.get("fixtures") or NM.fixtures_from_comunio(list(players.values()))
    odds = nmi.get("odds") or []
    fx_out = []
    for f in fixtures:
        o = None
        if odds:
            for cand in odds:
                if NM.match_name(f["home"], [cand["home"]]) and NM.match_name(f["away"], [cand["away"]]):
                    o = cand
                    break
        imp = NM.implied(o["odd1"], o["oddX"], o["odd2"]) if o else None
        fx_out.append({**f, "odd1": o and o["odd1"], "oddX": o and o["oddX"], "odd2": o and o["odd2"],
                       "bookmaker": o and o["bookmaker"], **(imp or {})})
    fx_out.sort(key=lambda f: f.get("kickoff") or "")
    team_names = [n for f in fx_out for n in (f["home"], f["away"]) if n]

    def club_fixture(club_name):
        hit = NM.match_name(club_name, team_names) if club_name else None
        if not hit:
            return None, None
        for f in fx_out:
            if f["home"] == hit:
                return f, True
            if f["away"] == hit:
                return f, False
        return None, None

    nm_rows = []
    for p in squads.get(me_id, []):
        pid = str(p["id"])
        b = base.get(pid) or {}
        mm = model.get(pid, {})
        f, home = club_fixture(p.get("club"))
        play = b.get("p_start", 0) + 0.4 * b.get("p_sub", 0)
        adj_hist = mm.get("adj", 0.0) or 0.0
        odds_adj, p_win, p_loss = None, None, None
        if f and f.get("p1") is not None:
            p_win, p_loss = (f["p1"], f["p2"]) if home else (f["p2"], f["p1"])
            odds_adj = max(-M.OPP_CAP, min(M.OPP_CAP, (p_win - p_loss) * 3.0))
        adj = odds_adj if odds_adj is not None else adj_hist
        p_unav = mm.get("p_unav", b.get("p_unav", 0)) or 0
        e_match = (1 - p_unav) * (b.get("e_play", 0) + adj * play)
        opp_name = (f["away"] if home else f["home"]) if f else mm.get("next_opp")
        nm_rows.append({"id": pid, "name": p.get("name"), "club": p.get("club"), "position": p.get("position"),
                        "positionName": pos_de.get(p.get("position"), p.get("position")),
                        "status": p.get("status"), "statusName": p.get("statusName"), "statusInfo": p.get("statusInfo"),
                        "injury": p.get("injury"), "opponent": opp_name,
                        "venue": ("Heim" if home else "Auswärts") if f else mm.get("next_venue"),
                        "kickoff": f and f.get("kickoff"), "played": bool(f and f.get("finished")),
                        "pWin": p_win, "pLoss": p_loss,
                        "p_start": round(b.get("p_start", 0) * (1 - p_unav), 3),
                        "p_play": round((b.get("p_start", 0) + b.get("p_sub", 0)) * (1 - p_unav), 3),
                        "p_unav": round(p_unav, 3), "e_match": round(e_match, 2),
                        "adj": round(adj, 2), "adjSource": "Quote" if odds_adj is not None else "Gegnerhistorie",
                        "inLineup": pid in cur_ids})
    nm_squad = [{"id": r["id"], "name": r["name"], "position": r["position"], "e_match": r["e_match"]} for r in nm_rows]
    nm_form, nm_total, nm_ids, nm_forms = M.best_lineup(nm_squad, key="e_match") if nm_squad else (None, 0, [], {})
    for r in nm_rows:
        r["inBest"] = r["id"] in nm_ids
    nm_rows.sort(key=lambda r: (M.POS_ORDER.index(r["position"]) if r["position"] in M.POS_ORDER else 9, -r["e_match"]))
    nm_now = round(sum(r["e_match"] for r in nm_rows if r["inLineup"]), 2)
    next_match = {"md": nmi.get("md") or next_md, "date": nmi.get("date") or next_date, "started": nmi.get("started"),
                  "fixtures": fx_out, "fixtureSource": (fx_out[0]["source"] if fx_out else None),
                  "oddsAvailable": bool(odds), "bookmaker": next((f["bookmaker"] for f in fx_out if f.get("bookmaker")), None),
                  "squad": nm_rows, "formation": nm_form, "total": round(nm_total, 2), "nowTotal": nm_now,
                  "best": [{"name": r["name"], "club": r.get("club"), "pos": r["positionName"], "e": r["e_match"], "opponent": r["opponent"],
                            "venue": r["venue"]} for r in nm_rows if r["inBest"]],
                  "formations": sorted([{"formation": k, "total": v["total"], "complete": v["complete"]}
                                        for k, v in nm_forms.items()], key=lambda x: -x["total"])}

    opp_list = sorted([{"clubId": k, "adj": round(v, 2)} for k, v in opp.items()], key=lambda r: -r["adj"])
    club_names = {}
    for p in players.values():
        nm = p.get("nextMatch") or {}
        if nm.get("homeId"):
            club_names[nm["homeId"]] = nm.get("home")
        if nm.get("guestId"):
            club_names[nm["guestId"]] = nm.get("guest")
        if p.get("clubId"):
            club_names[p["clubId"]] = p.get("club")
    for r in opp_list:
        r["club"] = club_names.get(r["clubId"], str(r["clubId"]))

    status = {"nextMd": next_md, "nextDate": next_date, "remaining": remaining, "played": played,
              "lineupNow": lineup_now, "lineupBest": best_total, "lineupGain": round(best_total - lineup_now, 2),
              "formationBest": best_form, "formationNow": M.tactic_name(lu.get("tactic")),
              "offerMean": round(r_mean, 4), "offerMin": round(r_min, 4), "offerMax": round(r_max, 4),
              "offerN": len(ratios), "staleGap": stale_gap, "staleN": len(stale), "credit": num(raw.get("credit")),
              "venueHome": round(venue.get(True, 0), 2), "venueAway": round(venue.get(False, 0), 2),
              "planDelta": plans[0]["delta_md"] if plans else None, "xiOf": xi_of}
    return {"status": status, "actions": actions, "ownSquad": own_rows, "games": games,
            "lineup": {"now": xi_rows(cur_ids), "best": xi_rows(best_ids), "formationNow": M.tactic_name(lu.get("tactic")),
                       "formationBest": best_form},
            "formations": formations, "offers": offers_out, "buyers": buyers, "plans": plans,
            "forecast": fc, "monthly": monthly, "opponents": opp_list, "nextMatch": next_match}


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
