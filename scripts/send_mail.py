#!/usr/bin/env python3
"""Morgen-Mail vor dem Aufstellungsschluss: Kontostand, Aufstellung, Warnungen, beste Elf.

Wird nach build_data.py ausgeführt. Versand per Gmail (SMTP mit App-Passwort):
  MAIL_USERNAME      Gmail-Adresse (Absender)
  MAIL_APP_PASSWORD  App-Passwort dieses Gmail-Kontos
  MAIL_TO            Empfänger (optional, Standard = MAIL_USERNAME)
  FORCE_MAIL=true    sofort senden (Test), unabhängig von Tag und Uhrzeit
  DRY_RUN=<datei>    nur HTML in die Datei schreiben, nichts senden
Es werden keine Inhalte ins (öffentliche) Log geschrieben.
"""
import html
import json
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
TZ = ZoneInfo("Europe/Berlin")
SITE = "https://mxmerkel-stack.github.io/comunio-dashboard/#nextmatch"


def f1(v):
    return f"{(v or 0):.1f}".replace(".", ",")


def eur(v):
    return "–" if v is None else f"{v:,.0f} €".replace(",", ".")


def when(iso):
    if not iso:
        return "–"
    d = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ)
    tage = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    return f"{tage[d.weekday()]} {d:%d.%m.}, {d:%H:%M} Uhr"


def should_send(chk, now):
    if os.environ.get("FORCE_MAIL", "").lower() == "true":
        return True, "manuell ausgelöst"
    dl = chk.get("deadline")
    if not dl or chk.get("started"):
        return False, "kein anstehender Aufstellungsschluss"
    d = datetime.fromisoformat(dl.replace("Z", "+00:00")).astimezone(TZ)
    if d.date() == now.date() and now.hour < 12 and now < d:
        return True, "Morgen des Spieltags"
    return False, "nicht der Morgen des Spieltags"


def build_mail(dash):
    chk = dash.get("checks") or {}
    nm = dash.get("nextMatch") or {}
    warn = [i for i in chk.get("issues", []) if i["level"] == "warn"]
    info = [i for i in chk.get("issues", []) if i["level"] == "info"]
    md = chk.get("md") or "?"
    subject = (f"⚠️ Comunio {md}. Spieltag: {len(warn)} Warnung{'en' if len(warn) != 1 else ''}" if warn
               else f"✅ Comunio {md}. Spieltag: alles in Ordnung")
    best = chk.get("best") or {}
    rows = []
    for r in nm.get("best") or []:
        rows.append(f"<tr><td>{html.escape(r['pos'])}</td><td>{html.escape(r['name'])}</td>"
                    f"<td>{html.escape(r['opponent']) + (' (H)' if r.get('venue') == 'Heim' else ' (A)') if r.get('opponent') else '–'}</td>"
                    f"<td style='text-align:right'>{f1(r['e'])}</td></tr>")

    def block(items, color):
        return "".join(f"<li style='margin:0 0 8px'><b style='color:{color}'>{html.escape(i['title'])}</b><br>"
                       f"<span style='color:#444'>{html.escape(i['detail'])}</span></li>" for i in items)

    bal = chk.get("balance")
    status_line = (f"<p style='font-size:15px'><b>Aufstellungsschluss:</b> {when(chk.get('deadline'))}<br>"
                   f"<b>Kontostand:</b> <span style='color:{'#b3261e' if (bal or 0) < 0 else '#0f6b4f'}'>{eur(bal)}</span><br>"
                   f"<b>Deine Aufstellung:</b> {html.escape(str(best.get('formationNow') or '–'))}, "
                   f"{f1(best.get('now'))} erwartete Punkte<br>"
                   f"<b>Beste mögliche Elf:</b> {html.escape(str(best.get('formation') or '–'))}, "
                   f"{f1(best.get('best'))} erwartete Punkte – "
                   + ("deine Aufstellung ist die beste für deinen Kader." if best.get("optimal")
                      else f"Verbesserung möglich: rein {html.escape(', '.join(best.get('in') or []) or '–')}, raus {html.escape(', '.join(best.get('out') or []) or '–')}.")
                   + "</p>")
    body = f"""<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:640px;color:#1d1c1a">
<h2 style="margin:0 0 4px">Comunio – {md}. Spieltag</h2>
{status_line}
{"<h3 style='color:#b3261e;margin:16px 0 6px'>Warnungen (kosten Punkte)</h3><ul style='padding-left:18px'>" + block(warn, '#b3261e') + "</ul>" if warn else "<p style='color:#0f6b4f'><b>Keine Warnungen:</b> Konto nicht negativ, alle Positionen besetzt, niemand auf falscher Position oder sicher ausgefallen.</p>"}
{"<h3 style='margin:16px 0 6px'>Hinweise</h3><ul style='padding-left:18px'>" + block(info, '#a5590b') + "</ul>" if info else ""}
<h3 style="margin:16px 0 6px">Empfohlene Aufstellung</h3>
<table cellpadding="4" style="border-collapse:collapse;font-size:14px">{''.join(rows)}</table>
<p style="margin-top:18px"><a href="{SITE}">Zum Dashboard (Next Match)</a></p>
<p style="color:#888;font-size:12px">Automatisch erstellt um {datetime.now(TZ):%H:%M} Uhr aus deinen Comunio-Daten. Es wurde nichts verändert.
Regeln: negativer Kontostand zum Aufstellungsschluss = keine Punkte; jede unbesetzte Position −4 Punkte; Spieler auf fremder Position bekommen keine Punkte.</p>
</div>"""
    text = "\n".join([subject, f"Aufstellungsschluss: {when(chk.get('deadline'))}", f"Kontostand: {eur(bal)}"]
                     + [f"WARNUNG: {i['title']} – {i['detail']}" for i in warn]
                     + [f"Hinweis: {i['title']} – {i['detail']}" for i in info] + [SITE])
    return subject, body, text


def main():
    dash = json.loads((DATA / "dashboard.json").read_text())
    now = datetime.now(TZ)
    ok, why = should_send(dash.get("checks") or {}, now)
    dry = os.environ.get("DRY_RUN")
    if not ok and not dry:
        print(f"Keine Mail: {why}.")
        return
    subject, body, text = build_mail(dash)
    if dry:
        Path(dry).write_text(f"<!-- {subject} -->\n{body}")
        print(f"Vorschau geschrieben ({why}).")
        return
    user, pw = os.environ.get("MAIL_USERNAME"), os.environ.get("MAIL_APP_PASSWORD")
    if not user or not pw:
        print("::warning title=E-Mail::MAIL_USERNAME / MAIL_APP_PASSWORD fehlen – keine Mail gesendet.")
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
        print(f"::error title=E-Mail::Versand fehlgeschlagen ({type(e).__name__})")
        sys.exit(1)
    print(f"Mail gesendet ({why}).")


if __name__ == "__main__":
    main()
