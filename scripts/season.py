"""Saisonprognose auf Spielerebene (Paket 3) und Rückprüfung/Kalibrierung (Paket 4).

Grundidee
- Jeder Spieler hat ein Punkteniveau als Startelf- bzw. Jokerspieler, Einsatzwahrscheinlichkeiten und ein
  Ausfallrisiko (model.player_base).
- Vereinsstärke: aus allen Ergebnissen (laufende + letzte Saison) wird je Verein eine Tordifferenz-Stärke
  geschätzt. Für jedes kommende Spiel ergibt das eine erwartete Tordifferenz (Spielplan, Heimvorteil).
- Gemeinsame Schocks: je Spiel wird eine Tordifferenz gezogen, die alle Spieler beider Vereine betrifft
  (Gewinner-Spieler punkten besser). Dazu kommt ein Vereins-Schock je Spiel und das Einzelrauschen.
- Je Spieltag stellt jeder Manager aus den gerade fitten Spielern die nach Erwartung beste Elf auf.
- Rückprüfung: alle Parameter (Gewichtung der Vergangenheit, Mannschaftseffekt, Streuungen) werden aus
  vergangenen Spielen geschätzt – nur mit dem Wissen, das vor dem jeweiligen Spiel vorlag.
"""
import math
import re
import statistics
from collections import defaultdict
from datetime import date, timedelta

import numpy as np

import model as M

POS = M.POS_ORDER
SUB_W = 0.4          # Joker: Anteil an Niveau- und Vereinsschwankung
Q_OUT = 0.4          # Wahrscheinlichkeit je Spieltag, nach Ausfall wieder fit zu sein (Ø 2,5 Spieltage)
RATING_HL = 20.0     # Halbwertszeit (Spieltage) bei der Vereinsstärke
RATING_RIDGE = 4.0   # Pseudo-Spiele Richtung Durchschnitt
DRIFT_RHO = 0.9      # Kaderstärke: Abweichungen gleichen sich teilweise aus (Halbwertszeit ≈ 6–7 Spieltage)
GRID = ((6, 1.0), (6, 2.0), (12, 1.0), (12, 2.0), (12, 4.0), (24, 2.0), (24, 4.0))
DEFAULT_PRM = {"beta": {"keeper": 0.9, "defender": 0.9, "midfielder": 0.7, "striker": 0.6}, "beta_sub": 0.25,
               "sig_c": 1.5, "sig_ind": {"keeper": 4.0, "defender": 4.0, "midfielder": 4.5, "striker": 5.0},
               "sig_sub": 2.5, "sig_extra": 0.0, "bias_adj": 0.0, "sig_drift": 1.0, "sig_lvl": 0.8, "rho_drift": DRIFT_RHO}


# ---------------------------------------------------------------- Zeitachse
def tkey(season, md):
    """Sortierschlüssel (Saison, Spieltag); laufende Saison zuletzt."""
    s = 10**9 if season == "cur" else (M.num(season, 0) or 0)
    return (s, int(M.num(md, 0) or 0))


# ---------------------------------------------------------------- Vereinsstärke
def matches_from_histories(histories):
    out = {}
    for hist in histories.values():
        for r in hist:
            hg, gg = M.num(r.get("homeGoals")), M.num(r.get("guestGoals"))
            if hg is None or gg is None or not r.get("homeId") or not r.get("guestId"):
                continue
            key = r.get("matchId") or (r.get("season"), r.get("md"), r["homeId"], r["guestId"])
            out[key] = {"t": tkey(r.get("season"), r.get("md")), "home": str(r["homeId"]),
                        "guest": str(r["guestId"]), "gd": hg - gg}
    return list(out.values())


def team_ratings(matches, upto=None, hl=RATING_HL, ridge=RATING_RIDGE, iters=40):
    """Tordifferenz-Stärke je Verein (Mittel 0), Heimvorteil und Streuung der Tordifferenz.
    upto: nur Spiele vor diesem Zeitpunkt (Rückprüfung ohne Blick in die Zukunft)."""
    ms = [m for m in matches if upto is None or m["t"] < upto]
    if len(ms) < 10:
        return {}, 0.3, 1.7
    order = {k: i for i, k in enumerate(sorted({m["t"] for m in ms}))}
    last = len(order) - 1
    w = [0.5 ** ((last - order[m["t"]]) / hl) for m in ms]
    by_club = defaultdict(list)
    for wi, m in zip(w, ms):
        by_club[m["home"]].append((wi, 1, m["guest"], m["gd"]))
        by_club[m["guest"]].append((wi, -1, m["home"], m["gd"]))
    r = {c: 0.0 for c in by_club}
    hfa = 0.3
    sw = sum(w)
    for _ in range(iters):
        hfa = sum(wi * (m["gd"] - (r[m["home"]] - r[m["guest"]])) for wi, m in zip(w, ms)) / sw
        for c, lst in by_club.items():
            num_ = sum(wi * (sg * (gd - hfa) + r[o]) for wi, sg, o, gd in lst)
            den = sum(wi for wi, *_ in lst)
            r[c] = num_ / (den + ridge)
        mean = statistics.fmean(r.values())
        r = {c: v - mean for c, v in r.items()}
    res = [wi * (m["gd"] - (r[m["home"]] - r[m["guest"]] + hfa)) ** 2 for wi, m in zip(w, ms)]
    sd = math.sqrt(sum(res) / sw) if sw else 1.7
    return r, hfa, max(1.0, min(2.5, sd))


def exp_gd(ratings, hfa, club, opp, home):
    if club is None or opp is None:
        return None
    d = ratings.get(str(club), 0.0) - ratings.get(str(opp), 0.0)
    return d + hfa if home else d - hfa


# ---------------------------------------------------------------- Rückprüfung: Spielerebene
def _ols(xs, ys):
    n = len(xs)
    if n < 30:
        return None, None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-9:
        return None, None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return b, my - b * mx


def player_backtest(histories, positions, min_prior=3, max_rows=40):
    """Prognose jedes vergangenen Spiels nur aus den älteren Spielen. Liefert Kennzahlen, die beste
    Gewichtung (Halbwertszeit, Pseudo-Spiele) und die Streuungsparameter für die Simulation."""
    saved = (M.HALF_LIFE, M.SHRINK_K)
    plist = [(pid, positions.get(pid), h) for pid, h in histories.items() if positions.get(pid) in POS and h]
    results = []
    try:
        for hl, k in GRID:
            M.HALF_LIFE, M.SHRINK_K = hl, k
            means = M.position_means(histories, positions)
            se_c = se_s = n_c = n_s = 0.0
            for pid, pos, h in plist:
                for i in range(min(len(h), max_rows)):
                    prior = h[i + 1:]
                    if len(prior) < min_prior:
                        continue
                    r = h[i]
                    b = M.player_base(prior, pos, means, "ACTIVE")
                    y = r["pts"] if r["pts"] is not None else 0.0
                    e_s = (1 - min(b["base_unav"], 0.25)) * b["e_play"]
                    se_s += (y - e_s) ** 2
                    n_s += 1
                    if r["role"] != "unav":
                        se_c += (y - b["e_play"]) ** 2
                        n_c += 1
            if n_c:
                results.append({"hl": hl, "k": k, "rmse_cond": math.sqrt(se_c / n_c), "rmse_season": math.sqrt(se_s / n_s),
                                "n": int(n_c)})
    finally:
        M.HALF_LIFE, M.SHRINK_K = saved
    if not results:
        return None
    best = min(results, key=lambda x: x["rmse_cond"] ** 2 + x["rmse_season"] ** 2)
    return {"grid": results, "best": best}


def residual_params(histories, positions, matches, min_prior=3, max_rows=40):
    """Mit der gewählten Gewichtung: Mannschaftseffekt, Vereins-Schock, Einzelstreuung, Vergleich mit
    einfachen Verfahren und Nutzen des Spielplans (erwartete Tordifferenz vor dem Spiel)."""
    means = M.position_means(histories, positions)
    rating_cache = {}

    def ratings_at(t):
        if t not in rating_cache:
            rating_cache[t] = team_ratings(matches, upto=t)
        return rating_cache[t]

    rows = []  # (pos, role, resid, x_real, x_exp, club_match_key, e_play, y, base_avg, p_s, p_b)
    for pid, h in histories.items():
        pos = positions.get(pid)
        if pos not in POS:
            continue
        for i in range(min(len(h), max_rows)):
            prior = h[i + 1:]
            if len(prior) < min_prior:
                continue
            r = h[i]
            if r["role"] == "unav":
                continue
            b = M.player_base(prior, pos, means, "ACTIVE")
            y = r["pts"] if r["pts"] is not None else 0.0
            pp = [x["pts"] if x["pts"] is not None else 0.0 for x in prior[:10] if x["role"] != "unav"]
            base_avg = statistics.fmean(pp) if pp else 0.0
            t = tkey(r.get("season"), r.get("md"))
            rt, hfa, _sd = ratings_at(t)
            club = str(r.get("clubId")) if r.get("clubId") else None
            x_real = x_exp = None
            if rt and club and r.get("gd") is not None:
                x_real = r["gd"] - rt.get(club, 0.0)
                eg = exp_gd(rt, hfa, club, r.get("opp"), r.get("home"))
                x_exp = (eg - rt.get(club, 0.0)) if eg is not None else None
            ref = b["ppg_start"] if r["role"] == "start" else (b["ppg_sub"] if r["role"] == "sub" else None)
            rows.append({"pid": pid, "pos": pos, "role": r["role"], "resid": (y - ref) if ref is not None else None,
                         "x_real": x_real, "x_exp": x_exp, "cm": (r.get("matchId") or (r.get("season"), r.get("md")), club),
                         "e": b["e_play"], "y": y, "avg": base_avg, "p_s": b["p_start"], "p_b": b["p_sub"],
                         "cur": r.get("season") == "cur"})
    prm = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_PRM.items()}
    info = {"n_rows": len(rows)}
    if len(rows) < 200:
        return prm, info
    # Mannschaftseffekt (Punkte je Tor Tordifferenz über dem Vereinsschnitt)
    for pos in POS:
        sub = [r for r in rows if r["pos"] == pos and r["role"] == "start" and r["x_real"] is not None]
        b, _ = _ols([r["x_real"] for r in sub], [r["resid"] for r in sub])
        if b is not None:
            prm["beta"][pos] = round(max(0.0, min(2.5, b)), 3)
    sub = [r for r in rows if r["role"] == "sub" and r["x_real"] is not None]
    b, _ = _ols([r["x_real"] for r in sub], [r["resid"] for r in sub])
    if b is not None:
        prm["beta_sub"] = round(max(0.0, min(1.5, b)), 3)

    def clean(r):
        beta = prm["beta"][r["pos"]] if r["role"] == "start" else prm["beta_sub"]
        return r["resid"] - beta * (r["x_real"] or 0.0)

    # Vereins-Schock: mittleres Produkt der Restabweichungen von Startelfspielern desselben Vereins im selben Spiel
    groups = defaultdict(list)
    for r in rows:
        if r["role"] == "start" and r["x_real"] is not None:
            groups[r["cm"]].append(clean(r))
    num_ = den = 0.0
    for g in groups.values():
        n = len(g)
        if n >= 2:
            s1, s2 = sum(g), sum(v * v for v in g)
            num_ += s1 * s1 - s2
            den += n * (n - 1)
    sig_c2 = max(0.0, num_ / den) if den else prm["sig_c"] ** 2
    prm["sig_c"] = round(math.sqrt(sig_c2), 3)
    # Bleibender Fehler im Spielerniveau: mittleres Produkt der Restabweichungen desselben Spielers in verschiedenen Spielen
    by_p = defaultdict(list)
    for r in rows:
        if r["role"] == "start" and r["x_real"] is not None:
            by_p[r["pid"]].append(clean(r))
    num_ = den = 0.0
    for g in by_p.values():
        n = len(g)
        if n >= 2:
            s1, s2 = sum(g), sum(v * v for v in g)
            num_ += s1 * s1 - s2
            den += n * (n - 1)
    sig_l2 = max(0.0, num_ / den) if den else prm["sig_lvl"] ** 2
    prm["sig_lvl"] = round(math.sqrt(sig_l2), 3)
    for pos in POS:
        v = [clean(r) for r in rows if r["pos"] == pos and r["role"] == "start" and r["x_real"] is not None]
        if len(v) > 50:
            prm["sig_ind"][pos] = round(math.sqrt(max(1.0, statistics.pvariance(v) - sig_c2 - sig_l2)), 3)
    v = [clean(r) for r in rows if r["role"] == "sub" and r["x_real"] is not None]
    if len(v) > 50:
        prm["sig_sub"] = round(math.sqrt(max(0.5, statistics.pvariance(v) - SUB_W ** 2 * (sig_c2 + sig_l2))), 3)

    # Prognosegüte (fitte Spieler, laufende + letzte Saison): Modell vs. einfacher Schnitt, mit/ohne Spielplan
    def rmse(pairs):
        return math.sqrt(statistics.fmean([(a - b) ** 2 for a, b in pairs])) if pairs else None

    with_x = [r for r in rows if r["x_exp"] is not None]
    adj = lambda r: r["e"] + r["p_s"] * prm["beta"][r["pos"]] * r["x_exp"] + r["p_b"] * prm["beta_sub"] * r["x_exp"]
    slope, icpt = _ols([r["e"] for r in rows], [r["y"] for r in rows])
    info.update({
        "rmse_model": rmse([(r["y"], r["e"]) for r in rows]),
        "rmse_avg10": rmse([(r["y"], r["avg"]) for r in rows]),
        "rmse_model_cur": rmse([(r["y"], r["e"]) for r in rows if r["cur"]]),
        "rmse_avg10_cur": rmse([(r["y"], r["avg"]) for r in rows if r["cur"]]),
        "rmse_no_fixture": rmse([(r["y"], r["e"]) for r in with_x]),
        "rmse_fixture": rmse([(r["y"], adj(r)) for r in with_x]),
        "bias": statistics.fmean([r["y"] - r["e"] for r in rows]),
        "slope": slope, "intercept": icpt, "n_fixture": len(with_x), "n_groups": len(groups)})
    return prm, info


# ---------------------------------------------------------------- Simulation eines Spieltags (gemeinsam genutzt)
class Squads:
    """Spieler-Tabellen als Arrays für schnelle Simulation."""

    def __init__(self, pinfo, squads):
        self.pids = sorted({p for ids in squads.values() for p in ids if p in pinfo})
        self.ix = {p: i for i, p in enumerate(self.pids)}
        g = lambda k: np.array([pinfo[p][k] for p in self.pids], dtype=float)
        self.ppg_s, self.ppg_b, self.p_s, self.p_b = g("ppg_s"), g("ppg_b"), g("p_s"), g("p_b")
        self.se = g("se")
        self.pos = [pinfo[p]["pos"] for p in self.pids]
        self.club = [pinfo[p].get("club") for p in self.pids]
        self.members = {m: [self.ix[p] for p in ids if p in self.ix] for m, ids in squads.items()}


def _lineups(sq, m, avail, e_cond):
    """Beste Elf je Verfügbarkeits-Muster. avail: S×P bool. Liefert (Auswahl S×n, fehlende Plätze S, Spieler-Indizes)."""
    idx = sq.members.get(m, [])
    S = avail.shape[0]
    if not idx:
        return np.zeros((S, 0)), np.full(S, 11), idx
    sub = avail[:, idx]
    keys = sub.astype(np.int64) @ (np.int64(1) << np.arange(len(idx), dtype=np.int64))
    uniq, inv = np.unique(keys, return_inverse=True)
    sel_u = np.zeros((len(uniq), len(idx)))
    miss_u = np.zeros(len(uniq))
    for u, key in enumerate(uniq):
        pool = [{"id": j, "position": sq.pos[idx[j]], "e": float(e_cond[idx[j]])}
                for j in range(len(idx)) if (int(key) >> j) & 1]
        if pool:
            _, _, ids, _ = M.best_lineup(pool, key="e")
            sel_u[u, ids] = 1.0
            miss_u[u] = 11 - len(ids)
        else:
            miss_u[u] = 11
    return sel_u[inv], miss_u[inv], idx


def simulate_md(sq, prm, rng, avail, x_exp, fixtures_md, ratings, hfa, sd_gd, level, managers, club_ix):
    """Ein Spieltag. avail: S×P bool; x_exp: erwartete Tordifferenz minus Vereinsschnitt je Spieler (P).
    fixtures_md: [(heim, gast)] mit Vereins-IDs. level: S×P Niveau-Unsicherheit. Liefert {manager: S Punkte}."""
    S, P = avail.shape
    beta = np.array([prm["beta"].get(p, 0.7) for p in sq.pos])
    sig = np.array([prm["sig_ind"].get(p, 4.5) for p in sq.pos])
    e_cond = (sq.p_s * (sq.ppg_s + beta * x_exp) + sq.p_b * (sq.ppg_b + prm["beta_sub"] * x_exp))
    # gemeinsame Schocks: Tordifferenz je Spiel, Vereins-Schock je Verein
    nclub = len(club_ix)
    gd_club = np.zeros((S, nclub))
    has_fix = np.zeros(nclub, dtype=bool)
    for h, g in fixtures_md:
        if h in club_ix and g in club_ix:
            eg = ratings.get(h, 0.0) - ratings.get(g, 0.0) + hfa
            d = eg + sd_gd * rng.standard_normal(S)
            gd_club[:, club_ix[h]] = d
            gd_club[:, club_ix[g]] = -d
            has_fix[club_ix[h]] = has_fix[club_ix[g]] = True
    rvec = np.array([ratings.get(c, 0.0) for c in club_ix])
    shock = prm["sig_c"] * rng.standard_normal((S, nclub))
    pc = np.array([club_ix.get(c, -1) for c in sq.club])
    known = (pc >= 0)
    x_real = np.zeros((S, P))
    u_club = np.zeros((S, P))
    if known.any():
        cols = pc[known]
        fx = has_fix[cols]
        x_real[:, known] = np.where(fx, gd_club[:, cols] - rvec[cols], 0.0)
        u_club[:, known] = shock[:, cols]
    u = rng.random((S, P))
    start = u < sq.p_s
    sub = (~start) & (u < sq.p_s + sq.p_b)
    pts = np.where(start, sq.ppg_s + level + beta * x_real + u_club + sig * rng.standard_normal((S, P)), 0.0)
    pts = pts + np.where(sub, sq.ppg_b + SUB_W * level + prm["beta_sub"] * x_real + SUB_W * u_club
                         + prm["sig_sub"] * rng.standard_normal((S, P)), 0.0)
    pts = np.where(avail, pts, 0.0)
    out = {}
    for m in managers:
        sel, miss, idx = _lineups(sq, m, avail, e_cond)
        tot = (sel * pts[:, idx]).sum(axis=1) if idx else np.zeros(S)
        out[m] = tot - M.EMPTY_SLOT_PENALTY * miss
    return out, e_cond


def player_info(pid, hist, pos, means, status, club, injury=None, sig_lvl=0.8):
    """se: Unsicherheit des Spielerniveaus – im Mittel sig_lvl (aus der Rückprüfung), mehr bei wenig Daten."""
    b = M.player_base(hist, pos, means, status, injury)
    n = b["n_start"] + b["n_sub"]
    se = sig_lvl * math.sqrt((15.0 + M.SHRINK_K) / (n + M.SHRINK_K))
    return {"pos": pos, "club": club, "ppg_s": b["ppg_start"], "ppg_b": b["ppg_sub"], "p_s": b["p_start"],
            "p_b": b["p_sub"], "p_unav": b["p_unav"], "base_unav": min(b["base_unav"], 0.25),
            "se": max(0.5 * sig_lvl, min(2.0 * sig_lvl, se))}


# ---------------------------------------------------------------- Rückprüfung: Managerebene (laufende Saison)
def manager_backtest(owners, histories, positions, matches, actual, prm, sims=1500, seed=11):
    """Für jeden gespielten Spieltag: Kader beim Anpfiff, Spielerwerte nur aus älteren Spielen, bekannte Ausfälle.
    Vergleicht die simulierte Verteilung mit den echten Punkten."""
    rng = np.random.default_rng(seed)
    means = M.position_means(histories, positions)
    recs = []
    for md, per in sorted(owners.items(), key=lambda kv: int(kv[0])):
        tk = tkey("cur", md)
        rt, hfa, sd_gd = team_ratings(matches, upto=tk)
        pinfo, avail_now, fixtures, x_exp = {}, {}, set(), {}
        for uid, pids in per.items():
            for pid in pids:
                h = histories.get(pid)
                pos = positions.get(pid)
                if not h or pos not in POS:
                    continue
                cur = next((r for r in h if r.get("season") == "cur" and str(int(M.num(r.get("md"), 0) or 0)) == str(md)), None)
                prior = [r for r in h if tkey(r.get("season"), r.get("md")) < tk]
                club = str(cur["clubId"]) if cur and cur.get("clubId") else None
                pinfo[pid] = player_info(pid, prior, pos, means, "ACTIVE", club, sig_lvl=prm["sig_lvl"])
                avail_now[pid] = not (cur and cur["role"] == "unav")
                if cur and cur.get("homeId") and cur.get("guestId"):
                    fixtures.add((str(cur["homeId"]), str(cur["guestId"])))
                    eg = exp_gd(rt, hfa, club, cur.get("opp"), cur.get("home")) if rt else None
                    x_exp[pid] = (eg - rt.get(club, 0.0)) if eg is not None else 0.0
        # nur Kader, deren Spieler fast alle Daten haben (sonst fehlen Spieler und die Prognose wäre zu niedrig)
        mgrs = [u for u in per if (actual.get(u) or {}).get(md) is not None and per[u]
                and sum(1 for p in per[u] if p in pinfo) / len(per[u]) >= 0.9]
        if not pinfo or not mgrs:
            continue
        sq = Squads(pinfo, {u: per[u] for u in mgrs})
        if not sq.pids:
            continue
        clubs = sorted({c for c in sq.club if c} | {c for f in fixtures for c in f})
        club_ix = {c: i for i, c in enumerate(clubs)}
        avail = np.tile(np.array([avail_now[p] for p in sq.pids]), (sims, 1))
        level = rng.standard_normal((sims, len(sq.pids))) * sq.se
        xe = np.array([x_exp.get(p, 0.0) for p in sq.pids])
        out, _ = simulate_md(sq, prm, rng, avail, xe, sorted(fixtures), rt, hfa, sd_gd, level, mgrs, club_ix)
        for u in mgrs:
            a = actual[u][md]
            v = out[u]
            recs.append({"md": int(md), "uid": u, "actual": a, "mean": float(v.mean()), "var": float(v.var()),
                         "p10": float(np.quantile(v, 0.1)), "p90": float(np.quantile(v, 0.9)),
                         "pit": float((v < a).mean() + 0.5 * (v == a).mean())})
    if not recs:
        return None
    err = [r["actual"] - r["mean"] for r in recs]
    mse = statistics.fmean([e * e for e in err])
    var = statistics.fmean([r["var"] for r in recs])
    n = len(recs)
    return {"n": n, "bias": statistics.fmean(err), "rmse": math.sqrt(mse), "sd_pred": math.sqrt(var),
            "cover80": sum(1 for r in recs if r["p10"] <= r["actual"] <= r["p90"]) / n,
            "sig_extra": math.sqrt(max(0.0, mse - statistics.fmean(err) ** 2 - var)),
            "bias_adj": statistics.fmean(err) * n / (n + 20.0), "records": recs}


def squad_drift(owners, squads_now, model_e, positions):
    """Wie stark ändert sich die Stärke der besten Elf eines Kaders von Spieltag zu Spieltag (Transfers)?
    Bewertet mit dem heutigen Spielermodell, damit nur die Kaderwechsel zählen."""
    def strength(pids):
        pool = [{"id": p, "position": positions.get(p), "e": model_e[p]} for p in pids
                if positions.get(p) in POS and p in model_e]
        if not pool or len(pool) < 0.9 * len(pids):
            return None
        _, tot, ids, _ = M.best_lineup(pool, key="e")
        return tot - M.EMPTY_SLOT_PENALTY * (11 - len(ids))

    seq = sorted(owners, key=lambda k: int(k))
    steps, pairs = [], []
    for uid in squads_now:
        vals = [strength(owners[md].get(uid, set())) for md in seq] + [strength(set(squads_now[uid]))]
        vals = [v for v in vals if v is not None]
        d = [b - a for a, b in zip(vals, vals[1:])]
        steps += d
        pairs += list(zip(d, d[1:]))
    if len(steps) < 5:
        return None
    mu = statistics.fmean(steps)
    var = statistics.fmean([(x - mu) ** 2 for x in steps])
    sd = math.sqrt(var)
    # Ausgleich: Bei einer Stärke, die um ein eigenes Niveau schwankt (AR(1) mit rho), korrelieren
    # aufeinanderfolgende Änderungen mit −(1−rho)/2. Daraus rho und die Stärke der einzelnen Stöße.
    corr = None
    if len(pairs) >= 10 and var > 0:
        corr = statistics.fmean([(a - mu) * (b - mu) for a, b in pairs]) / var
        rho = max(0.0, min(0.95, 1 + 2 * corr))
    else:
        rho = DRIFT_RHO
    sig = sd * math.sqrt((1 + rho) / 2)
    return {"mean": round(mu, 2), "sd": round(sd, 2), "corr": None if corr is None else round(corr, 3),
            "rho": round(rho, 3), "sig": round(sig, 2), "n": len(steps)}


# ---------------------------------------------------------------- Spielplan
def build_fixtures(raw, histories, club_names, remaining_mds, match_name):
    """{Spieltag: [(heim, gast)]} für die restlichen Spieltage. Quellen: OpenLigaDB (ganze Saison),
    sonst nächste Spiele aus Comunio und die gespiegelte Hinrunde."""
    fx = defaultdict(set)
    src = defaultdict(str)
    names = {cid: n for cid, n in club_names.items() if n}
    lookup = list(names.values())
    rev = {n: cid for cid, n in names.items()}
    unmatched = 0
    for f in raw.get("seasonFixtures") or []:
        md = f.get("md")
        if md not in remaining_mds:
            continue
        h = match_name(f.get("home"), lookup)
        g = match_name(f.get("away"), lookup)
        if h and g and h != g:
            fx[md].add((rev[h], rev[g]))
            src[md] = "OpenLigaDB"
        else:
            unmatched += 1
    # Hinrunde gespiegelt (Bundesliga: Rückrunde in gleicher Reihenfolge, Heimrecht getauscht)
    played = defaultdict(set)
    for h in histories.values():
        for r in h:
            if r.get("season") == "cur" and r.get("homeId") and r.get("guestId"):
                played[int(M.num(r.get("md"), 0) or 0)].add((str(r["homeId"]), str(r["guestId"])))
    for md in remaining_mds:
        if fx.get(md):
            continue
        if md > 17 and played.get(md - 17):
            fx[md] = {(g, h) for h, g in played[md - 17]}
            src[md] = "Hinrunde gespiegelt"
    # nächstes Spiel aus den Spielerdaten (Comunio), falls OpenLigaDB fehlt
    if remaining_mds and not fx.get(remaining_mds[0]):
        for p in (raw.get("market") or []) + [x for sq in (raw.get("squads") or {}).values() for x in sq]:
            nm = p.get("nextMatch") or {}
            if nm.get("homeId") and nm.get("guestId"):
                fx[remaining_mds[0]].add((str(nm["homeId"]), str(nm["guestId"])))
                src[remaining_mds[0]] = "Comunio"
    fx = {k: sorted(v) for k, v in fx.items() if v}
    return fx, {"mds_with_fixtures": sum(1 for md in remaining_mds if fx.get(md)), "unmatched": unmatched,
                "sources": sorted({v for v in src.values() if v})}


# ---------------------------------------------------------------- Saisonsimulation
def season_forecast(managers, squads, pinfo, fixtures, ratings, hfa, sd_gd, prm, remaining_mds, extras,
                    standings, md_points, sims=3000, seed=7, odds_gd=None):
    """Simuliert alle restlichen Spieltage auf Spielerebene. Ausgabe wie model.forecast (+ Platzverteilung)."""
    rng = np.random.default_rng(seed)
    sq = Squads(pinfo, squads)
    P = len(sq.pids)
    clubs = sorted({c for c in sq.club if c} | {c for fl in fixtures.values() for f in fl for c in f})
    club_ix = {c: i for i, c in enumerate(clubs)}
    S = sims
    level = rng.standard_normal((S, P)) * sq.se
    p_un0 = np.array([pinfo[p]["p_unav"] for p in sq.pids])
    base = np.array([pinfo[p]["base_unav"] for p in sq.pids])
    q_in = np.clip(base * Q_OUT / np.maximum(1e-6, 1 - base), 0, 0.5)
    out_md = np.array([(-1 if pinfo[p].get("out_mds") is None else pinfo[p]["out_mds"]) for p in sq.pids], dtype=int)
    known = out_md >= 0
    delay = (rng.random((S, P)) < 0.3).astype(int) * (1 + (rng.random((S, P)) < 0.4).astype(int))  # Rückkehr verzögert sich oft
    unav = rng.random((S, P)) < p_un0
    if known.any():
        unav = np.where(known, (out_md + delay > 0) | (rng.random((S, P)) < base), unav)
    totals = {m: np.full(S, float(standings.get(m, 0) or 0)) for m in managers}
    drift = {m: np.zeros(S) for m in managers}
    md_mean = {m: [] for m in managers}
    raw_mean = {m: [] for m in managers}
    for k, md in enumerate(remaining_mds):
        if k > 0:  # Ausfälle entwickeln sich weiter (Markov: verletzt bleibt oft verletzt)
            rec = rng.random((S, P)) < Q_OUT
            new = rng.random((S, P)) < q_in
            unav = np.where(unav, ~rec, new)
            if known.any():  # bekannte Rückkehr: bis dahin fehlt der Spieler, danach nur das übliche neue Verletzungsrisiko
                unav = np.where(known, np.where(k < out_md + delay, True, new), unav)
        fl = fixtures.get(md, [])
        opp = {}
        for h, g in fl:
            opp[h] = (g, True)
            opp[g] = (h, False)
        xe = np.zeros(P)
        for i, c in enumerate(sq.club):
            if c in opp:
                o, home = opp[c]
                eg = (odds_gd or {}).get((md, c))
                if eg is None:
                    eg = exp_gd(ratings, hfa, c, o, home)
                xe[i] = eg - ratings.get(c, 0.0)
        out, _ = simulate_md(sq, prm, rng, ~unav, xe, fl, ratings, hfa, sd_gd, level, managers, club_ix)
        for m in managers:
            ex = extras.get(m, {})
            raw_mean[m].append(float(out[m].mean()))
            if k > 0:  # Kaderwechsel (z. B. Winter-Transfers): Stärke wandert, gleicht sich aber teilweise aus
                drift[m] = prm.get("rho_drift", DRIFT_RHO) * drift[m] + prm["sig_drift"] * rng.standard_normal(S)
            v = (out[m] * ex.get("disc", 1.0) + ex.get("gain", 0.0) + prm["bias_adj"] + drift[m]
                 + prm["sig_extra"] * rng.standard_normal(S))
            pz = ex.get("p_zero", 0.0)
            if pz:
                v = np.where(rng.random(S) < pz, 0.0, v)
            md_mean[m].append(float(v.mean()))
            totals[m] = totals[m] + v
    mat = np.vstack([totals[m] for m in managers])        # M×S
    order = np.argsort(-mat, axis=0)                       # Platz → Manager-Index
    ranks = np.empty_like(order)
    for pos_ in range(len(managers)):
        ranks[order[pos_], np.arange(S)] = pos_
    out = []
    for i, m in enumerate(managers):
        f = np.sort(mat[i])
        hist = [v for v in md_points.get(m, []) if v is not None]
        ex = extras.get(m, {})
        p_rank = [float((ranks[i] == r).mean()) for r in range(len(managers))]
        per_md = np.diff(np.vstack([np.full(S, float(standings.get(m, 0) or 0)), mat[i]]), axis=0)
        out.append({"id": m, "pts_now": standings.get(m, 0),
                    "rate": round(statistics.fmean(md_mean[m]), 1) if md_mean[m] else 0.0,
                    "rate_squad": round(raw_mean[m][0], 1) if raw_mean[m] else 0.0,
                    "avg": round(statistics.fmean(hist), 1) if hist else 0.0,
                    "disc": ex.get("disc", 1.0), "gain": round(ex.get("gain", 0.0), 2),
                    "p_zero": round(ex.get("p_zero", 0.0), 3), "missing": 0,
                    "exp_final": round(float(f.mean())), "p10": round(float(f[int(0.1 * S)])),
                    "p90": round(float(f[int(0.9 * S)])), "p_title": round(p_rank[0], 3),
                    "p_top3": round(sum(p_rank[:3]), 3), "exp_rank": round(float(ranks[i].mean()) + 1, 2),
                    "p_rank": [round(x, 3) for x in p_rank],
                    "sd": round(float(np.std(f)) / max(1, len(remaining_mds)) ** 0.5, 1) if remaining_mds else 0.0,
                    "sd_level": round(float(np.std(f)), 1), "md_mean": [round(x, 1) for x in md_mean[m]]})
    out.sort(key=lambda r: -r["exp_final"])
    for i, r in enumerate(out):
        r["rank_final"] = i + 1
    return out


# ---------------------------------------------------------------- Rückkehr-Angaben aus der Recherche
MONTHS = {"januar": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7,
          "august": 8, "september": 9, "sept": 9, "oktober": 10, "okt": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12}


def parse_return(text, today):
    """Voraussichtliches Rückkehrdatum aus Texten wie „ca. 28.–31.10.“, „Anfang November“, „in 2–3 Wochen“.
    Gibt ein date zurück, today bei „einsatzbereit“, None bei unklaren Angaben. Bei Spannen gilt das spätere Ende."""
    t = (text or "").lower().strip()
    if not t or re.search(r"unklar|offen|unbekannt|ungewiss|n/?a\b", t):
        return None
    if re.search(r"einsatzbereit|fit\b|zurück im training|wieder dabei", t):
        return today

    def fix(d, m, y=None):
        try:
            dt = date(y or today.year, m, d)
        except ValueError:
            return None
        if y is None and dt < today - timedelta(days=90):
            dt = date(today.year + 1, m, d)
        return dt
    m = re.search(r"(\d{1,2})\.\s*(?:–|-|bis)\s*(\d{1,2})\.(\d{1,2})\.?(\d{4})?", t)
    if m:
        return fix(int(m.group(2)), int(m.group(3)), int(m.group(4)) if m.group(4) else None)
    ds = [fix(int(a), int(b), int(c) if c else None) for a, b, c in re.findall(r"(\d{1,2})\.(\d{1,2})\.(\d{4})?", t)]
    ds = [d for d in ds if d]
    if ds:
        return max(ds)
    m = re.search(r"in\s*(\d+)(?:\s*(?:–|-|bis)\s*(\d+))?\s*wochen", t) or re.search(r"(\d+)(?:\s*(?:–|-|bis)\s*(\d+))?\s*wochen", t)
    if m:
        return today + timedelta(days=7 * int(m.group(2) or m.group(1)))
    m = re.search(r"(\d+)(?:\s*(?:–|-|bis)\s*(\d+))?\s*monat", t)
    if m:
        return today + timedelta(days=30 * int(m.group(2) or m.group(1)))
    if re.search(r"saisonende|saison\s*aus|season", t):
        return date(today.year + (1 if today.month > 6 else 0), 6, 1)
    if "winterpause" in t:
        return date(today.year + (1 if today.month > 6 else 0), 1, 10)
    if "jahresende" in t:
        return date(today.year, 12, 31)
    for name, mon in MONTHS.items():
        if re.search(rf"\b{name}\b", t):
            day = 5 if "anfang" in t else 25 if "ende" in t else 15
            return fix(day, mon)
    return None


def out_matchdays(ret, md_dates, today):
    """Wie viele der kommenden Spieltage (md_dates: Liste Aufstellungsschluss, aufsteigend) liegen vor der Rückkehr?"""
    if ret is None:
        return None
    return sum(1 for d in md_dates if d.date() < ret) if ret > today else 0
