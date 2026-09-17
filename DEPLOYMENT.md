# Deployment

Two supported ways to run this: Docker, or a virtualenv behind systemd.
Both end up serving the same feeds.

## Docker

```bash
git clone https://github.com/BxnnyG/untis-ics.git
cd untis-ics

cp config.example.yaml config.yaml    # accounts, no secrets
cp .env.example .env                  # passwords and tokens
chmod 600 .env

docker compose up -d
docker compose logs -f
```

`config.yaml` is mounted read-only. Generated feeds live in a named volume,
so the last good state survives a restart — without it, a WebUntis outage
right after a restart would leave you with nothing to serve.

Check it came up:

```bash
curl -s localhost:8080/health
curl -s "localhost:8080/status?token=$STATUS_TOKEN"
```

To build locally instead of pulling:

```bash
docker compose build
```

## systemd

### Install

```bash
sudo apt install -y python3 python3-venv git
sudo useradd --system --create-home --shell /usr/sbin/nologin untis
sudo git clone https://github.com/BxnnyG/untis-ics.git /opt/untis-ics
cd /opt/untis-ics

sudo python3 -m venv .venv
sudo .venv/bin/pip install -e .
sudo chown -R untis:untis /opt/untis-ics
```

### Configure

```bash
sudo -u untis cp config.example.yaml config.yaml
sudo -u untis nano config.yaml
```

Find your school's login name and server:

```bash
sudo -u untis .venv/bin/python find_schools.py "My School"
```

### Secrets

Keep them out of `config.yaml` and out of any directory the service user can
read:

```bash
sudo install -m 600 -o root -g root /dev/null /etc/untis-ics.env
sudo nano /etc/untis-ics.env
```

```ini
UNTIS_PASS_STUDENT1=...
FEED_TOKEN_STUDENT1=...
STATUS_TOKEN=...
HEARTBEAT_URL=...
```

systemd reads this **as root** and hands the values to the unprivileged
service, which cannot read the file itself. That matters on a host where a
web server runs under the same user: a compromised web application can read
`config.yaml`, but not this.

### Unit

`/etc/systemd/system/untis-ics.service`:

```ini
[Unit]
Description=untis-ics
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=untis
Group=untis
WorkingDirectory=/opt/untis-ics
Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=/etc/untis-ics.env
ExecStart=/opt/untis-ics/.venv/bin/untis-ics serve \
    --config /opt/untis-ics/config.yaml --host 127.0.0.1 --port 8080
Restart=on-failure
RestartSec=10s

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/untis-ics/out

[Install]
WantedBy=multi-user.target
```

Binding to `127.0.0.1` and putting a reverse proxy in front is the
recommended setup — it gives you TLS and a place for rate limiting.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now untis-ics
sudo systemctl status untis-ics
journalctl -u untis-ics -f
```

### Verify before trusting it

```bash
sudo -u untis .venv/bin/untis-ics check --config /opt/untis-ics/config.yaml
```

This reports, per account, the resolved server, whether the login works, and
the first few lessons. Run it whenever something looks off.

## CLI plus cron

If you would rather not run a service, generate the files periodically and
let any web server serve them:

```cron
*/15 6-22 * * * cd /opt/untis-ics && ./.venv/bin/untis-ics generate --config config.yaml >> /var/log/untis-ics.log 2>&1
```

`generate` exits non-zero when an account fails, so cron's mail or your job
monitor notices. It never overwrites an existing calendar with an empty one.

## Reverse proxy

### Caddy

```
cal.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name cal.example.com;

    # certbot --nginx -d cal.example.com

    # Feed tokens live in the query string; keep them out of the access log.
    access_log /var/log/nginx/untis.log combined;

    limit_req_zone $binary_remote_addr zone=untis:10m rate=30r/m;

    location / {
        limit_req zone=untis burst=10 nodelay;
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Rate limiting is deliberately not built into the application. This is where
it belongs.

### Apache

```apache
<VirtualHost *:443>
    ServerName cal.example.com

    ProxyPreserveHost On
    ProxyPass        / http://127.0.0.1:8080/
    ProxyPassReverse / http://127.0.0.1:8080/
</VirtualHost>
```

```bash
sudo a2enmod proxy proxy_http
sudo systemctl reload apache2
```

## Subscribing

```
https://cal.example.com/calendar/<key>.ics?token=<token>
```

In Google Calendar: *Other calendars → + → From URL*. One feed becomes one
calendar. Note that Google decides its own fetch cadence — usually a few
hours — and that changing a token means deleting and re-adding the
subscription, since Google cannot edit an existing URL.

## Monitoring

Point `HEARTBEAT_URL` at a Healthchecks.io check or an Uptime Kuma push
monitor. The service pings it after each refresh cycle, but only when every
enabled account has fresh data. If the service dies or a login stops working,
the ping stops and the monitor alerts.

This is worth setting up. The failure this project was built around is a sync
that quietly broke and stayed broken, because nothing was watching.

## Updating

```bash
cd /opt/untis-ics
sudo -u untis git pull
sudo .venv/bin/pip install -e .
sudo systemctl restart untis-ics
```

Docker:

```bash
docker compose pull && docker compose up -d
```

## Backup

Worth keeping: `config.yaml` and `/etc/untis-ics.env`. Everything in `out/`
is regenerated on the next refresh.

```bash
sudo tar czf untis-backup-$(date +%F).tar.gz \
    -C / opt/untis-ics/config.yaml etc/untis-ics.env
sudo chmod 600 untis-backup-*.tar.gz
```

That archive contains credentials. Treat it accordingly.

## Troubleshooting

| Symptom | Cause |
|---------|-------|
| `404` on `/WebUntis/jsonrpc.do` | School moved servers, or `school` is wrong. `untis-ics check` shows the correct host. |
| `bad credentials` | Password expired. Verify in the WebUntis web login first — repeated failures lock the account. |
| Feed returns `404` | Wrong token or unknown account key; both answer identically by design. |
| `/status` returns `404` | `status_token_env` is unset or the token is wrong. It is fail-closed. |
| Calendar empty in Google | Check `/status`. If the feed has events, Google simply has not re-fetched yet. |
| Service starts but feeds are stale | Check `journalctl` for login errors; `/status` names the failing account. |
| Permission errors on `out/` | The service user must own it: `chown -R untis:untis /opt/untis-ics/out`. |
