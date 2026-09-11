# Architektur & Troubleshooting Guide

Technische Dokumentation für Entwickler und KI-Assistenten.

## Projektarchitektur

### Übersicht

```
untis_calendar/
├── __init__.py
├── config.py          # Pydantic-Config (YAML + ENV) + Validierung
├── school_lookup.py   # Schulname -> aktueller WebUntis-Server (Schulsuche)
├── untis_direct.py    # JSON-RPC Client (authenticate, getTimetable)
├── untis_client.py    # Hauptlogik: Daten abrufen, mappen, mergen, filtern
├── models.py          # LessonEvent Datenmodell
├── utils.py           # Helfer (UID-Generierung, Timezone)
├── ics.py             # ICS-Generierung (icalendar)
├── server.py          # FastAPI-Webserver + Hintergrund-Refresh
├── logging_config.py  # Logging-Setup

cli.py                 # CLI Entry Point (generate, check, serve)
config.yaml            # Haupt-Konfiguration
requirements.txt       # Python-Dependencies
```

### Datenfluss

```
CLI/Cron
  └─> Config laden (config.py)
      └─> UntisClient.fetch_events()
          └─> resolve_server() [school_lookup.py]  (falls server: leer)
          └─> direct_untis_login() [untis_direct.py]
              └─> JSON-RPC authenticate + getTimetable
          └─> _map_raw_to_event() [untis_client.py]
              └─> WebUntis-JSON → LessonEvent
          └─> _filter_event()
              └─> include/exclude Subjects
          └─> _merge_consecutive()
              └─> Doppelstunden zu einem Termin zusammenfassen
      └─> events_to_ics() [ics.py]
          └─> LessonEvent[] → ICS bytes
      └─> Datei schreiben (output_dir)
```

### Wichtige Designentscheidungen

#### 1. Direkte JSON-RPC statt webuntis-Bibliothek

**Grund**: Die `python-webuntis`-Bibliothek hat einen Bug bei Request-ID-Validierung, der bei manchen Servern zu "Request ID mismatch"-Fehlern führt.

**Lösung**: Eigene Implementation in `untis_direct.py`:
- Direkter `requests`-Aufruf
- Manuelles Session-Management
- Robustes Error-Handling

**Code-Referenz**: `untis_direct.DirectUntisSession`

#### 2. Ein-Datei-Konfiguration

**Grund**: Einfachheit für Endnutzer ("DAU-friendly").

**Design**:
- Passwörter/Tokens direkt in YAML (für Einfachheit)
- Optional: ENV-Fallback (`password_env`, `token_env`)
- Pydantic-Validierung

**Migration**: Falls du zurück zu ENV-only willst, ändere in `config.py`:
```python
class AccountConfig(BaseModel):
    password_env: str  # Required statt Optional
```

#### 3. Stabile Event-UIDs

**Grund**: Google Calendar & Co. erkennen Updates über UIDs.

**Strategie**:
```python
uid = sha256(school | account | source_id | start | end | room)
```

**Wichtig**: 
- UID darf sich nicht ändern, wenn Event gleich bleibt
- Bei Updates: gleiche UID + erhöhte SEQUENCE oder neuere DTSTAMP

**Code**: `utils.stable_uid()`

#### 4. Subject-Parsing (Bugfix)

**Problem**: WebUntis liefert `subject` in verschiedenen Formaten:
- String: `"MATHE"`
- Dict: `{"name": "MATHE", "longName": "Mathematik"}`
- Liste: `[{"name": "MATHE"}]`

**Lösung** in `untis_client._map_raw_to_event()`:
```python
if isinstance(subject, list) and subject:
    subject = subject[0].get("name", ...)
elif isinstance(subject, dict):
    subject = subject.get("name", ...)
subject = str(subject) if subject else "Unbekannt"
```

#### 5. SSL-Verifikation Optional

**Grund**: Manche Schulen nutzen selbst-signierte Zertifikate.

**Config**:
```yaml
accounts:
  - verify_ssl: false
```

**Implementation**: `urllib3.disable_warnings()` in `untis_direct.__init__`

---

## Häufige Probleme & Fixes

### 1. "invalid schoolname" (Code -8500)

**Symptom**:
```
RuntimeError: WebUntis API Error: {'message': 'invalid schoolname', 'code': -8500}
```

**Ursache**:
- Schulname ist case-sensitive
- Schulname muss exakt aus WebUntis-URL übernommen werden

**Fix**:
1. Browser: `https://SERVER/WebUntis/?school=SCHULNAME`
2. `SCHULNAME` exakt in `config.yaml` kopieren

**Debug**:
```python
# In untis_direct.py, Zeile ~40:
logger.debug("Request URL: %s", self.url)
```

### 2. "unhashable type: 'list'" beim Subject

**Symptom**:
```
TypeError: unhashable type: 'list'
  File "untis_client.py", line 114, in _map_raw_to_event
    color_key = account.color_map.get(subject)
```

**Ursache**: `subject` ist Liste, aber `dict.get()` erwartet hashable (String).

**Fix**: Bereits gefixt in aktuellem Code (siehe Architektur #4).

**Workaround** (falls alter Code):
```python
subject = str(subject) if not isinstance(subject, (list, dict)) else "Unbekannt"
color_key = account.color_map.get(subject, None)
```

### 3. SSL Certificate Verify Failed

**Symptom**:
```
SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain'))
```

**Fix**: In `config.yaml`:
```yaml
accounts:
  - verify_ssl: false
```

**Unterdrückung der Warnung**: `untis_direct.py` macht automatisch `urllib3.disable_warnings()`.

### 4. Request-ID Mismatch (webuntis-Bibliothek)

**Symptom** (alter Code):
```
Request ID was not the same one as returned. error
```

**Ursache**: Bug in `python-webuntis` bei manchen Servern.

**Fix**: `untis_direct.py` ist der einzige Client; der alte `auth.py`-Wrapper wurde entfernt.

### 5. Leere ICS (99 Bytes)

**Symptom**: ICS-Datei enthält nur Header, keine Events.

**Checks**:
```bash
# Log anschauen
grep "Empfangen:" /var/log/untis-calendar.log
# Sollte zeigen: "Empfangen: N Roheinträge"

# Manuell testen
python cli.py generate --config config.yaml
cat out/*.ics | grep "BEGIN:VEVENT" | wc -l
```

**Ursachen**:
- Login fehlgeschlagen (Credentials falsch)
- Falscher Schulname/Server
- Zeitfenster außerhalb der Unterrichtszeiten
- Filter zu restriktiv (`include_subjects`)

**Debug**: Logging auf DEBUG setzen:
```python
# In logging_config.py
logger.setLevel(logging.DEBUG)
```

### 6. Keine Permissions (Debian)

**Symptom**:
```
PermissionError: [Errno 13] Permission denied: '/var/www/untis-calendar/kalender.ics'
```

**Fix**:
```bash
sudo chown -R www-data:www-data /var/www/untis-calendar
sudo chmod 755 /var/www/untis-calendar
```

Oder Config anpassen:
```yaml
app:
  output_dir: "/home/user/untis-out"  # Statt /var/www
```

---

## Debugging-Tools

### 1. Debug-Logging aktivieren

In `cli.py` ändern:
```python
from untis_calendar.logging_config import setup_logging
setup_logging(level=logging.DEBUG)
```

### 2. Rohdaten inspizieren

In `untis_client.py`, Zeile ~38:
```python
logger.info("Empfangen: %d Roheinträge für %s", len(raw_list), account.key)
# Hinzufügen:
logger.debug("Rohdaten: %s", raw_list[:2])  # Erste 2 Events
```

### 3. Einzelnes Event testen

```python
from untis_calendar.untis_direct import direct_untis_login

with direct_untis_login("alt-server.webuntis.com", "musterschule", "user", "pass", False) as sess:
    events = sess.timetable(start=date(2025, 10, 22), end=date(2025, 10, 23))
    print(events)
```

### 4. ICS-Validierung

```bash
# icalendar-Validator (Python)
pip install icalendar
python -c "from icalendar import Calendar; c = Calendar.from_ical(open('out/kalender.ics', 'rb').read()); print(c.walk())"

# Online: https://icalendar.org/validator.html
```

---

## Erweiterungen

### 1. Mehrere Kalender pro Account

**Use Case**: Schüler will Fächer in separate Kalender trennen.

**Implementation**:
```yaml
accounts:
  - key: "schueler1_mathe"
    filters:
      include_subjects: ["MATHE"]
    calendar:
      file_name: "mathe.ics"
  
  - key: "schueler1_deutsch"
    filters:
      include_subjects: ["DEUTSCH"]
    calendar:
      file_name: "deutsch.ics"
```

### 2. Integrierter Scheduler (statt Cron)

**Use Case**: Windows oder Systeme ohne Cron.

**Implementation** (bereits vorbereitet):
```python
# In cli.py
from apscheduler.schedulers.blocking import BlockingScheduler

def cmd_schedule(args):
    cfg = Config.load(args.config)
    client = UntisClient(cfg.app)
    
    def sync():
        for acc in cfg.accounts:
            events = client.fetch_events(acc)
            ics_bytes = events_to_ics(events)
            # ...
    
    scheduler = BlockingScheduler()
    scheduler.add_job(sync, 'interval', minutes=15)
    scheduler.start()
```

### 3. Farben in ICS (begrenzt)

**Problem**: ICS-Standard kennt keine echten Event-Farben.

**Workaround**: CATEGORIES nutzen (bereits implementiert):
```python
# In ics.py
ve.add("categories", [e.subject, e.color_key])
```

Google Calendar ignoriert das meist, aber Apple Calendar kann es nutzen.

**Bessere Lösung**: Separate Kalender pro Fach/Farbe (siehe #1).

### 4. Web-UI für Konfiguration

**Idee**: FastAPI-Frontend zum Bearbeiten von `config.yaml`.

**Sketch**:
```python
@app.get("/admin")
def admin_ui():
    return HTMLResponse("""<form>...</form>""")

@app.post("/admin/save")
def save_config(data: dict):
    # Validieren, YAML schreiben
    cfg = Config.model_validate(data)
    Path("config.yaml").write_text(yaml.dump(cfg.model_dump()))
```

**Sicherheit**: Basic Auth oder Token!

### 5. Direkter Google Calendar Push (statt ICS)

**Idee**: Über Google Calendar API direkt Events eintragen.

**Pro**: Echtzeit-Updates, keine Polling-Delays.

**Contra**: OAuth kompliziert, Rate-Limits, Token-Refresh.

**Bibliothek**: `google-api-python-client`

---

## Testing

### Unit Tests

```bash
# Pytest installieren
pip install pytest

# Tests ausführen
pytest tests/
```

**Beispiel** (`tests/test_ics.py`):
```python
from untis_calendar.models import LessonEvent
from untis_calendar.ics import events_to_ics

def test_ics_contains_event():
    ev = LessonEvent(...)
    ics = events_to_ics([ev])
    assert b"BEGIN:VEVENT" in ics
    assert b"SUMMARY:MATHE" in ics
```

### Integration Test

```bash
# Vollständiger Durchlauf
python cli.py generate --config config.yaml

# Prüfen
test -s out/kalender.ics && echo "OK" || echo "FAIL"
grep -c "BEGIN:VEVENT" out/kalender.ics
```

---

## Performance

### Bottlenecks

1. **WebUntis-API**: ~500ms pro Request
2. **ICS-Generierung**: ~5ms für 100 Events (vernachlässigbar)
3. **Datei-IO**: ~1ms (vernachlässigbar)

**Empfehlung**: Cache mit TTL (bereits implementiert).

### Caching-Strategie

**Aktuell** (Datei-basiert):
```python
if file_age < cache_ttl:
    return cached_file
else:
    refresh_from_untis()
```

**Alternative** (Redis/Memcached):
```python
import redis
r = redis.Redis()
cached = r.get(f"events:{account.key}")
if cached and r.ttl(f"events:{account.key}") > 0:
    return pickle.loads(cached)
```

### Concurrency

**Für mehrere Accounts parallel**:
```python
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=5) as exe:
    futures = [exe.submit(client.fetch_events, acc) for acc in accounts]
    results = [f.result() for f in futures]
```

---

## Security Considerations

### 1. Tokens in Git

**Nie committen!**

`.gitignore`:
```
config.yaml
.env
out/
*.ics
```

### 2. Token-Länge

**Minimum**: 24 Zeichen, random.

**Generierung**:
```python
import secrets
token = secrets.token_urlsafe(32)
```

### 3. Feed ohne Token

Falls `token` und `token_env` beide fehlen, ist Feed öffentlich:
```python
# In server.py
if expected_val and token != expected_val:
    raise HTTPException(401)
# Sonst: durchlassen
```

**Best Practice**: Immer Token setzen!

### 4. Rate Limiting

**Apache** (siehe DEPLOYMENT.md):
```apache
<Location />
    SetOutputFilter RATE_LIMIT
    SetEnv rate-limit 400
</Location>
```

**FastAPI** (mit `slowapi`):
```python
from slowapi import Limiter
limiter = Limiter(key_func=lambda: request.client.host)

@app.get("/calendar/{key}.ics")
@limiter.limit("10/minute")
def get_calendar(...):
```

---

## Code-Style & Konventionen

- **Python**: PEP 8, Type Hints
- **Imports**: `from __future__ import annotations`
- **Logging**: `logger.info()` für Erfolg, `logger.error()` mit `exc_info=True`
- **Config**: Pydantic BaseModel, snake_case
- **Variablen**: sprechend (`start_dt` statt `s`)

---

## Lizenz & Credits

- MIT License (optional anpassen)
- Basiert auf `python-webuntis`, `icalendar`, `fastapi`
- WebUntis ist Trademark von Untis GmbH

---

## Kontakt

Bei technischen Fragen oder Bugs:
- GitHub Issues (falls Repo vorhanden)
- Logs mit DEBUG-Level bereitstellen
- Config (anonymisiert) anhängen
