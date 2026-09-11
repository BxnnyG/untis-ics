# Untis → ICS Tool

Zweck: Stundenpläne aus WebUntis abrufen und als ICS-Feed bereitstellen
(z. B. zum Abonnieren in Google Calendar oder Apple Kalender).

Betriebsmodi:
- **Server** (`serve`): FastAPI liefert pro Account einen abonnierbaren ICS-Feed
  und aktualisiert im Hintergrund selbstständig. ← auf diesem Host aktiv
- **CLI** (`generate`): ICS-Dateien einmalig erzeugen (für Cron)
- **Check** (`check`): Diagnose – Schulsuche, Login und Abruf pro Account testen

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

## Konfiguration

`config.yaml` aus `config.example.yaml` ableiten. Wichtigste Punkte:

- `school` ist der WebUntis-**loginName** der Schule, nicht der Klartextname
  (z. B. `musterschule`, nicht `Muster-Berufskolleg`).
- `server` kann leer bleiben – dann wird der zuständige Server automatisch
  über die offizielle WebUntis-Schulsuche ermittelt.
- Jeder Account braucht einen **eigenen** `token`. Ein geteilter Token heißt:
  wer einen Feed kennt, kann alle lesen.

## Betrieb

```bash
# Accounts testen (zeigt Server, Login, erste Termine)
python cli.py check --config config.yaml

# nur einen Account testen
python cli.py check --config config.yaml --only schueler1

# ICS-Dateien erzeugen
python cli.py generate --config config.yaml

# Server starten
python cli.py serve --config config.yaml --host 0.0.0.0 --port 8080
```

Auf diesem Host läuft der Server als systemd-Unit:

```bash
systemctl status untis-calendar-sync
systemctl restart untis-calendar-sync
journalctl -u untis-calendar-sync -f
```

### Endpunkte

| Pfad | Zweck |
|------|-------|
| `/health` | Lebt der Dienst? |
| `/status` | Pro Account: letzter Erfolg, letzter Fehler, Terminanzahl, Dateialter |
| `/calendar/<key>.ics?token=<token>` | Der eigentliche Feed |

`/status` ist die erste Anlaufstelle, wenn ein Kalender leer wirkt.

## Google Calendar

Ein ICS-Feed = **ein eigener Kalender** in Google. Für drei Accounts also
dreimal: *Andere Kalender → + → Per URL → `https://.../calendar/<key>.ics?token=...`*

Zu beachten:
- Immer die **https**-URL eintragen.
- Google bestimmt das Abrufintervall selbst (typisch einige Stunden) und
  ignoriert `REFRESH-INTERVAL` im Feed. Kurzfristige Vertretungen erscheinen
  deshalb verzögert. Wer das nicht will, muss statt eines Abos direkt über
  die Google-Calendar-API schreiben.
- Google zeigt anfangs die URL als Kalendername. Umbenennen geht in den
  Einstellungen des Kalenders.
- Ändert sich ein Token, muss das Abo in Google gelöscht und neu angelegt
  werden – eine URL lässt sich dort nicht bearbeiten.

## Sicherheit

**In dieses Repository gehören keine Zugangsdaten.** `config.yaml`, `.env` und
`out/` stehen in `.gitignore` – die echte Konfiguration enthält Benutzernamen
und Schulen, die generierten ICS-Dateien komplette Stundenpläne samt Lehrer-
und Klassennamen.

Passwörter und Feed-Tokens gehören nicht in die `config.yaml`, sondern in eine
root-only Datei, die systemd einliest:

```bash
sudo install -m 600 -o root -g root /dev/null /etc/untis-sync.env
sudo nano /etc/untis-sync.env        # Vorlage: .env.example
```

In der Unit:

```ini
[Service]
User=www-data
EnvironmentFile=/etc/untis-sync.env
```

Die `config.yaml` verweist dann nur noch auf die Variablennamen:

```yaml
password_env: "UNTIS_PASS_SCHUELER1"
token_env: "FEED_TOKEN_SCHUELER1"
```

Der Gewinn: systemd liest die Datei **als root** und reicht die Werte an den
unprivilegierten Dienst weiter. Der Dienstbenutzer kann die Datei selbst nicht
lesen. Läuft auf demselben Host noch ein Webserver unter dem gleichen Benutzer
(typisch `www-data`), kommt eine kompromittierte Web-Anwendung damit nicht an
die Untis-Passwörter – bei Klartext in der `config.yaml` schon.

Das ist keine Verschlüsselung: der Dienst muss das Passwort im Klartext kennen,
um sich bei WebUntis anzumelden. Es begrenzt nur, wer es von der Platte lesen
kann.

Weiteres:
- Jeder Feed braucht einen **eigenen** Token. Ein geteilter Token heißt: wer
  eine URL kennt, liest alle Stundenpläne.
- Feed-URLs sind nur durch den Token geschützt – wer sie hat, kommt rein.
  Entsprechend nicht in öffentliche Chats oder Issues kopieren.
- `verify_ssl: false` schaltet die Zertifikatsprüfung ab. Die öffentlichen
  WebUntis-Server haben gültige Zertifikate; die Option sollte auf `true`
  bleiben.

## Fehlersuche

| Symptom | Ursache |
|---------|---------|
| `404` auf `/WebUntis/jsonrpc.do` | Schule ist auf einen anderen Server umgezogen oder `school` stimmt nicht. `cli.py check` zeigt den richtigen Server. |
| `bad credentials` | Passwort/Benutzer abgelaufen. Login zuerst im WebUntis-Web testen. |
| Kalender in Google leer | `/status` prüfen. Liefert der Feed Daten, hat Google nur noch nicht neu abgerufen. |
| Feed liefert `401` | Falscher oder alter Token in der Abo-URL. |

Der Dienst überschreibt eine vorhandene ICS-Datei bewusst **nicht** mit einem
leeren Kalender, wenn ein Abruf fehlschlägt – ein kurzer Untis-Ausfall leert
also nicht den Kalender.

## Struktur

```
untis_calendar/
  config.py         # YAML + ENV laden, Validierung
  school_lookup.py  # Schulname -> aktueller WebUntis-Server
  untis_direct.py   # JSON-RPC-Client (authenticate, getTimetable)
  untis_client.py   # Abruf, Mapping, Filter, Doppelstunden-Zusammenfassung
  ics.py            # ICS-Erzeugung
  server.py         # FastAPI-Feeds + Hintergrund-Refresh
  models.py, utils.py, logging_config.py
cli.py              # Entry Point: generate | check | serve
tests/              # pytest
```

## Dokumentation
- **DEPLOYMENT.md** – Debian-Anleitung, Betrieb
- **ARCHITECTURE.md** – technische Details

## Lizenz
MIT
