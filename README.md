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
| `/status?token=…` | Pro Account: letzter Erfolg, letzter Fehler, Terminanzahl, Dateialter. Braucht `status_token_env`. |
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

## Entfallene Stunden

Google Calendar **blendet Termine mit `STATUS:CANCELLED` in abonnierten Feeds
aus**. Wer entfallene Stunden weiterhin sehen will, darf diesen Status also
nicht setzen. Steuerung über `app.cancelled_style`:

| Wert | Verhalten |
|------|-----------|
| `mark` (Standard) | Termin bleibt sichtbar, Titel beginnt mit `❌ Entfällt:`, `TRANSP:TRANSPARENT` – die Zeit gilt nicht mehr als belegt |
| `status` | Setzt `STATUS:CANCELLED` (RFC-konform, aber in Google meist unsichtbar) |
| `hide` | Entfallene Stunden kommen gar nicht erst in den Kalender |

Eine echte Durchstreich-Darstellung wie in Teams kennt Google für abonnierte
Kalender nicht – `mark` kommt dem am nächsten: der Termin steht weiter an
seinem Platz, ist als Entfall erkennbar und blockiert die Zeit nicht mehr.

Wer entfallene Stunden generell nicht will, kann sie auch pro Account über
`include_cancelled: false` abschalten.

### Verlegungen und Vertretungen

Verschiebt die Schule eine Stunde, entstehen in WebUntis **zwei** Einträge:
der alte Termin gilt als entfallen, am neuen steht die Stunde. Beide werden
gegenseitig verlinkt, sodass an jedem Termin steht, wohin er zeigt:

```
Di 22.09. 17:00   ❌ Verlegt · Anwendungsentwicklung … → Di 15.09. 18:40
Di 15.09. 18:40   ➡️ Anwendungsentwicklung … · K004
                     Verlegt – ursprünglich Di 22.09. 17:00.
```

Der Zustand steht bewusst **am Anfang** des Titels: Google kürzt Titel in der
Monats- und Wochenansicht, und so bleibt `❌ Verlegt …` auch dann lesbar.
Bei entfallenen Stunden wird der Raum weggelassen – er ist dann belanglos.

Ändert sich nur Lehrkraft oder Raum, steht das als `⚠️` im Titel und im
Detail, was getauscht wurde (`Lehrer: MY statt VS`).

Die Zuordnung braucht die REST-Ansicht (`fetch_online_info`). Die alte
JSON-RPC-Schnittstelle meldet nur „irgendetwas weicht ab", ohne zu sagen was.

## Online-Unterricht

Stunden, die in WebUntis als Online-Unterricht markiert sind, bekommen ein
`💻` im Titel, die Kategorie `Online` und – falls hinterlegt – den
Meeting-Link:

- in der `URL`-Property des Termins,
- in der Beschreibung (dort von Google anklickbar),
- als `LOCATION`, wenn kein Raum vergeben ist.

Diese Information liefert die alte JSON-RPC-Schnittstelle **nicht**. Sie wird
über die REST-Ansicht nachgeladen (`app.fetch_online_info: true`) und per
Stunden-ID zugeordnet. Schlägt das fehl, läuft der Sync ohne diese Extras
weiter – die Stundenplandaten selbst kommen unverändert aus JSON-RPC.

Zwei Einschränkungen aus der Praxis:
- Viele Schulen setzen zwar das Online-Flag, hinterlegen aber keine URL
  (WebUntis liefert dann den Platzhalter `"0"`). Der Termin wird dann als
  Online gekennzeichnet, mit dem Hinweis, dass kein Link hinterlegt ist.
- Häufiger steht der Link einfach im Stundentext. Der wird ebenfalls
  durchsucht, und ein gefundener Link zählt als Online-Unterricht.

## Aktualisierung

Im Serverbetrieb aktualisiert ein Hintergrund-Task die Feeds selbst. Das
Intervall richtet sich nach der Tageszeit – ein Stundenplan ändert sich
nachts nicht:

```yaml
refresh_interval_minutes: 15   # innerhalb der aktiven Stunden
refresh_idle_minutes: 120      # ausserhalb (0 = immer gleiches Intervall)
active_hours_start: 6
active_hours_end: 22
```

Anfragen an den Feed werden dabei **immer aus der Datei** beantwortet und
lösen keinen WebUntis-Abruf aus. Das hält die Antwortzeiten kurz und
verhindert, dass jeder Client-Abruf Last erzeugt. Live geholt wird nur, wenn
noch keine Datei existiert oder der Hintergrund-Task offensichtlich hängt
(Datei älter als das Dreifache des Intervalls). Parallele Anfragen auf
denselben Account werden über ein Lock zusammengefasst.

Wichtig zur Erwartung: **wie oft Google den Feed abholt, bestimmt Google.**
Typisch sind einige Stunden, und `REFRESH-INTERVAL` im Feed wird ignoriert.
Ein kürzeres Intervall hier macht den Feed frischer, beschleunigt aber nicht
Googles Abruf. Wer eine Änderung sofort sehen will, ruft die Feed-URL direkt
auf oder abonniert sie in einem Client, der selbst häufiger pollt.

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

### Absicherung des Dienstes

Der Dienst liefert personenbezogene Daten aus und steht oft öffentlich im
Netz. Folgendes ist deshalb voreingestellt:

| Einstellung | Wirkung |
|-------------|---------|
| `docs_enabled: false` | `/docs`, `/redoc` und `/openapi.json` werden nicht ausgeliefert. Sie beschreiben sonst die Angriffsfläche und laden Swagger-JS aus einem fremden CDN. |
| `status_token_env` | `/status` verrät Account-Keys, Schulen und Fehlertexte und ist deshalb tokenpflichtig. Ohne konfigurierten Token antwortet er mit 404 – fail-closed, damit er nicht versehentlich offen steht. |
| `security_headers: true` | `X-Content-Type-Options`, `X-Frame-Options`, `Content-Security-Policy` und `Referrer-Policy: no-referrer`. Letzteres verhindert, dass der Token über den Referer abfließt, wenn jemand die Feed-URL im Browser öffnet. |
| `redact_tokens_in_logs: true` | Der Token steht zwangsläufig im Query-String – Google kann ihn nicht anders übergeben. Ohne diesen Filter landet jeder Token im Journal und in allem, was Logs weiterreicht. |

Zusätzlich:
- Unbekannter Account und falscher Token liefern **dieselbe** Antwort
  (`404`). Unterschiedliche Fehler würden verraten, welche Account-Keys es
  gibt.
- Token werden zeitkonstant verglichen (`secrets.compare_digest`).
- Feeds werden mit `Cache-Control: private` ausgeliefert, damit geteilte
  Caches und Proxys die Stundenpläne nicht vorhalten.
- `/health` bleibt offen, verrät aber nur Status und Uhrzeit.

Was der Dienst **nicht** mitbringt: Rate-Limiting. Wer den Feed öffentlich
erreichbar macht, sollte das im Reverse Proxy ergänzen.

## Fehlersuche

| Symptom | Ursache |
|---------|---------|
| `404` auf `/WebUntis/jsonrpc.do` | Schule ist auf einen anderen Server umgezogen oder `school` stimmt nicht. `cli.py check` zeigt den richtigen Server. |
| `bad credentials` | Passwort/Benutzer abgelaufen. Login zuerst im WebUntis-Web testen. |
| Kalender in Google leer | `/status` prüfen. Liefert der Feed Daten, hat Google nur noch nicht neu abgerufen. |
| Feed liefert `404` | Falscher Token oder unbekannter Account-Key – beide antworten bewusst gleich. |

Der Dienst überschreibt eine vorhandene ICS-Datei bewusst **nicht** mit einem
leeren Kalender, wenn ein Abruf fehlschlägt – ein kurzer Untis-Ausfall leert
also nicht den Kalender.

## Struktur

```
untis_calendar/
  config.py         # YAML + ENV laden, Validierung
  school_lookup.py  # Schulname -> aktueller WebUntis-Server
  untis_direct.py   # JSON-RPC-Client (authenticate, getTimetable)
  untis_rest.py     # REST-Anreicherung: Online-Unterricht, Stundentexte
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
