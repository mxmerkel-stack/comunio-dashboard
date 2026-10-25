"""Nächster Spieltag: Paarungen (OpenLigaDB), Tipico-Quoten (The Odds API) und Abgleich mit Comunio-Vereinen."""
import difflib
import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (comunio-dashboard; private read-only)"
GENERIC = {"fc", "sv", "sc", "vfb", "vfl", "tsg", "1", "04", "05", "07", "borussia", "bayer", "1899", "fsv",
           "sport", "club", "de", "ev", "spvgg", "rb", "rasenballsport", "1846", "1848", "1900", "1909", "1910"}
ALIAS = {"munich": "munchen", "cologne": "koln", "mgladbach": "monchengladbach", "gladbach": "monchengladbach",
         "st": "sankt", "pauli": "pauli", "hamburg": "hamburger", "hsv": "hamburger", "leipzig": "leipzig"}


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def norm_tokens(name):
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    s = s.replace("ß", "ss")
    toks = [ALIAS.get(t, t) for t in re.split(r"[^a-z0-9]+", s) if t]
    core = [t for t in toks if t not in GENERIC]
    return set(core or toks)


def match_name(name, candidates):
    """Bestes Gegenstück zu name in candidates (Liste von Strings) oder None."""
    a = norm_tokens(name)
    best, score = None, 0.0
    for c in candidates:
        b = norm_tokens(c)
        j = len(a & b) / max(len(a | b), 1)
        if j == 0:
            j = 0.5 * difflib.SequenceMatcher(None, " ".join(sorted(a)), " ".join(sorted(b))).ratio()
        if j > score:
            best, score = c, j
    return best if score >= 0.34 else None


def fetch_fixtures(season_year, md):
    """OpenLigaDB: Paarungen eines Bundesliga-Spieltags. Gibt Liste oder None (bei Fehler) zurück."""
    try:
        data = _get(f"https://api.openligadb.de/getmatchdata/bl1/{season_year}/{md}")
    except Exception as e:  # Netz/Format
        print(f"::warning title=OpenLigaDB::{type(e).__name__}")
        return None
    out = []
    for m in data or []:
        if not isinstance(m, dict):
            continue
        res = [r for r in (m.get("matchResults") or []) if isinstance(r, dict)]
        final = next((r for r in res if r.get("resultTypeID") == 2), res[-1] if res else None)
        out.append({"home": (m.get("team1") or {}).get("teamName"), "away": (m.get("team2") or {}).get("teamName"),
                    "kickoff": m.get("matchDateTimeUTC") or m.get("matchDateTime"),
                    "finished": bool(m.get("matchIsFinished")),
                    "goalsHome": final.get("pointsTeam1") if final else None,
                    "goalsAway": final.get("pointsTeam2") if final else None,
                    "source": "OpenLigaDB"})
    return out


def fetch_season(season_year):
    """OpenLigaDB: alle Paarungen einer Bundesliga-Saison (für die Saisonsimulation). None bei Fehler."""
    try:
        data = _get(f"https://api.openligadb.de/getmatchdata/bl1/{season_year}", timeout=40)
    except Exception as e:
        print(f"::warning title=OpenLigaDB-Spielplan::{type(e).__name__}")
        return None
    out = []
    for m in data or []:
        if not isinstance(m, dict):
            continue
        res = [r for r in (m.get("matchResults") or []) if isinstance(r, dict)]
        final = next((r for r in res if r.get("resultTypeID") == 2), None)
        out.append({"md": (m.get("group") or {}).get("groupOrderID"),
                    "home": (m.get("team1") or {}).get("teamName"), "away": (m.get("team2") or {}).get("teamName"),
                    "kickoff": m.get("matchDateTimeUTC") or m.get("matchDateTime"),
                    "finished": bool(m.get("matchIsFinished")),
                    "goalsHome": final.get("pointsTeam1") if final else None,
                    "goalsAway": final.get("pointsTeam2") if final else None})
    return out


def fetch_odds():
    """The Odds API (Tipico DE, Siegwette 1/X/2). Nur wenn ODDS_API_KEY gesetzt ist."""
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        return None
    params = urllib.parse.urlencode({"apiKey": key, "bookmakers": "tipico_de", "markets": "h2h", "oddsFormat": "decimal"})
    try:
        data = _get(f"https://api.the-odds-api.com/v4/sports/soccer_germany_bundesliga/odds?{params}")
    except Exception as e:
        print(f"::warning title=Quoten::{type(e).__name__}")
        return None
    out = []
    for ev in data or []:
        if not isinstance(ev, dict):
            continue
        for bm in ev.get("bookmakers") or []:
            for mk in bm.get("markets") or []:
                if mk.get("key") != "h2h":
                    continue
                prices = {o.get("name"): o.get("price") for o in mk.get("outcomes") or []}
                out.append({"home": ev.get("home_team"), "away": ev.get("away_team"), "kickoff": ev.get("commence_time"),
                            "bookmaker": bm.get("title") or bm.get("key"),
                            "odd1": prices.get(ev.get("home_team")), "oddX": prices.get("Draw"),
                            "odd2": prices.get(ev.get("away_team"))})
    return out


def fixtures_from_comunio(players):
    """Fallback: Paarungen aus den nächsten Spielen der Comunio-Spieler ableiten."""
    seen = {}
    for p in players:
        nm = p.get("nextMatch") or {}
        if nm.get("homeId") and nm.get("guestId"):
            seen[(nm["homeId"], nm["guestId"])] = {"home": nm.get("home"), "away": nm.get("guest"),
                                                   "kickoff": nm.get("kickoff"), "finished": False,
                                                   "goalsHome": None, "goalsAway": None, "source": "Comunio"}
    return sorted(seen.values(), key=lambda f: f.get("kickoff") or "")


def implied(o1, ox, o2):
    try:
        a, b, c = 1 / float(o1), 1 / float(ox), 1 / float(o2)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    s = a + b + c
    return {"p1": round(a / s, 3), "pX": round(b / s, 3), "p2": round(c / s, 3), "margin": round((s - 1) * 100, 1)}
