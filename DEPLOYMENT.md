# Deployment-Anleitung: Untis → ICS auf Debian

Vollständige Anleitung zum Betrieb des Untis-Kalender-Tools auf einem Debian-Server.

## Übersicht

Das Tool kann auf zwei Arten betrieben werden:
1. **CLI + Cron**: Periodische ICS-Generierung + statischer Webserver (Apache/Nginx)
2. **FastAPI-Server**: Dynamischer Webserver mit automatischem Refresh

Empfohlen für Debian: **CLI + Cron + Apache** (einfacher, weniger Ressourcen)

---

## Voraussetzungen

- Debian 11/12 oder Ubuntu 20.04+
- Python 3.9+ (idealerweise 3.11+)
- Root- oder sudo-Zugriff
- Optional: Apache2 oder Nginx für statische Feeds

---

## Installation auf Debian

### 1. System vorbereiten

```bash
# System aktualisieren
sudo apt update && sudo apt upgrade -y

# Python und Tools installieren
sudo apt install -y python3 python3-pip python3-venv git

# Projekt-Verzeichnis erstellen
sudo mkdir -p /opt/untis-calendar
sudo chown $USER:$USER /opt/untis-calendar
cd /opt/untis-calendar

# Code hochladen (via git, scp oder rsync)
# Beispiel: rsync -avz . user@server:/opt/untis-calendar/
```

### 2. Python-Umgebung einrichten

```bash
cd /opt/untis-calendar

# Virtual Environment erstellen
python3 -m venv .venv

# Aktivieren
source .venv/bin/activate

# Dependencies installieren
pip install -U pip
pip install -r requirements.txt
```

### 3. Konfiguration anpassen

```bash
# Config aus Beispiel kopieren
cp config.example.yaml config.yaml
nano config.yaml
```

Wichtige Einstellungen in `config.yaml`:
```yaml
app:
  output_dir: "/var/www/untis-calendar"  # Webserver-Root
  
accounts:
  - key: "account1"
    school: "schulname"  # Exakt aus WebUntis-URL!
    server: "alt-server.webuntis.com"  # Dein WebUntis-Server
    username: "user"
    password: "pass"  # Oder password_env: "ENV_VAR"
    verify_ssl: false  # Nur bei selbst-signierten Zertifikaten
    calendar:
      file_name: "kalender1.ics"
      web_feed: true
      token: "geheimer-token-hier"  # Für URL-Absicherung
```

### 4. Ersten Test durchführen

```bash
# Aktiviere venv falls noch nicht aktiv
source .venv/bin/activate

# ICS generieren
python cli.py generate --config config.yaml

# Prüfen
ls -lh out/
cat out/*.ics | head -20
```

---

## Variante A: CLI + Cron (Empfohlen)

### 1. Output-Verzeichnis für Webserver

```bash
# Webserver-Root erstellen
sudo mkdir -p /var/www/untis-calendar
sudo chown www-data:www-data /var/www/untis-calendar

# In config.yaml setzen:
# app.output_dir: "/var/www/untis-calendar"
```

### 2. Cron-Job einrichten

Bearbeite Crontab:
```bash
crontab -e
```

Füge hinzu (alle 15 Minuten):
```cron
*/15 * * * * cd /opt/untis-calendar && /opt/untis-calendar/.venv/bin/python cli.py generate --config /opt/untis-calendar/config.yaml >> /var/log/untis-calendar.log 2>&1
```

Oder stündlich (zur vollen Stunde):
```cron
0 * * * * cd /opt/untis-calendar && /opt/untis-calendar/.venv/bin/python cli.py generate --config /opt/untis-calendar/config.yaml >> /var/log/untis-calendar.log 2>&1
```

Log-Rotation einrichten:
```bash
sudo nano /etc/logrotate.d/untis-calendar
```

Inhalt:
```
/var/log/untis-calendar.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
}
```

### 3. Apache einrichten (statische ICS-Feeds)

```bash
# Apache installieren
sudo apt install -y apache2

# Virtual Host erstellen
sudo nano /etc/apache2/sites-available/untis-calendar.conf
```

Inhalt:
```apache
<VirtualHost *:80>
    ServerName kalender.deine-domain.de
    DocumentRoot /var/www/untis-calendar

    <Directory /var/www/untis-calendar>
        Options -Indexes +FollowSymLinks
        AllowOverride None
        Require all granted
        
        # CORS für Kalender-Clients
        Header set Access-Control-Allow-Origin "*"
        
        # Caching (15 Minuten)
        <FilesMatch "\.ics$">
            Header set Content-Type "text/calendar; charset=utf-8"
            Header set Cache-Control "max-age=900, public"
        </FilesMatch>
    </Directory>

    ErrorLog ${APACHE_LOG_DIR}/untis-calendar-error.log
    CustomLog ${APACHE_LOG_DIR}/untis-calendar-access.log combined
</VirtualHost>
```

Aktivieren:
```bash
# Modul aktivieren
sudo a2enmod headers

# Site aktivieren
sudo a2ensite untis-calendar.conf
sudo systemctl reload apache2
```

### 4. Feed-URLs nutzen

Feeds sind jetzt erreichbar unter:
```
http://kalender.deine-domain.de/kalender1.ics?token=geheimer-token-hier
```

Für Google Calendar:
1. Google Calendar öffnen
2. Links: "Weitere Kalender" → "Per URL hinzufügen"
3. URL eingeben mit Token
4. Fertig!

---

## Variante B: FastAPI-Server (Dynamisch)

### 1. Systemd Service erstellen

```bash
sudo nano /etc/systemd/system/untis-calendar.service
```

Inhalt:
```ini
[Unit]
Description=Untis ICS Calendar Server
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/untis-calendar
Environment="PYTHONUNBUFFERED=1"
ExecStart=/opt/untis-calendar/.venv/bin/python cli.py serve --config /opt/untis-calendar/config.yaml --host 127.0.0.1 --port 8080
Restart=on-failure
RestartSec=10s

# Security
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Service aktivieren:
```bash
sudo systemctl daemon-reload
sudo systemctl enable untis-calendar
sudo systemctl start untis-calendar
sudo systemctl status untis-calendar
```

### 2. Apache Reverse Proxy

```bash
# Module aktivieren
sudo a2enmod proxy proxy_http

# Virtual Host anpassen
sudo nano /etc/apache2/sites-available/untis-calendar.conf
```

Inhalt:
```apache
<VirtualHost *:80>
    ServerName kalender.deine-domain.de

    ProxyPreserveHost On
    ProxyPass / http://127.0.0.1:8080/
    ProxyPassReverse / http://127.0.0.1:8080/

    ErrorLog ${APACHE_LOG_DIR}/untis-calendar-error.log
    CustomLog ${APACHE_LOG_DIR}/untis-calendar-access.log combined
</VirtualHost>
```

Reload:
```bash
sudo systemctl reload apache2
```

Feed-URLs:
```
http://kalender.deine-domain.de/calendar/account1.ics?token=geheimer-token-hier
```

---

## SSL/HTTPS einrichten (Let's Encrypt)

```bash
# Certbot installieren
sudo apt install -y certbot python3-certbot-apache

# Zertifikat anfordern
sudo certbot --apache -d kalender.deine-domain.de

# Auto-Renewal prüfen
sudo certbot renew --dry-run
```

Feeds dann über HTTPS:
```
https://kalender.deine-domain.de/kalender1.ics?token=...
```

---

## Troubleshooting

### Problem: "invalid schoolname"

**Ursache**: Schulname in `config.yaml` ist falsch oder case-sensitive.

**Lösung**:
1. WebUntis im Browser öffnen
2. Nach Login URL prüfen: `https://SERVER/WebUntis/?school=SCHULNAME`
3. Exakten Namen (inkl. Groß-/Kleinschreibung) in Config übernehmen

### Problem: SSL-Zertifikatsfehler

**Ursache**: Server nutzt selbst-signiertes Zertifikat.

**Lösung**: In `config.yaml` setzen:
```yaml
accounts:
  - verify_ssl: false
```

### Problem: Keine Events in ICS

**Prüfungen**:
```bash
# Log anschauen
tail -f /var/log/untis-calendar.log

# Manuell testen mit Debug
source /opt/untis-calendar/.venv/bin/activate
python cli.py generate --config config.yaml

# ICS prüfen
cat /var/www/untis-calendar/*.ics | grep "BEGIN:VEVENT" | wc -l
```

**Häufige Ursachen**:
- Falscher Username/Passwort
- Falscher Schulname
- Falscher Server
- Zeitfenster außerhalb der Unterrichtszeiten (siehe `window_days_before/after`)

### Problem: "unhashable type: 'list'"

**Ursache**: Bug beim Parsen von WebUntis-Daten (bereits gefixt in aktueller Version).

**Lösung**: Aktuellen Code verwenden (siehe `untis_client.py` Subject-Parsing).

### Problem: Cron läuft nicht

**Prüfungen**:
```bash
# Cron-Log prüfen
sudo grep CRON /var/log/syslog | tail -20

# Manuell ausführen
cd /opt/untis-calendar
source .venv/bin/activate
python cli.py generate --config config.yaml

# Pfade in Cron absolut angeben!
```

### Problem: Permissions

```bash
# Output-Verzeichnis Rechte prüfen
ls -ld /var/www/untis-calendar
sudo chown -R www-data:www-data /var/www/untis-calendar
sudo chmod 755 /var/www/untis-calendar
```

---

## Wartung

### Updates installieren

```bash
cd /opt/untis-calendar
source .venv/bin/activate

# Dependencies aktualisieren
pip install -U -r requirements.txt

# Service neustarten (falls FastAPI)
sudo systemctl restart untis-calendar
```

### Logs überwachen

```bash
# Cron-Log
tail -f /var/log/untis-calendar.log

# Apache-Log
sudo tail -f /var/log/apache2/untis-calendar-access.log

# Systemd-Log (FastAPI)
sudo journalctl -u untis-calendar -f
```

### Backup

```bash
# Config sichern
cp /opt/untis-calendar/config.yaml ~/config.yaml.backup

# Oder automatisch (täglich)
echo "0 2 * * * cp /opt/untis-calendar/config.yaml /backup/untis-config-\$(date +\%Y\%m\%d).yaml" | crontab -
```

---

## Performance-Tipps

### Cache-TTL anpassen

In `config.yaml`:
```yaml
app:
  cache_ttl_seconds: 900  # 15 Minuten (Standard: 600)
```

### Zeitfenster reduzieren

```yaml
app:
  window_days_before: 1   # Statt 3
  window_days_after: 14   # Statt 28
```

### Apache Compression

```bash
sudo a2enmod deflate
```

In Virtual Host:
```apache
<IfModule mod_deflate.c>
    AddOutputFilterByType DEFLATE text/calendar
</IfModule>
```

---

## Sicherheit

### Tokens schützen

- Lange, zufällige Tokens verwenden (min. 24 Zeichen)
- Verschiedene Tokens pro Account/Feed
- Nicht in Git committen

### Firewall einrichten

```bash
# UFW aktivieren
sudo apt install -y ufw
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable
```

### Rate Limiting (Apache)

```bash
sudo a2enmod ratelimit
```

In Virtual Host:
```apache
<Location />
    SetOutputFilter RATE_LIMIT
    SetEnv rate-limit 400
</Location>
```

---

## Kontakt & Support

Bei Problemen:
1. Logs prüfen (siehe Troubleshooting)
2. Config auf Tippfehler prüfen
3. Manuellen Test durchführen
4. Python-Version prüfen (`python3 --version` >= 3.9)

Projekt-Repository: (URL einfügen wenn vorhanden)
