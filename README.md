# Comunio Zentrale

Privates Dashboard für eine Comunio-Community: Saisontabelle, Ewige Tabelle, Siege-Ranking,
Kontostände aller Manager, Transfers, Kader und Gebotsempfehlungen für den Transfermarkt.

**Dieses Repository enthält nur Code** – keine Daten und keine Zugangsdaten.

## So funktioniert es

1. GitHub Actions läuft automatisch: täglich morgens und am Wochenende abends, außerdem auf Knopfdruck.
2. `scripts/fetch_comunio.py` meldet sich mit den Secrets bei Comunio an und liest die Daten.
   Es werden nur Daten abgerufen, nichts wird verändert.
3. `scripts/build_data.py` berechnet Tabellen, Siege und Kontostände und schreibt den Verlauf fort.
   Der Verlauf wird verschlüsselt im Actions-Cache gespeichert.
4. `scripts/render_site.py` baut die Seite, und `scripts/encrypt_page.py` verschlüsselt sie
   (AES-256-GCM, Schlüssel per PBKDF2 aus dem Seitenpasswort).
5. Die verschlüsselte Seite wird auf GitHub Pages veröffentlicht. Ohne Passwort sieht man nur ein Eingabefeld.

## Einrichtung (einmalig)

**Settings → Secrets and variables → Actions → New repository secret:**

| Name | Inhalt |
|---|---|
| `COMUNIO_USER` | Comunio-Benutzername |
| `COMUNIO_PASS` | Comunio-Passwort |
| `PAGE_PASSWORD` | Neues Passwort für die Seite, mindestens 12 Zeichen |
| `MAIL_USERNAME` | optional: Gmail-Adresse für die Morgen-Mail am Spieltag |
| `MAIL_APP_PASSWORD` | optional: App-Passwort dieses Gmail-Kontos (Google-Konto → Sicherheit → App-Passwörter) |
| `MAIL_TO` | optional: Empfänger, falls nicht die Gmail-Adresse selbst |
| `ODDS_API_KEY` | optional: kostenloser Schlüssel von the-odds-api.com für die Tipico-Quoten im Reiter „Next Match“ |

**Settings → Pages → Build and deployment → Source:** „GitHub Actions“

Danach unter **Actions → Dashboard aktualisieren → Run workflow** den ersten Lauf starten.

## Anpassen

`config.json` enthält das Saisonstartdatum, das Startkapital je Manager, die Punkteprämie
und die Parameter der Gebotsempfehlung.

## Lokal testen (ohne Comunio)

```bash
pip install -r requirements.txt
python tests/make_sample.py && python scripts/build_data.py && python scripts/render_site.py
# dist/index.html im Browser öffnen
```
