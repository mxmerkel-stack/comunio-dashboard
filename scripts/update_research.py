#!/usr/bin/env python3
"""Aktualisiert research/rumors.json und research/injuries.json per Claude-API mit Websuche.

Backends (Vorrang): GROQ_API_KEY (kostenlos, Suche über TAVILY_API_KEY oder Google-News-RSS), GEMINI_API_KEY, ANTHROPIC_API_KEY. Die Spielerliste ist immer der aktuelle Kader aus data/raw.json. Fehlt es oder schlägt ein Aufruf fehl, bleiben die
bisherigen Einträge unverändert (Exit-Code 0, Warnung als Annotation).
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = os.environ.get("RESEARCH_MODEL", "claude-sonnet-5-5")
KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GROQ_KEY = os.environ.get("GROQ_API_KEY", "").strip()
MODEL_GROQ = os.environ.get("RESEARCH_MODEL_GROQ", "")
TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "").strip()
MODEL_GEMINI = os.environ.get("RESEARCH_MODEL_GEMINI", "gemini-3.8-flash")
BATCH = 5
TODAY = dt.datetime.now(dt.timezone(dt.timedelta(hours=2))).strftime("%d.%m.%Y")

RUMOR_FIELDS = ["target", "probability", "note", "source", "url", "status", "pWinter", "pSummer"]
INJURY_FIELDS = ["return", "note", "source", "url"]

RUMOR_TASK = """Recherchiere per Websuche (transfermarkt.de, kicker.de, bild.de, sport1, sky, Vereinsseiten) die aktuellen Transfergerüchte
für diese Bundesliga-Spieler. Heutiges Datum: {today}. Schätze für jeden die Wahrscheinlichkeit eines Vereinswechsels im
kommenden Winter (pWinter) und im kommenden Sommer (pSummer) als Zahl 0..1, vorsichtig und aus den Quellen begründet;
"probability" ist der höhere der beiden Werte. Ohne belastbare neue Quelle den bisherigen Eintrag beibehalten.
Felder je Spieler: target (Zielverein/e oder ""), probability, pWinter, pSummer, note (2-3 Sätze, deutsch, mit Datum der Meldung),
source (Medium + Datum), url (echte, aufgerufene URL), status ("Gerücht", "Altes Gerücht", "Konkret", "Kein Gerücht").
Spieler (ID: Name, bisheriger Eintrag):
{players}"""

INJURY_TASK = """Recherchiere per Websuche (transfermarkt.de Verletzungshistorie, kicker.de, ligainsider, Vereinsseiten) den aktuellen
Verletzungs-/Reha-/Sperrenstand dieser Bundesliga-Spieler. Heutiges Datum: {today}. Felder je Spieler: return (voraussichtliche Rückkehr,
kurz, z. B. "ca. 08.11."), note (1-2 Sätze deutsch: Art der Verletzung, seit wann, Stand), source (Medium + Datum), url (echte URL).
Ist ein Spieler wieder fit und im Kader, setze "fit": true (dann ohne weitere Felder).
Spieler (ID: Name, bisheriger Eintrag):
{players}"""

OUT = 'Antworte ausschließlich mit einem JSON-Objekt {"<ID>": {...Felder...}, ...} ohne Erklärtext und ohne Markdown-Zaun.'


def _post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"content-type": "application/json", **headers})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 503) or attempt >= 1:
                try:
                    msg = json.loads(e.read().decode()).get("error", {}).get("message", "")[:700]
                except Exception:
                    msg = ""
                raise RuntimeError(f"HTTP {e.code} {msg}") from None
            if True:
                time.sleep(20)  # Rate-Limit des Gratis-Tarifs
                continue
            raise


def _get_json(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


GROQ_CANDIDATES = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "moonshotai/kimi-k2-instruct",
                   "meta-llama/llama-4-maverick-17b-128e-instruct", "meta-llama/llama-4-scout-17b-16e-instruct",
                   "qwen/qwen3-32b", "deepseek-r1-distill-llama-70b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]


def groq_models():
    """Kandidatenliste: erst die bei Groq gemeldeten Modelle (falls der Schlüssel sie lesen darf), sonst bekannte Namen."""
    if MODEL_GROQ:
        return [MODEL_GROQ]
    skip = ("whisper", "tts", "guard", "embed", "vision", "ocr", "compound", "prompt", "safety")
    prefer = ("gpt-oss-120b", "versatile", "120b", "70b", "kimi", "maverick", "qwen", "scout", "instant")
    try:
        ids = [m.get("id", "") for m in _get_json("https://api.groq.com/openai/v1/models",
                                                  {"Authorization": f"Bearer {GROQ_KEY}"}).get("data", [])]
        live = [i for i in ids if i and not any(w in i.lower() for w in skip)]
        live.sort(key=lambda i: min([prefer.index(w) for w in prefer if w in i.lower()] or [99]))
        if live:
            return live
    except Exception as e:
        print(f"::warning title=Groq-Modelle::{type(e).__name__}: {e} – Modellname wird ausprobiert")
    return list(GROQ_CANDIDATES)


def groq_chat(prompt):
    """Chat-Anfrage; nicht (mehr) verfügbare Modellnamen werden übersprungen."""
    global MODEL_GROQ
    last = None
    for m in groq_models():
        try:
            data = _post("https://api.groq.com/openai/v1/chat/completions",
                         {"model": m, "temperature": 0.1, "response_format": {"type": "json_object"},
                          "messages": [{"role": "user", "content": prompt}]},
                         {"Authorization": f"Bearer {GROQ_KEY}", "User-Agent": "comunio-dashboard"})
        except RuntimeError as e:
            last = e
            if "does not exist" in str(e) or "decommissioned" in str(e):
                continue  # nächster Modellname
            raise
        if not MODEL_GROQ:
            MODEL_GROQ = m
            print(f"Groq-Modell: {m}")
        return data["choices"][0]["message"]["content"]
    raise last or RuntimeError("kein nutzbares Groq-Modell gefunden")


def search(query):
    """Aktuelle Meldungen: Tavily (wenn Schlüssel) sonst Google-News-RSS. Gibt Textblock für den Prompt zurück."""
    items = []
    try:
        if TAVILY_KEY:
            d = _post("https://api.tavily.com/search", {"query": query, "topic": "news", "days": 45, "max_results": 6},
                      {"Authorization": f"Bearer {TAVILY_KEY}"})
            items = [(r.get("title"), r.get("url"), (r.get("content") or "")[:350], (r.get("published_date") or "")) for r in d.get("results", [])]
        else:
            u = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query + " when:45d", "hl": "de", "gl": "DE", "ceid": "DE:de"})
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (comunio-dashboard)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                root = ET.fromstring(r.read())
            for it in root.iter("item"):
                items.append((it.findtext("title"), it.findtext("link"), "", it.findtext("pubDate") or ""))
                if len(items) >= 8:
                    break
    except Exception as e:
        print(f"::warning title=Suche::{type(e).__name__}: {e}")
    items = [x for x in items if x[0]]
    return "\n".join(f"- [{d}] {t} | {c} | {u}" for t, u, c, d in items), {x[1] for x in items if x[1]}


def call(prompt):
    prompt = prompt + "\n\n" + OUT
    if GROQ_KEY:  # kostenloser Tarif, Suchtreffer stehen im Prompt
        text = groq_chat(prompt)
        time.sleep(6)  # Gratis-Tarif: Anfragen und Tokens pro Minute begrenzt
    elif GEMINI_KEY:  # kostenloser Tarif, Websuche über Google Search Grounding
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_GEMINI}:generateContent"
        data = _post(url, {"contents": [{"parts": [{"text": prompt}]}], "tools": [{"google_search": {}}]},
                     {"x-goog-api-key": GEMINI_KEY})
        cand = (data.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
        time.sleep(8)  # Gratis-Tarif: Anfragen pro Minute begrenzt
    else:
        body = {"model": MODEL, "max_tokens": 6000,
                "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 12}],
                "messages": [{"role": "user", "content": prompt}]}
        data = _post("https://api.anthropic.com/v1/messages", body,
                     {"x-api-key": KEY, "anthropic-version": "2023-06-01"})
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("keine JSON-Antwort")
    return json.loads(m.group(0))


def clamp(x, default=None):
    try:
        return round(min(1.0, max(0.0, float(x))), 2)
    except (TypeError, ValueError):
        return default


def load_squad():
    """Aktueller Kader des Nutzers aus data/raw.json (von fetch_comunio.py): {id: {name, club, status}}."""
    p = Path(os.environ.get("DATA_DIR", ROOT / "data")) / "raw.json"
    if not p.exists():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))
    me = str((raw.get("me") or {}).get("id", ""))
    sq = (raw.get("squads") or {}).get(me) or []
    def row(x):
        return {"name": x.get("name"), "club": x.get("club"), "status": x.get("status"), "statusInfo": x.get("statusInfo")}
    squad = {str(x["id"]): row(x) for x in sq if isinstance(x, dict) and x.get("id")}
    market = {str(x["id"]): row(x) for x in (raw.get("market") or []) if isinstance(x, dict) and x.get("id")}
    return {"squad": squad, "market": market} if squad else None


DEFAULT = {"rumors": {"target": "", "probability": 0.0, "pWinter": 0.0, "pSummer": 0.0, "note": "Noch nicht recherchiert.",
                      "source": "", "url": "", "status": "Keine Angabe"},
           "injuries": {"return": "", "note": "", "source": "", "url": ""}}


def run(name, task, fields, squad):
    path = ROOT / "research" / f"{name}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    old = doc.get("players", {})
    if squad:
        # Gerüchte: alle Spieler des aktuellen Kaders. Ausfälle: Kaderspieler, die schon gelistet oder bei Comunio nicht ACTIVE sind.
        own, mkt = squad["squad"], squad["market"]
        if name == "rumors":
            keep = list(own)
        else:  # Ausfälle: bereits gelistete (auch Transfermarkt-Spieler) + Kader/Markt-Spieler, die bei Comunio nicht aktiv sind
            keep = list(old) + [pid for pid, v in {**mkt, **own}.items() if pid not in old and (v.get("status") or "ACTIVE") != "ACTIVE"]
        players = {}
        for pid in keep:
            v = own.get(pid) or mkt.get(pid) or {}
            base = old.get(pid) or {"name": v.get("name"), **DEFAULT[name]}
            if name == "injuries" and pid not in old and v.get("statusInfo"):
                base["note"] = str(v["statusInfo"])
            players[pid] = base
        print(f"{name}: {len(players)} Spieler ({len(old.keys() - players.keys())} entfernt)")
    else:
        players = old
        print(f"::warning title=Recherche::data/raw.json fehlt – nur bereits gelistete Spieler")
    doc["players"] = players
    ids = list(players)
    changed = 0
    ok = 0
    batch = 1 if GROQ_KEY else BATCH
    for i in range(0, len(ids), batch):
        part = ids[i:i + batch]
        listing = "\n".join(f"{pid}: {players[pid].get('name')} – {json.dumps({k: players[pid].get(k) for k in fields}, ensure_ascii=False)}"
                            for pid in part)
        extra = ""
        if GROQ_KEY:
            q = f"{players[part[0]].get('name')} Bundesliga " + ("Transfer Wechsel Gerücht" if name == "rumors" else "Verletzung Rückkehr Ausfall")
            hits, urls = search(q)
            if not hits:
                continue  # ohne Suchtreffer keine Aussage
            extra = ("\n\nSuchtreffer (Datum | Titel | Auszug | URL). Nutze ausschließlich diese Treffer; url nur aus dieser Liste; "
                     "Meldungen älter als 60 Tage zählen nicht als aktuelles Gerücht. Ohne relevante Treffer gib {} zurück:\n" + hits)
        try:
            res = call(task.format(today=TODAY, players=listing) + extra)
        except Exception as e:  # Netz, Limit, Format
            print(f"::warning title=Recherche {name}::{type(e).__name__}: {e} – bei {','.join(part)} – alte Werte bleiben")
            if "HTTP 429" in str(e) or "HTTP 401" in str(e) or "HTTP 403" in str(e):  # Kontingent leer: weitere Versuche sind zwecklos
                print("::error title=Recherche::API-Schlüssel ungültig oder Kontingent erschöpft – Abbruch (Secret prüfen)")
                break
            continue
        ok += 1
        for pid in part:
            new = res.get(pid)
            if not isinstance(new, dict) or not new:
                continue
            if GROQ_KEY and new.get("url") not in urls:
                continue  # Belegpflicht: ohne URL aus den Suchtreffern wird nichts übernommen
            if name == "injuries" and new.get("fit") and not GROQ_KEY:
                del players[pid]
                changed += 1
                continue
            upd = {k: new[k] for k in fields if new.get(k) not in (None, "")}
            for k in ("probability", "pWinter", "pSummer"):
                if k in upd:
                    upd[k] = clamp(upd[k], players[pid].get(k))
            if upd.get("url") and not str(upd["url"]).startswith("http"):
                del upd["url"]
            if upd and any(players[pid].get(k) != v for k, v in upd.items()):
                players[pid].update(upd)
                changed += 1
    if ok:
        doc["asOf"] = TODAY
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{name}: {changed} Einträge geändert")


def main():
    if not (KEY or GEMINI_KEY or GROQ_KEY):
        print("::warning title=Recherche::GROQ_API_KEY, GEMINI_API_KEY oder ANTHROPIC_API_KEY fehlt – nichts aktualisiert")
        return 0
    squad = load_squad()
    run("rumors", RUMOR_TASK, RUMOR_FIELDS, squad)
    run("injuries", INJURY_TASK, INJURY_FIELDS, squad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
