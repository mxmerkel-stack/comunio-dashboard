"""Prognosemodell und Analysen (übernommen aus der „Comunio Zentrale“).

Alle Funktionen arbeiten nur mit den Daten aus raw.json – nichts wird bei Comunio verändert.
"""
import itertools
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HALF_LIFE = 12          # Spieltage
SHRINK_K = 2.0          # Pseudo-Spiele Richtung Positionsschnitt
OPP_SHRINK = 30.0       # Dämpfung Gegnerfaktor
OPP_CAP = 1.5
SIMS = 4000
SEASON_MDS = 34
FORMATIONS = {"3-4-3": (3, 4, 3), "3-5-2": (3, 5, 2), "4-3-3": (4, 3, 3), "4-4-2": (4, 4, 2),
              "4-5-1": (4, 5, 1), "5-3-2": (5, 3, 2), "5-4-1": (5, 4, 1)}
POS_ORDER = ["keeper", "defender", "midfielder", "striker"]
UNAV_NOW = {"ACTIVE": 0.0, "WEAKENED": 0.35, "INJURED": 0.85, "REHABILITATION": 0.6, "SUSPENDED": 1.0,
            "REDBANNED": 1.0, "YELLOWREDBANNED": 1.0, "YELLOWBANNED": 1.0, "AWAY": 0.9, "RETIRED": 1.0}
COMPUTER_ID = "1"


def num(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def tactic_name(t):
    t = str(t or "").replace("-", "")
    return f"{t[0]}-{t[1]}-{t[2]}" if len(t) == 3 and t.isdigit() else t


# ---------------------------------------------------------------- Spielerhistorie
def parse_history(rows):
    """Sortiert die Spielhistorie (neueste zuerst) und bestimmt die Rolle je Spieltag."""
    def key(r):
        s = 10**9 if r.get("season") == "cur" else (num(r.get("season"), 0) or 0)
        return (s, num(r.get("md"), 0) or 0)
    out = []
    for r in sorted(rows or [], key=key, reverse=True):
        pts = num(r.get("points"))
        status = r.get("status") or "ACTIVE"
        if r.get("in") not in (None, ""):
            role = "sub"
        elif pts is not None:
            role = "start"
        elif status not in ("ACTIVE", "WEAKENED"):
            role = "unav"
        else:
            role = "none"
        opp, home = None, None
        if r.get("homeId") and r.get("guestId") and r.get("clubId"):
            home = r["clubId"] == r["homeId"]
            opp = r["guestId"] if home else r["homeId"]
        out.append({**r, "pts": pts, "role": role, "opp": opp, "home": home})
    return out


def position_means(histories, positions):
    acc = {p: {"start": [0.0, 0.0], "sub": [0.0, 0.0]} for p in POS_ORDER}
    for pid, hist in histories.items():
        pos = positions.get(pid)
        if pos not in acc:
            continue
        for i, r in enumerate(hist[:40]):
            if r["role"] in ("start", "sub") and r["pts"] is not None:
                w = 0.5 ** (i / HALF_LIFE)
                acc[pos][r["role"]][0] += w * r["pts"]
                acc[pos][r["role"]][1] += w
    means = {}
    for p, d in acc.items():
        means[p] = {k: (v[0] / v[1] if v[1] else (4.0 if k == "start" else 1.5)) for k, v in d.items()}
    return means


def opponent_factors(histories, base):
    """Abweichung vom eigenen Mittel je Gegner und Heim/Auswärts (stark gedämpft)."""
    dev, venue = defaultdict(list), {True: [], False: []}
    for pid, hist in histories.items():
        b = base.get(pid)
        if not b:
            continue
        for r in hist[:40]:  # etwa laufende und letzte Saison
            if r["role"] not in ("start", "sub") or r["pts"] is None or r["opp"] is None:
                continue
            expect = b["ppg_start"] if r["role"] == "start" else b["ppg_sub"]
            dev[r["opp"]].append(r["pts"] - expect)
            venue[bool(r["home"])].append(r["pts"] - expect)
    opp = {}
    for club, d in dev.items():
        n = len(d)
        val = statistics.fmean(d) * n / (n + OPP_SHRINK)
        opp[club] = max(-OPP_CAP, min(OPP_CAP, val))
    ven = {}
    for k, d in venue.items():
        n = len(d)
        ven[k] = max(-OPP_CAP, min(OPP_CAP, (statistics.fmean(d) * n / (n + OPP_SHRINK)) if n else 0.0))
    return opp, ven


def player_base(hist, pos, means, status, injury_return=None):
    w_start = w_sub = w_avail = w_unav = w_all = 0.0
    s_start = s_sub = n_s = n_b = 0.0
    yellow_cur, apps_cur = 0, 0
    for i, r in enumerate(hist[:40]):
        w = 0.5 ** (i / HALF_LIFE)
        w_all += w
        if r["role"] == "unav":
            w_unav += w
            continue
        w_avail += w
        if r["role"] == "start":
            w_start += w
            if r["pts"] is not None:
                s_start += w * r["pts"]
                n_s += w
        elif r["role"] == "sub":
            w_sub += w
            s_sub += w * (r["pts"] or 0.0)
            n_b += w
        if r.get("season") == "cur" and r["role"] in ("start", "sub"):
            apps_cur += 1
            yellow_cur += int(num(r.get("yellow"), 0) or 0)
    m = means.get(pos, {"start": 4.0, "sub": 1.5})
    ppg_start = (s_start + SHRINK_K * m["start"]) / (n_s + SHRINK_K)
    ppg_sub = (s_sub + SHRINK_K * m["sub"]) / (n_b + SHRINK_K)
    # Rollenwahrscheinlichkeit: wenig Daten -> vorsichtig (Prior: 30 % Start, 20 % Joker)
    p_start = (w_start + 0.6) / (w_avail + 2.0)
    p_sub = (w_sub + 0.4) / (w_avail + 2.0)
    base_unav = (w_unav + 0.1) / (w_all + 2.0)
    p_unav = max(UNAV_NOW.get(status or "ACTIVE", 0.3), min(base_unav, 0.25) if status == "ACTIVE" else 0)
    if injury_return:
        txt = injury_return.lower()
        if "einsatzbereit" in txt:
            p_unav = min(p_unav, 0.15)
    e_play = p_start * ppg_start + p_sub * ppg_sub
    # Gelbsperre: Wahrscheinlichkeit, bis Saisonende die 5. Gelbe zu sehen (vereinfacht, Poisson)
    return {"ppg_start": ppg_start, "ppg_sub": ppg_sub, "p_start": p_start, "p_sub": p_sub,
            "p_unav": p_unav, "base_unav": base_unav, "e_play": e_play,
            "yellow": yellow_cur, "apps": apps_cur}


def poisson_at_least(lmbda, k):
    if k <= 0:
        return 1.0
    p, term = 0.0, math.exp(-lmbda)
    for i in range(k):
        p += term
        term *= lmbda / (i + 1)
    return max(0.0, 1.0 - p)


def enrich_players(players, histories, means, opp, venue, remaining, injuries):
    """players: dict id -> {position, status, nextMatch, clubId, points, ...}"""
    out = {}
    for pid, p in players.items():
        hist = histories.get(pid, [])
        inj = (injuries.get(pid) or {}).get("return")
        b = player_base(hist, p.get("position"), means, p.get("status"), inj)
        nm = p.get("nextMatch") or {}
        adj, opp_name, venue_txt = 0.0, None, None
        cid = p.get("clubId")
        if nm.get("homeId") and cid:
            home = nm["homeId"] == cid
            other = nm["guestId"] if home else nm["homeId"]
            opp_name = nm.get("guest") if home else nm.get("home")
            venue_txt = "Heim" if home else "Auswärts"
            adj = max(-OPP_CAP, min(OPP_CAP, opp.get(other, 0.0) + venue.get(home, 0.0)))
        play = (b["p_start"] + 0.4 * b["p_sub"])
        e_base = (1 - b["p_unav"]) * b["e_play"]
        e_next = (1 - b["p_unav"]) * (b["e_play"] + adj * play)
        # Rest der Saison: Ausfallwahrscheinlichkeit erholt sich Richtung eigener Grundrate
        exp_rest, pu = 0.0, b["p_unav"]
        base = min(b["base_unav"], 0.25)
        for k in range(remaining):
            pk = pu * (0.6 ** k) + base * (1 - 0.6 ** k)
            exp_rest += (1 - pk) * b["e_play"]
        season_pts = num(p.get("points"), 0) or 0
        apps_left = remaining * (b["p_start"] + b["p_sub"])
        y_rate = (b["yellow"] + 0.5) / (b["apps"] + 4)
        p_ban = poisson_at_least(y_rate * apps_left, 5 - b["yellow"]) if b["yellow"] < 5 else 1.0
        p_inj5 = 1 - (1 - base) ** 5
        out[pid] = {**b, "e_base": round(e_base, 2), "e_next": round(e_next, 2), "adj": round(adj, 2),
                    "next_opp": opp_name, "next_venue": venue_txt, "exp_rest": round(exp_rest, 1),
                    "proj": round(season_pts + exp_rest, 1), "p_ban": round(p_ban, 3), "p_inj5": round(p_inj5, 3)}
    return out


# ---------------------------------------------------------------- Aufstellung
def best_lineup(squad, key="e_next", exclude=()):
    """squad: Liste {id, position, <key>}. Liefert beste Formation, Summe, IDs und alle Formationen."""
    by = {p: sorted([s for s in squad if s["position"] == p and s["id"] not in exclude],
                    key=lambda s: -s[key]) for p in POS_ORDER}
    gk = by["keeper"][:1]
    gk_sum = sum(s[key] for s in gk)
    results = {}
    for name, (d, m, f) in FORMATIONS.items():
        pick = gk + by["defender"][:d] + by["midfielder"][:m] + by["striker"][:f]
        results[name] = {"total": round(gk_sum + sum(s[key] for s in pick[len(gk):]), 2),
                         "ids": [s["id"] for s in pick], "complete": len(pick) == 11}
    best = max(results, key=lambda n: (results[n]["complete"], results[n]["total"]))
    return best, results[best]["total"], results[best]["ids"], results


def lineup_total(squad, ids, key="e_next"):
    look = {s["id"]: s for s in squad}
    return round(sum(look[i][key] for i in ids if i in look), 2)


# ---------------------------------------------------------------- Saisonprognose
def forecast(managers, squads_model, md_points, standings_pts, remaining, seed=7):
    rnd = random.Random(seed)
    rates, sds = {}, {}
    for m in managers:
        squad = squads_model.get(m, [])
        _, xi, _, _ = best_lineup(squad, key="e_base") if squad else (None, 0, None, None)
        hist = [v for v in md_points.get(m, []) if v is not None]
        avg = statistics.fmean(hist) if hist else xi
        rates[m] = 0.7 * xi + 0.3 * avg if hist else xi
        sds[m] = max(10.0, statistics.pstdev(hist) if len(hist) > 2 else 12.0)
        rates[m] = {"rate": rates[m], "xi": xi, "avg": avg}
    finals = {m: [] for m in managers}
    title, top3, rank_sum = defaultdict(int), defaultdict(int), defaultdict(float)
    for _ in range(SIMS):
        res = {}
        for m in managers:
            tot = standings_pts.get(m, 0)
            r, sd = rates[m]["rate"], sds[m]
            for _k in range(remaining):
                tot += rnd.gauss(r, sd)
            res[m] = tot
            finals[m].append(tot)
        order = sorted(managers, key=lambda x: -res[x])
        title[order[0]] += 1
        for i, m in enumerate(order):
            rank_sum[m] += i + 1
            if i < 3:
                top3[m] += 1
    out = []
    for m in managers:
        f = sorted(finals[m])
        out.append({"id": m, "pts_now": standings_pts.get(m, 0), "rate": round(rates[m]["rate"], 1),
                     "rate_squad": round(rates[m]["xi"], 1), "avg": round(rates[m]["avg"], 1),
                     "exp_final": round(statistics.fmean(f)), "p10": round(f[int(0.1 * SIMS)]),
                     "p90": round(f[int(0.9 * SIMS)]), "p_title": round(title[m] / SIMS, 3),
                     "p_top3": round(top3[m] / SIMS, 3), "exp_rank": round(rank_sum[m] / SIMS, 2),
                     "sd": round(sds[m], 1)})
    out.sort(key=lambda r: -r["exp_final"])
    for i, r in enumerate(out):
        r["rank_final"] = i + 1
    return out


# ---------------------------------------------------------------- Transfer-Pläne
def transfer_plans(own, market, balance, sale_price, buy_price, max_buys=3, n_sell=5, n_buy=12, top=8):
    """own/market: Listen mit id, name, position, e_next, e_base, marketValue."""
    if not own:
        return [], {}
    _, cur_total, _, _ = best_lineup(own, key="e_base")
    _, _, best_ids, _ = best_lineup(own, key="e_base")
    loss = {}
    for s in own:
        _, t, _, _ = best_lineup(own, key="e_base", exclude=(s["id"],))
        loss[s["id"]] = cur_total - t
    sell_cands = sorted(own, key=lambda s: (loss[s["id"]] + 0.2 * s["e_base"]) / max(sale_price(s), 1e5))[:n_sell]
    buy_cands = [m for m in market if m.get("e_base", 0) > 0.5]
    buy_cands = sorted(buy_cands, key=lambda m: -(m["e_base"] / max(buy_price(m), 1e5)))[:n_buy]
    plans = []
    for k_s in range(0, min(4, len(sell_cands)) + 1):
        for sells in itertools.combinations(sell_cands, k_s):
            proceeds = sum(sale_price(s) for s in sells)
            remaining = [s for s in own if s not in sells]
            for k_b in range(1, max_buys + 1):
                for buys in itertools.combinations(buy_cands, k_b):
                    cost = sum(buy_price(b) for b in buys)
                    cash = balance + proceeds - cost
                    if cash < 0:
                        continue
                    _, total, _, _ = best_lineup(remaining + list(buys), key="e_base")
                    delta = total - cur_total
                    if delta <= 0.2:
                        continue
                    plans.append({"sell": [s["name"] for s in sells], "buy": [b["name"] for b in buys],
                                  "sellIds": [s["id"] for s in sells], "buyIds": [b["id"] for b in buys],
                                  "proceeds": round(proceeds), "cost": round(cost), "cash_after": round(cash),
                                  "delta_md": round(delta, 2), "delta_3": round(3 * delta, 1)})
    plans.sort(key=lambda p: (-p["delta_md"], -p["cash_after"]))
    seen, out = set(), []
    for p in plans:
        sig = tuple(sorted(p["buy"]))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(p)
        if len(out) >= top:
            break
    for i, p in enumerate(out):
        p["id"] = f"{chr(65 + i)}"
        p["kind"] = "Verkaufen und kaufen" if p["sell"] else "Nur kaufen"
    return out, {s["id"]: round(loss[s["id"]], 2) for s in own}


# ---------------------------------------------------------------- Monatswertung
def monthly(md_rows, calendar, names):
    month_of = {}
    for c in calendar or []:
        if c.get("season") and c.get("key") and c.get("date"):
            month_of[str(c["key"])] = c["date"][:7]
    acc = defaultdict(lambda: defaultdict(int))
    for md in md_rows:
        mon = month_of.get(str(md["key"]))
        if not mon:
            continue
        for uid, p in md["points"].items():
            if p is not None:
                acc[mon][uid] += p
    out = []
    for mon in sorted(acc):
        ranking = sorted(acc[mon].items(), key=lambda kv: -kv[1])
        for i, (uid, pts) in enumerate(ranking):
            out.append({"month": mon, "rank": i + 1, "id": uid, "name": names.get(uid, uid), "points": pts})
    return out


# ---------------------------------------------------------------- Hilfen
def trend(quotes, days):
    """quotes: [[datum, wert], ...] neueste zuerst."""
    if not quotes or len(quotes) <= days:
        return None
    a, b = num(quotes[0][1]), num(quotes[days][1])
    if not a or not b:
        return None
    return round((a / b - 1) * 100, 1)


def age(dob, today=None):
    if not dob:
        return None
    try:
        d = datetime.fromisoformat(dob[:10])
    except ValueError:
        return None
    today = today or datetime.now()
    return round((today - d).days / 365.25, 1)


def hours_text(h):
    if h is None:
        return "–"
    h = int(h)
    d, r = divmod(h, 24)
    return f"{d} T. {r} Std." if d else f"{r} Std."
