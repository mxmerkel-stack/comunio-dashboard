#!/usr/bin/env python3
"""Meldet einen Workflow-Fehler per Push (ntfy) und Mail. Nutzung: alert.py "Titel" "Text"

Secrets (alle optional): NTFY_TOPIC, MAIL_USERNAME, MAIL_APP_PASSWORD, MAIL_TO.
Es werden nur Workflow-Name und Link zum Lauf gesendet, keine Daten.
"""
import json
import os
import smtplib
import sys
import unicodedata
import urllib.error
import urllib.request
from email.mime.text import MIMEText

SITE = "https://mxmerkel-stack.github.io/comunio-dashboard/"


def topic_name():
    """Themenname aus dem Secret; erlaubt auch versehentlich eingefügte Adressen wie https://ntfy.sh/<thema>."""
    t = (os.environ.get("NTFY_TOPIC") or "").strip().strip("/")
    for pre in ("https://", "http://"):
        if t.lower().startswith(pre):
            t = t[len(pre):]
    if t.lower().startswith("ntfy.sh/"):
        t = t[len("ntfy.sh/"):]
    bad = [(i, f"U+{ord(c):04X} {unicodedata.name(c, '?')}") for i, c in enumerate(t) if not (c.isascii() and (c.isalnum() or c in "-_"))]
    if bad:  # nur Position und Art der Zeichen melden, nicht den Namen selbst
        print(f"::notice title=ntfy::Ungültige Zeichen im Themennamen bereinigt: {bad}")
    t = "".join("-" if unicodedata.category(c) == "Pd" else c for c in t)
    return "".join(c for c in t if c.isascii() and (c.isalnum() or c in "-_"))


def ntfy(title, message, priority=3, click=SITE, tags=None):
    topic = topic_name()
    if not topic:
        return False
    body = json.dumps({"topic": topic, "title": title, "message": message, "priority": priority,
                       "click": click, "tags": tags or []}).encode()
    req = urllib.request.Request("https://ntfy.sh/", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "comunio-zentrale/1.0"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        print("::notice title=ntfy::Nachricht angenommen (HTTP 200)")
        return True
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode()).get("error", "")
        except Exception:
            err = ""
        ok_chars = all(c.isalnum() or c in "-_" for c in topic)
        print(f"::warning title=ntfy::HTTP {e.code} {err} (Themenname: {len(topic)} Zeichen, nur erlaubte Zeichen: {'ja' if ok_chars else 'nein'})")
        return False
    except Exception as e:
        print(f"::warning title=ntfy::{type(e).__name__}")
        return False


def mail(subject, text):
    user, pw = os.environ.get("MAIL_USERNAME"), os.environ.get("MAIL_APP_PASSWORD")
    if not user or not pw:
        return False
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, user, os.environ.get("MAIL_TO") or user
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(user, pw)
            s.sendmail(user, [msg["To"]], msg.as_string())
        return True
    except Exception as e:
        print(f"::warning title=Mail::{type(e).__name__}")
        return False


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else "Comunio Zentrale: Fehler"
    text = sys.argv[2] if len(sys.argv) > 2 else "Ein Workflow ist fehlgeschlagen."
    repo, run = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_RUN_ID", "")
    link = f"https://github.com/{repo}/actions/runs/{run}" if repo and run else SITE
    a = ntfy(title, text, priority=4, click=link, tags=["warning"])
    b = mail(title, f"{text}\n\n{link}")
    print(f"::notice title=Fehlermeldung::Push={'ja' if a else 'nein'}, Mail={'ja' if b else 'nein (MAIL_USERNAME/MAIL_APP_PASSWORD fehlen oder Fehler)'}")


if __name__ == "__main__":
    main()
