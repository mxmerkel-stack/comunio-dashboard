#!/usr/bin/env python3
"""Prüft ohne Comunio-Zugang, ob der Aufstellungsschluss (erster Anpfiff des Spieltags) in den nächsten
MINUTES Minuten liegt. Schreibt run=true/false in $GITHUB_OUTPUT."""
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

WINDOW = int(os.environ.get("MINUTES", "150"))


def main():
    out = os.environ.get("GITHUB_OUTPUT")
    run = False
    try:
        req = urllib.request.Request("https://api.openligadb.de/getmatchdata/bl1", headers={"User-Agent": "comunio-dashboard"})
        data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
        ks = sorted(datetime.fromisoformat(m["matchDateTimeUTC"].replace("Z", "+00:00")) for m in data if m.get("matchDateTimeUTC"))
        now = datetime.now(timezone.utc)
        if ks:
            first = ks[0]
            run = timedelta(0) < first - now <= timedelta(minutes=WINDOW)
            print(f"Erster Anpfiff {first.isoformat()}, in {(first - now).total_seconds() / 60:.0f} Min. -> run={run}")
    except Exception as e:
        print(f"::warning title=Gate::{type(e).__name__}")
    if os.environ.get("FORCE") == "true":
        run = True
    if out:
        with open(out, "a") as f:
            f.write(f"run={'true' if run else 'false'}\n")


if __name__ == "__main__":
    main()
