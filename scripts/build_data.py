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
import season as S

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


OUT_STATES = {"INJURED", "REHABILITATION", "SUSPENDED", "REDBANNED", "YELLOWREDBANNED", "YELLOWBANNED", "AWAY", "RETIRED"}


def light(status, p_unav=None, injury=None):
    """Ampel: rot = fällt aus, gelb = fraglich/angeschlagen, grün = fit."""
    st = status or "ACTIVE"
    pu = p_unav or 0
    if st in OUT_STATES or pu >= 0.5:
        return "red"
    if st == "WEAKENED" or pu >= 0.15 or injury:
        return "yellow"
    return "green"


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
    # Tore der aktuellen Kaderspieler in dieser Saison, gezählt ab dem Kaufdatum
    stats_all = raw.get("stats") or {}
    for s in standings:
        g = 0
        for p in (raw.get("squads") or {}).get(s["id"], []):
            since = str(p.get("purchaseDate") or "")[:10]
            for r in stats_all.get(str(p.get("id")), []):
                if r.get("season") == "cur" and (not since or (r.get("date") or "") >= since):
                    g += num(r.get("goals"), 0) or 0
        s["goals"] = int(g)
    # Torschützenliste: alle Spieler in den Kadern der Manager, Tore dieser Saison
    POS_DE = {"keeper": "Tor", "defender": "Abwehr", "midfielder": "Mittelfeld", "striker": "Sturm"}
    scorers = []
    for uid, ps in (raw.get("squads") or {}).items():
        for p in ps:
            rows = [r for r in stats_all.get(str(p.get("id")), []) if r.get("season") == "cur"]
            since = str(p.get("purchaseDate") or "")[:10]
            goals = sum(num(r.get("goals"), 0) or 0 for r in rows)
            if not goals:
                continue
            scorers.append({"id": str(p.get("id")), "name": p.get("name"), "club": p.get("club"),
                            "position": POS_DE.get(p.get("position"), p.get("position")), "ownerId": str(uid),
                            "owner": names.get(str(uid), "?"), "goals": int(goals),
                            "goalsOwned": int(sum(num(r.get("goals"), 0) or 0 for r in rows if not since or (r.get("date") or "") >= since)),
                            "games": sum(1 for r in rows if r.get("points") is not None or r.get("in")),
                            "points": num(p.get("points"))})
    scorers.sort(key=lambda r: (-r["goals"], -r["goalsOwned"], r["name"] or ""))
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

    # --- Saisonübergang: Kontostand je Manager neu gesetzt ---
    # Der Communityleiter hat jedes Konto per Strafe/Gutschrift („Saisonübergang“) auf 10 Mio. € gesetzt,
    # danach kamen 5 Mio. € Saisonwechsel-Bonus dazu (= 15 Mio. € Basis). Maßgeblich ist je Manager der
    # Zeitpunkt SEINER letzten Saisonübergangs-Buchung: Transfers und Buchungen davor stecken im Reset.
    # (Geprüft am 25.10.2026 gegen den echten Kontoauszug: ergibt den Kontostand auf den Euro genau.)
    ref = CFG.get("balance_reference", {})
    ref_after = ref.get("after", "1970-01-01")
    base = num(ref.get("base_eur", 15000000))
    reset_re = re.compile(ref.get("reset_reason", "saisonübergang"), re.I)

    def ts(x):
        try:
            d = datetime.fromisoformat(re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", str(x).replace("Z", "+00:00")))
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    season_floor = ts(ref.get("season_floor", "2026-05-20T00:00:00+00:00"))
    reset_at = {}
    for b in raw.get("bonuses", []):
        bid = id_for(b.get("name"))
        t = ts(b.get("date"))
        if bid and t and t >= season_floor and reset_re.search(b.get("reason") or ""):
            reset_at[bid] = max(reset_at.get(bid, t), t)
    default_reset = ts(ref_after)
    for i in ids:
        reset_at.setdefault(i, default_reset)

    def after_reset(i, date):
        t = ts(date)
        return bool(t and reset_at.get(i) and t > reset_at[i])

    # --- Transfers (Comunio-News, vollständig, ohne Doppelte) ---
    first_reset = min(reset_at.values())
    seen, tx_unique = set(), []
    for t in raw["transfers"]:
        tt = ts(t.get("date"))
        if not tt or tt < first_reset:
            continue
        key = (t.get("newsId"), t.get("player"), str(t.get("fromId")), str(t.get("toId")), num(t.get("price")))
        if key not in seen:
            seen.add(key)
            tx_unique.append(t)

    sales, buys = defaultdict(int), defaultdict(int)
    tf_stats = {i: {"id": i, "name": names[i], "buys": 0, "sells": 0, "buySum": 0, "sellSum": 0,
                    "fromComputer": 0, "toComputer": 0, "betweenManagers": 0} for i in ids}
    for t in tx_unique:
        f, to, price = str(t.get("fromId")), str(t.get("toId")), num(t.get("price"))
        if f in tf_stats and after_reset(f, t.get("date")):
            sales[f] += price
            st = tf_stats[f]
            st["sells"] += 1
            st["sellSum"] += price
            st["toComputer" if to == str(COMPUTER_ID) else "betweenManagers"] += 1
        if to in tf_stats and after_reset(to, t.get("date")):
            buys[to] += price
            st = tf_stats[to]
            st["buys"] += 1
            st["buySum"] += price
            st["fromComputer" if f == str(COMPUTER_ID) else "betweenManagers"] += 1
    for st in tf_stats.values():
        st["total"] = st["buys"] + st["sells"]
        st["net"] = st["sellSum"] - st["buySum"]
    transfer_stats = sorted(tf_stats.values(), key=lambda s: -s["total"])

    # --- Strafen & Gutschriften nach dem jeweiligen Saisonübergang ---
    bonus_sum = defaultdict(int)
    bonus_rows = []
    for b in raw.get("bonuses", []):
        bid = id_for(b.get("name"))
        counted = bool(bid and after_reset(bid, b.get("date")))
        if counted:
            bonus_sum[bid] += num(b.get("amount"))
        bonus_rows.append({"date": b.get("date"), "name": names.get(bid, b.get("name")), "amount": num(b.get("amount")),
                           "reason": b.get("reason"), "counted": counted})
    bonus_rows.sort(key=lambda r: r["date"] or "", reverse=True)

    accounts = []
    for i in ids:
        pts = pts_of.get(i, 0)
        est = base + sales[i] - buys[i] + pts * bonus_per_point + bonus_sum[i]
        real = num(raw["me"]["budget"]) if i == me_id else None
        balance = real if real is not None else est
        accounts.append({"id": i, "name": names[i], "base": base, "resetAt": reset_at[i].isoformat() if reset_at.get(i) else None,
                         "sales": sales[i], "buys": buys[i],
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

    all_quotes = raw.get("quotes") or {}

    def enrich(p):
        pid = str(p.get("id"))
        out = {"positionName": POSITIONS.get(p.get("position"), p.get("position") or "–"),
               "statusName": STATUS.get(p.get("status") or "ACTIVE", p.get("status"))}
        stats_tr = raw.get("statsTrends") or {}
        if pid in stats_tr:  # Original-Pfeil von stats.comunio.de (None = „?“)
            out["trend"], out["trendSrc"] = stats_tr[pid], ("stats" if stats_tr[pid] is not None else None)
        else:
            api_trend = p.get("trend") if p.get("trend") is not None else (raw.get("trends") or {}).get(pid)
            out["trend"], out["trendSrc"] = M.player_trend(api_trend, all_quotes.get(pid))
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

    # --- Transfer-Bilanz: realisierte Gewinne (Verkauf gegen Kauf) und Buchgewinne im Kader ---
    by_player = defaultdict(list)
    for t in sorted(tx_unique, key=lambda t: t.get("date") or ""):
        by_player[norm(t.get("player"))].append(t)
    deals = []
    for lst in by_player.values():
        for k, t in enumerate(lst):
            seller = str(t.get("fromId"))
            if seller not in ids:
                continue
            buy = next((b for b in reversed(lst[:k]) if str(b.get("toId")) == seller), None)
            if not buy:
                continue
            deals.append({"managerId": seller, "manager": names[seller], "player": t.get("player"),
                          "buy": num(buy.get("price")), "sell": num(t.get("price")),
                          "profit": num(t.get("price")) - num(buy.get("price")), "date": t.get("date"),
                          "to": names.get(str(t.get("toId")), "Computer")})
    deals.sort(key=lambda d: -d["profit"])
    trade_balance = []
    for i in ids:
        mine = [d for d in deals if d["managerId"] == i]
        held = [p for p in squads.get(i, []) if p.get("purchasePrice") is not None]
        book = sum((p.get("marketValue") or 0) - p["purchasePrice"] for p in held) if held else None
        real = sum(d["profit"] for d in mine)
        trade_balance.append({"id": i, "name": names[i], "realized": real, "deals": len(mine),
                              "wins": sum(1 for d in mine if d["profit"] > 0),
                              "bookGain": book, "bookN": len(held),
                              "total": real + (book or 0),
                              "best": mine[0] if mine else None, "worst": mine[-1] if mine else None})
    trade_balance.sort(key=lambda r: -r["total"])

    # --- Platzierungsverlauf je Spieltag (kumulierte Punkte) ---
    cum = {i: 0 for i in ids}
    rank_hist = {"labels": [], "ranks": {i: [] for i in ids}, "points": {i: [] for i in ids}}
    for r in md_rows:
        if not any(v is not None for v in r["points"].values()):
            continue
        for i in ids:
            cum[i] += r["points"].get(i) or 0
        rank_hist["labels"].append(r["name"])
        for i in ids:
            rank_hist["ranks"][i].append(1 + sum(1 for j in ids if cum[j] > cum[i]))
            rank_hist["points"][i].append(cum[i])

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
    snap = {"date": today, "balance": dict(balance_of), "points": dict(pts_of), "teamValue": dict(tv_of),
            # Prognose-Archiv: so lässt sich die Endtabellen-Prognose am Saisonende rückwirkend prüfen
            "forecast": {r["id"]: [r["exp_final"], r["p10"], r["p90"], r["p_title"]] for r in an.get("forecast") or []},
            "forecastModel": (an.get("calib") or {}).get("model")}
    history = [h for h in history if h["date"] != today] + [snap]
    history.sort(key=lambda h: h["date"])

    me_rank = next(s for s in standings if s["id"] == me_id)
    return {
        "generatedAt": raw["fetchedAt"],
        "builtAt": datetime.now(timezone.utc).isoformat(),
        "tradeBalance": trade_balance,
        "deals": deals[:60] + [d for d in deals[60:] if d["managerId"] == me_id],
        "rankHistory": rank_hist,
        "season": CFG.get("season_label"),
        "prevSeason": prev_label,
        "community": raw["community"]["name"],
        "me": {"id": me_id, "name": names[me_id], "rank": me_rank["rank"], "points": me_rank["points"],
               "gap": me_rank["gap"], "balance": me_acc["balance"], "teamValue": me_rank["teamValue"],
               "deviation": me_acc["deviation"]},
        "managers": [{"id": i, "name": names[i]} for i in ids],
        "standings": standings,
        "scorers": scorers,
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
        "liveWorker": os.environ.get("LIVE_WORKER_URL") or None,
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
    pos_all = {str(k): v for k, v in (raw.get("playerPositions") or {}).items() if v}
    pos_all.update({pid: pl.get("position") for pid, pl in players.items() if pl.get("position")})

    # --- Rückprüfung (Paket 4): Gewichtung der Vergangenheit an allen bisherigen Spielen kalibrieren ---
    calib = {"ok": False}
    try:
        bt = S.player_backtest(histories, pos_all)
        if bt and bt["best"]["n"] >= 2000:
            M.HALF_LIFE, M.SHRINK_K = bt["best"]["hl"], bt["best"]["k"]
        calib.update({"ok": True, "grid": bt["grid"] if bt else [], "hl": M.HALF_LIFE, "k": M.SHRINK_K})
        print(f"::notice title=Rückprüfung Spieler::Gewichtung Halbwertszeit {M.HALF_LIFE} Spiele, "
              f"{M.SHRINK_K} Pseudo-Spiele; " + "; ".join(f"{g['hl']}/{g['k']}: {g['rmse_cond']:.3f}" for g in (bt or {}).get("grid", [])))
    except Exception as e:
        print(f"::warning title=Rückprüfung Spieler::übersprungen ({type(e).__name__}: {e})")
    means = M.position_means(histories, positions)
    base = {pid: M.player_base(histories.get(pid, []), positions.get(pid), means, players[pid].get("status"))
            for pid in players}
    opp, venue = M.opponent_factors(histories, base)
    model = M.enrich_players(players, histories, means, opp, venue, remaining,
                             {k: v for k, v in (injuries or {}).items()})

    for m in market:  # Prognose für Markt-Spieler (Auktions-Übersicht)
        mm = model.get(str(m["id"]), {})
        m["e_next"] = round(mm.get("e_next", 0.0) or 0.0, 2)
        m["p_unav"] = round(mm.get("p_unav", 0.0) or 0.0, 3)
        mv_ = num(m.get("marketValue")) or 0
        m["ePerMio"] = round(m["e_next"] / (mv_ / 1e6), 2) if mv_ else None
        m["light"] = light(m.get("status"), m["p_unav"], m.get("injury"))

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

    sq_me = {str(p["id"]): p for p in squads.get(me_id, [])}

    def light_of(pid):
        p = sq_me.get(str(pid)) or {}
        return light(p.get("status"), p.get("p_unav"), p.get("injury"))

    def xi_rows(id_list):
        rows = [look[i] for i in id_list if i in look]
        rows.sort(key=lambda s: M.POS_ORDER.index(s["position"]) if s["position"] in M.POS_ORDER else 9)
        return [{"id": s["id"], "name": s["name"], "club": club_of.get(str(s["id"])), "pos": pos_de.get(s["position"], s["position"]),
                 "e": round(s["e_next"], 2), "light": light_of(s["id"]),
                 "note": ((sq_me.get(str(s["id"])) or {}).get("statusInfo") or ((sq_me.get(str(s["id"])) or {}).get("injury") or {}).get("return"))}
                for s in rows]

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
                         "trend": p.get("trend"), "trendSrc": p.get("trendSrc"),
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

    cands_all = []
    for m in market:
        if (m.get("status") or "ACTIVE") not in ("ACTIVE", "WEAKENED"):
            continue
        cands_all.append({**mrow(m), "minBid": m.get("minBid"), "marketValue": num(m.get("marketValue")),
                          "ownerId": str(m.get("ownerId")), "mine": bool(m.get("mine"))})
    cands = [c for c in cands_all if not c["mine"]]
    plans, _ = M.transfer_plans(own, cands, own_cash, sale_price, buy_price) if own else ([], {})
    for p in plans:
        p["delta_rest"] = round(p["delta_md"] * remaining, 1)

    # --- Saisonprognose ---
    md_points = defaultdict(list)
    for md in md_rows:
        for uid in ids:
            md_points[uid].append(md["points"].get(uid))
    extras = {uid: {} for uid in ids}
    owners, actual = None, {}

    # (a) Aufstell-Effizienz: Kader jedes Managers zu jedem Anpfiff aus den Transfers rekonstruieren und die
    #     tatsächlichen Punkte mit der im Nachhinein besten Elf dieses Kaders vergleichen
    try:
        started = [c for c in cal if c.get("started") and str(c.get("key", "")).isdigit() and c.get("season")]
        season_lbl = max(started, key=lambda c: c.get("date") or "").get("season") if started else None
        deadlines = {str(int(c["key"])): M.parse_dt(c.get("date")) for c in started if c.get("season") == season_lbl}
        deadlines = {k: v for k, v in deadlines.items() if v}
        actual = {uid: {str(int(md["key"])): md["points"].get(uid) for md in md_rows} for uid in ids}
        seen_tx, tx_all = set(), []
        for t in raw.get("transfers") or []:
            key = (t.get("newsId"), t.get("playerId"), str(t.get("fromId")), str(t.get("toId")), num(t.get("price")))
            if key not in seen_tx:
                seen_tx.add(key)
                tx_all.append(t)
        stats_raw = raw.get("stats") or {}
        player_pts = {}
        for pid, rows_ in stats_raw.items():
            d_ = {}
            for r_ in rows_:
                if r_.get("season") == "cur" and r_.get("points") is not None and num(r_.get("md"), None) is not None:
                    d_[str(int(num(r_["md"])))] = num(r_["points"], 0)
            player_pts[str(pid)] = d_
        owners = M.squads_at(tx_all, {uid: [str(x["id"]) for x in sq] for uid, sq in squads.items()}, deadlines, ids)
        factors, pooled = M.lineup_factors(owners, player_pts, pos_all, actual, set(stats_raw))
        for uid, f_ in factors.items():
            extras[uid]["disc"] = f_["factor"]
        print(f"::notice title=Aufstell-Effizienz::{len(factors)} Manager aus {len(deadlines)} Spieltagen; "
              f"Faktoren {sorted(f_['factor'] for f_ in factors.values())}, Community-Quote {pooled}")
    except Exception as e:  # Prognose bleibt auch ohne diese Zusatzgröße lauffähig
        print(f"::warning title=Aufstell-Effizienz::übersprungen ({type(e).__name__})")

    # (b) Guthaben: was kann jeder Manager mit seinem Geld am Markt dazugewinnen?
    def sale_price_g(s):
        return (s["marketValue"] or 0) * r_mean

    for uid in ids:
        try:
            extras[uid]["gain"] = M.cash_gain(squads_model.get(uid, []), [c for c in cands_all if c["ownerId"] != uid],
                                              balance_of.get(uid, 0), sale_price_g, buy_price)
        except Exception as e:
            print(f"::warning title=Guthaben-Effekt::übersprungen ({type(e).__name__})")

    # (c) Minus-Risiko je Spieltag (bisherige Häufigkeit, zum Community-Mittel gezogen)
    n_all = sum(len(v) for v in md_points.values())
    z_all = sum(1 for v in md_points.values() for x in v if x is None)
    pooled_zero = z_all / n_all if n_all else 0.0
    for uid in ids:
        h_ = md_points.get(uid, [])
        z_ = sum(1 for x in h_ if x is None)
        extras[uid]["p_zero"] = (z_ + M.ZERO_K * pooled_zero) / (len(h_) + M.ZERO_K) if (h_ or pooled_zero) else 0.0

    # (d) Saisonsimulation auf Spielerebene (Paket 3), kalibriert durch Rückprüfung (Paket 4)
    fc = None
    try:
        import next_match as NM
        matches = S.matches_from_histories(histories)
        prm, info = S.residual_params(histories, pos_all, matches)
        calib["player"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in info.items()}
        mb = S.manager_backtest(owners, histories, pos_all, matches, actual, prm) if owners else None
        if mb:
            prm["sig_extra"] = round(mb["sig_extra"], 2)
            prm["bias_adj"] = round(mb["bias_adj"], 2)
            calib["manager"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in mb.items() if k != "records"}
            calib["managerRecords"] = [{"md": r_["md"], "name": names.get(r_["uid"], r_["uid"]), "actual": r_["actual"],
                                        "mean": round(r_["mean"], 1), "p10": round(r_["p10"]), "p90": round(r_["p90"])}
                                       for r_ in mb["records"]]
        e_any = {pid: M.player_base(h_, pos_all[pid], means, "ACTIVE")["e_play"]
                 for pid, h_ in histories.items() if pos_all.get(pid) in M.POS_ORDER}
        e_any.update({pid: mm_["e_play"] for pid, mm_ in model.items() if mm_ and "e_play" in mm_})
        dr = S.squad_drift(owners, {uid: [str(x["id"]) for x in sq] for uid, sq in squads.items()},
                           e_any, pos_all) if owners else None
        if dr:
            prm["sig_drift"] = round(min(4.0, dr["sig"]), 2)
            prm["rho_drift"] = dr["rho"]
            calib["drift"] = dr
        rt, hfa, sd_gd = S.team_ratings(matches)
        club_names = {str(k): v for k, v in (raw.get("clubs") or {}).items()}
        for p_ in players.values():
            if p_.get("clubId") and p_.get("club"):
                club_names.setdefault(str(p_["clubId"]), p_["club"])
        rem_mds = list(range(played + 1, M.SEASON_MDS + 1))
        fixtures, fx_info = S.build_fixtures(raw, histories, club_names, rem_mds, NM.match_name)
        # Quoten (falls vorhanden) für den nächsten Spieltag: erwartete Tordifferenz ≈ 2,6 × (P(Sieg) − P(Niederlage))
        odds_gd = {}
        lookup = list(club_names.values())
        rev = {v: k for k, v in club_names.items()}
        for o in ((raw.get("nextMatch") or {}).get("odds") or []):
            imp = NM.implied(o.get("odd1"), o.get("oddX"), o.get("odd2"))
            h_, g_ = NM.match_name(o.get("home"), lookup), NM.match_name(o.get("away"), lookup)
            if imp and h_ and g_ and rem_mds:
                eg = 2.6 * (imp["p1"] - imp["p2"])
                for c_, sg in ((rev[h_], 1), (rev[g_], -1)):
                    base_eg = S.exp_gd(rt, hfa, c_, rev[g_] if sg == 1 else rev[h_], sg == 1)
                    odds_gd[(rem_mds[0], c_)] = 0.5 * sg * eg + 0.5 * base_eg
        pinfo = {}
        for uid, sq in squads.items():
            for p_ in sq:
                pid = str(p_["id"])
                pos_ = p_.get("position") or pos_all.get(pid)
                if pos_ in M.POS_ORDER:
                    pinfo[pid] = S.player_info(pid, histories.get(pid, []), pos_, means, p_.get("status"),
                                               str(p_.get("clubId")) if p_.get("clubId") else None,
                                               ((injuries or {}).get(pid) or {}).get("return"), sig_lvl=prm["sig_lvl"])
        squads_ids = {uid: [str(x["id"]) for x in sq if str(x["id"]) in pinfo] for uid, sq in squads.items()}
        fc = S.season_forecast(ids, squads_ids, pinfo, fixtures, rt, hfa, sd_gd, prm, rem_mds, extras,
                               pts_of, md_points, odds_gd=odds_gd)
        calib.update({"prm": prm, "fixtures": fx_info, "hfa": round(hfa, 2), "sdGd": round(sd_gd, 2),
                      "oddsUsed": len(odds_gd) // 2, "sims": 3000, "model": "Spielerebene"})
        mp = calib.get("manager") or {}
        print(f"::notice title=Rückprüfung Manager::{mp.get('n')} Manager-Spieltage; Abweichung Ø {mp.get('bias')}, "
              f"Fehler {mp.get('rmse')}, Modell-Streuung {mp.get('sd_pred')}, Treffer 10–90 % {mp.get('cover80')}; "
              f"Zusatzstreuung {prm['sig_extra']}, Korrektur {prm['bias_adj']}")
        print(f"::notice title=Simulationsparameter::Mannschaftseffekt {prm['beta']}, Joker {prm['beta_sub']}, "
              f"Vereins-Schock {prm['sig_c']}, Spielerniveau {prm['sig_lvl']}, Einzel {prm['sig_ind']}, Kaderwechsel {calib.get('drift')}, "
              f"Spielplan {fx_info}, Heimvorteil {round(hfa, 2)}, Streuung Tordifferenz {round(sd_gd, 2)}, Quoten {len(odds_gd) // 2}")
        pi = calib.get("player") or {}
        print(f"::notice title=Prognosegüte Spieler::Fehler Modell {pi.get('rmse_model')} vs. Schnitt letzte 10 {pi.get('rmse_avg10')} "
              f"(laufende Saison {pi.get('rmse_model_cur')} vs. {pi.get('rmse_avg10_cur')}); mit Spielplan {pi.get('rmse_fixture')} "
              f"vs. ohne {pi.get('rmse_no_fixture')}; Steigung {pi.get('slope')}, n={pi.get('n_rows')}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"::warning title=Saisonsimulation::übersprungen, einfaches Modell ({type(e).__name__}: {e})")
        calib["model"] = "einfach"
    if fc is None:
        fc = M.forecast(ids, squads_model, md_points, pts_of, remaining, extras=extras)
    print("::notice title=Prognose-Zusatzgrößen::Guthaben-Effekt (Pkt./Spieltag) "
          f"{sorted(r['gain'] for r in fc)}, Ausfallrisiko {sorted(r['p_zero'] for r in fc)}, "
          f"Streuung Endstand {sorted(r['sd_level'] for r in fc)}")
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
                        "inLineup": pid in cur_ids, "light": light(p.get("status"), p_unav, p.get("injury"))})
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
                            "venue": r["venue"], "light": r["light"]} for r in nm_rows if r["inBest"]],
                  "formations": sorted([{"formation": k, "total": v["total"], "complete": v["complete"]}
                                        for k, v in nm_forms.items()], key=lambda x: -x["total"])}

    # Tausch-Vorschlag: aktuelle Aufstellung -> empfohlene Aufstellung (Paare je Position)
    swaps = []
    by_nm = {r["id"]: r for r in nm_rows}
    out_rows = [r for r in nm_rows if r["inLineup"] and r["id"] not in nm_ids]
    in_rows = [r for r in nm_rows if r["inBest"] and not r["inLineup"]]
    out_rows.sort(key=lambda r: r["e_match"])
    in_rows.sort(key=lambda r: -r["e_match"])
    used_in = set()
    for o in out_rows:
        cand = [r for r in in_rows if r["id"] not in used_in and r["position"] == o["position"]] or \
               [r for r in in_rows if r["id"] not in used_in]
        if not cand:
            swaps.append({"out": o["name"], "outPos": o["positionName"], "outLight": o["light"], "in": None, "gain": round(-o["e_match"], 2)})
            continue
        i = cand[0]
        used_in.add(i["id"])
        swaps.append({"out": o["name"], "outPos": o["positionName"], "outLight": o["light"], "outE": o["e_match"],
                      "in": i["name"], "inPos": i["positionName"], "inLight": i["light"], "inE": i["e_match"],
                      "gain": round(i["e_match"] - o["e_match"], 2)})
    for i in in_rows:
        if i["id"] not in used_in:
            swaps.append({"out": None, "in": i["name"], "inPos": i["positionName"], "inLight": i["light"], "inE": i["e_match"],
                          "gain": i["e_match"]})
    swaps.sort(key=lambda x: -x["gain"])
    next_match["swaps"] = swaps
    next_match["swapGain"] = round(nm_total - nm_now, 2)
    next_match["lineupEmpty"] = not cur_ids

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
            "forecast": fc, "calib": calib, "monthly": monthly, "opponents": opp_list, "nextMatch": next_match}


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
    # Kontrolle ohne Beträge (Logs sind öffentlich): stimmt die eigene Berechnung mit dem echten Kontostand?
    dev = (dash.get("me") or {}).get("deviation")
    if dev is not None:
        if abs(dev) < 1:
            print("::notice title=Kontostände::Eigene Berechnung stimmt auf den Euro mit dem echten Kontostand überein")
        else:
            print("::warning title=Kontostände::Eigene Berechnung weicht vom echten Kontostand ab – Daten prüfen")


if __name__ == "__main__":
    main()
