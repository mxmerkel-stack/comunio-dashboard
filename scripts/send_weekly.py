#!/usr/bin/env python3
"""Wochenmail: Platz, Punkte, Prognose im Vergleich zur Vorwoche, Transfers, Konto, nächste Schritte.

Wird nach build_data.py ausgeführt und sendet nur montags (Berlin) oder bei FORCE_WEEKLY=true.
Gleiche Secrets wie send_mail.py; DRY_RUN=<datei> schreibt nur die Vorschau. Keine Inhalte im Log.
"""
import html
import json
import os
import smtplib
import sys
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
TZ = ZoneInfo("Europe/Berlin")
SITE = "https://mxmerkel-stack.github.io/comunio-dashboard/"


def f1(v):
    return f"{(v or 0):.1f}".replace(".", ",")


def eur(v):
    return "–" if v is None else f"{v:,.0f} €".replace(",", ".")


def sgn(v, fmt=lambda x: f"{x:.0f}"):
    return ("+" if v > 0 else "−" if v < 0 else "±") + fmt(abs(v))


def pct(v):
    return f"{round((v or 0) * 100)} %"


def week_ago(history, today):
    """Letzter Verlaufseintrag, der mindestens 6 Tage zurückliegt."""
    cut = (today - timedelta(days=6)).strftime("%Y-%m-%d")
    older = [h for h in history if h.get("date") and h["date"] <= cut]
    return older[-1] if older else None


def build(d, now):
    me = d["me"]["id"]
    stand = {s["id"]: s for s in d.get("standings", [])}
    mine = stand.get(me, {})
    hist = d.get("history") or []
    old = week_ago(hist, now)
    mds = d.get("matchdays") or []
    last_md = mds[-1] if mds else None
    fc = {r["id"]: r for r in d.get("forecast") or []}
    my_fc = fc.get(me, {})
    old_fc = (old or {}).get("forecast", {}).get(me)
    since = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    tr = [t for t in d.get("transfers") or [] if (t.get("date") or "")[:10] >= since]
    names = {m["id"]: m["name"] for m in d.get("managers", [])}
    me_name = names.get(me, "")
    chk = d.get("checks") or {}
    nm = d.get("nextMatch") or {}

    lines = []  # (Überschrift, [Zeilen])
    # 1 Tabelle
    rows = []
    rank_txt = f"Platz {mine.get('rank')} mit {mine.get('points')} Punkten"
    if old and (old.get("points") or {}).get(me) is not None:
        rows.append(f"{rank_txt} ({sgn(mine.get('points', 0) - old['points'][me])} Punkte seit letzter Woche)")
    else:
        rows.append(rank_txt)
    rows.append("Tabellenführer" if not mine.get("gap") else f"{mine['gap']} Punkte hinter Platz 1")
    if last_md:
        order = sorted(last_md["points"].items(), key=lambda kv: -(kv[1] or 0))
        mp = last_md["points"].get(me)
        if mp is not None:
            rk = 1 + sum(1 for _, v in order if (v or 0) > mp)
            rows.append(f"{last_md['key']}. Spieltag: {mp} Punkte, Platz {rk} von {len(order)} (Bester: {names.get(order[0][0], '?')} mit {order[0][1]})")
    lines.append(("Tabelle", rows))
    # 2 Prognose
    if my_fc:
        r = [f"Erwartet am Ende: {my_fc['exp_final']} Punkte (Spanne {my_fc['p10']}–{my_fc['p90']}), Ø Platz {f1(my_fc['exp_rank'])}",
             f"Meister: {pct(my_fc['p_title'])}, Top 3: {pct(my_fc['p_top3'])}"]
        if old_fc:
            r.append(f"Seit letzter Woche: {sgn(my_fc['exp_final'] - old_fc[0])} erwartete Endpunkte, Meister {sgn((my_fc['p_title'] - old_fc[3]) * 100, lambda x: f'{x:.1f}'.replace('.', ','))} %-Punkte")
        top = sorted(d.get("forecast") or [], key=lambda x: -x["p_title"])[:3]
        r.append("Meisterchancen: " + ", ".join(f"{x['name']} {pct(x['p_title'])}" for x in top))
        lines.append(("Prognose", r))
    # 3 Transfers der Woche
    if tr:
        r = [f"{t['player']}: {t['from']} → {t['to']} für {eur(t.get('price'))}" for t in tr[:12]]
        if len(tr) > 12:
            r.append(f"… und {len(tr) - 12} weitere")
        lines.append((f"Transfers der Woche ({len(tr)})", r))
    # 4 Konto
    bal = d["me"].get("balance")
    r = [f"Kontostand {eur(bal)} – " + ("im Minus, Punkte gefährdet!" if (bal or 0) < 0 else "positiv"), f"Teamwert {eur(d['me'].get('teamValue'))}"]
    if old and (old.get("teamValue") or {}).get(me):
        r.append(f"Teamwert seit letzter Woche {sgn(d['me']['teamValue'] - old['teamValue'][me], lambda x: eur(x))}")
    lines.append(("Konto", r))
    # 5 Nächster Spieltag
    r = []
    if chk.get("lineupKnown"):
        e = chk.get("emptySlots") or 0
        r.append(f"Unbesetzte Position: {'Ja (' + str(e) + ')' if e else 'Nein'}")
        r.append(f"Ausfälle in der Aufstellung: {', '.join(chk.get('outInLineup') or []) or 'keine'}")
    best = chk.get("best") or {}
    if best and not best.get("optimal"):
        r.append(f"Bessere Aufstellung möglich: +{f1(best.get('gain'))} Punkte")
    if (d.get("status") or {}).get("nextMd"):
        r.insert(0, f"{d['status']['nextMd']}. Spieltag, Aufstellungsschluss {(d['status'].get('nextDate') or '')[:16].replace('T', ' ')}")
    if r:
        lines.append(("Nächster Spieltag", r))
    # 6 Tipps
    r = []
    plans = d.get("plans") or []
    if plans:
        p = plans[0]
        r.append(f"Bester Transfer-Plan {p['id']}: " + (f"{', '.join(p['sell'])} verkaufen, " if p["sell"] else "") + f"{', '.join(p['buy'])} kaufen (+{f1(p['delta_md'])} Punkte je Spieltag)")
    wi = [x for x in (d.get("whatif") or {}).get("rows", []) if x["d_final"] > 1.5]
    if wi:
        b = max(wi, key=lambda x: x["d_final"])
        r.append(f"Größte Wirkung laut Simulation: {b['kind']} ({b['label']}), {sgn(b['d_final'])} Endpunkte")
    ending = [m for m in d.get("market", []) if m.get("recommendation") == "ja" and m.get("affordable") and not m.get("mine") and (m.get("remainingDays") or 99) <= 2]
    for m in ending[:3]:
        r.append(f"Läuft bald ab: {m['name']} ({m.get('club') or '–'}), Mindestgebot {eur(m.get('minBid'))}")
    if r:
        lines.append(("Tipps", r))

    subject = f"Comunio Wochenrückblick: Platz {mine.get('rank')}, Meister {pct(my_fc.get('p_title'))}" if my_fc else "Comunio Wochenrückblick"
    body = "<div style='font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:640px;color:#1d1c1a'>"
    body += f"<h2 style='margin:0 0 4px'>Comunio – Wochenrückblick</h2><p style='color:#666;margin:0 0 8px'>{html.escape(me_name)} · {now:%d.%m.%Y}</p>"
    text = [subject]
    for h, rows_ in lines:
        body += f"<h3 style='margin:16px 0 4px;font-size:15px'>{html.escape(h)}</h3><ul style='margin:0;padding-left:18px'>" + "".join(f"<li style='margin:2px 0'>{html.escape(x)}</li>" for x in rows_) + "</ul>"
        text += ["", h] + ["• " + x for x in rows_]
    body += f"<p style='margin-top:18px'><a href='{SITE}'>Zum Dashboard</a></p><p style='color:#888;font-size:12px'>Automatisch erstellt aus deinen Comunio-Daten. Es wurde nichts verändert.</p></div>"
    text += ["", SITE]
    return subject, body, "\n".join(text)


def main():
    now = datetime.now(TZ)
    force = os.environ.get("FORCE_WEEKLY", "").lower() == "true"
    dry = os.environ.get("DRY_RUN")
    if not force and not dry and now.weekday() != 0:
        print("Keine Wochenmail: nicht Montag.")
        return
    d = json.loads((DATA / "dashboard.json").read_text())
    subject, body, text = build(d, now)
    if dry:
        Path(dry).write_text(f"<!-- {subject} -->\n{body}")
        print("Vorschau geschrieben.")
        return
    user, pw = os.environ.get("MAIL_USERNAME"), os.environ.get("MAIL_APP_PASSWORD")
    if not user or not pw:
        print("::warning title=Wochenmail::MAIL_USERNAME / MAIL_APP_PASSWORD fehlen – keine Mail gesendet.")
        return
    to = os.environ.get("MAIL_TO") or user
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(body, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(user, pw)
            s.sendmail(user, [to], msg.as_string())
    except Exception as e:
        print(f"::error title=Wochenmail::Versand fehlgeschlagen ({type(e).__name__})")
        sys.exit(1)
    print("Wochenmail gesendet.")


if __name__ == "__main__":
    main()
