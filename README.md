# untis-ics

Turn WebUntis timetables into ICS calendar feeds you can subscribe to in
Google Calendar, Apple Calendar, Thunderbird or anything else that speaks
iCalendar.

One feed per account. Cancelled lessons stay visible instead of silently
vanishing, rescheduled lessons say where they moved to, and a failed fetch
never wipes a working calendar.

## Why this exists

WebUntis moves schools between servers and occasionally renames their login
names. When that happens, `/WebUntis/jsonrpc.do` starts returning **404** on
the host you configured, every sync fails, and — depending on your tooling —
you end up with silently empty calendars.

This project resolves the responsible server at runtime through the official
school search and retries once against the new host, so a migration does not
break the sync. It also refuses to overwrite a good calendar with an empty
one when a fetch fails.

## Requirements

- Python 3.10+
- A WebUntis account (student, teacher or class login)

## Install

```bash
git clone https://github.com/BxnnyG/untis-ics.git
cd untis-ics
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Or with Docker — see [Docker](#docker) below.

## Configure

Copy `config.example.yaml` to `config.yaml` and edit it.

```yaml
app:
  timezone: "Europe/Berlin"
  window_days_before: 3
  window_days_after: 28
  output_dir: "./out"

accounts:
  - key: "student1"           # appears in the feed URL
    school: "myschool"        # WebUntis loginName, not the display name
    username: "MyUser"
    password_env: "UNTIS_PASS_STUDENT1"
    calendar:
      file_name: "student1.ics"
      display_name: "My timetable"
      web_feed: true
      token_env: "FEED_TOKEN_STUDENT1"
```

Two things trip people up:

- **`school` is the WebUntis `loginName`**, not the name on the website.
  Find it with `untis-ics` helper:
  ```bash
  python find_schools.py "My School Name"
  ```
- **`server` is optional.** Leave it out and the correct host is resolved
  automatically — recommended, since schools get migrated.

Every setting under `app:` and `server:` can also be set from the
environment, which is what the Docker image uses:

```
UNTIS_APP_TIMEZONE=Europe/Vienna
UNTIS_APP_REFRESH_INTERVAL_MINUTES=10
UNTIS_SERVER_DOCS_ENABLED=false
```

Passwords and feed tokens never belong in `config.yaml` — see
[Security](#security).

## Run

```bash
# Check every account: school lookup, login, first lessons
untis-ics check --config config.yaml

# Check one account (works for disabled ones too)
untis-ics check --config config.yaml --only student1

# Write the .ics files once (good for cron)
untis-ics generate --config config.yaml

# Run the server (serves feeds and refreshes in the background)
untis-ics serve --config config.yaml --host 0.0.0.0 --port 8080
```

`untis-ics check` is the first thing to run when something looks wrong. It
compares the configured server against the school search and tells you
whether the login still works.

### Endpoints

| Path | Purpose |
|------|---------|
| `/health` | Liveness. Public, reveals nothing about accounts. |
| `/status?token=…` | Per account: last success, last error, event count, file age, plus an overall `healthy` flag. Requires `status_token_env`. |
| `/calendar/<key>.ics?token=…` | The feed itself. |

### systemd

```ini
[Unit]
Description=untis-ics
After=network-online.target
Wants=network-online.target

[Service]
User=untis
WorkingDirectory=/opt/untis-ics
EnvironmentFile=/etc/untis-ics.env
ExecStart=/opt/untis-ics/.venv/bin/untis-ics serve --config /opt/untis-ics/config.yaml
Restart=on-failure
RestartSec=10s
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

### Docker

```bash
cp config.example.yaml config.yaml   # edit it
cp .env.example .env                 # add passwords and tokens
docker compose up -d
```

The image runs as an unprivileged user, keeps generated feeds in a named
volume (so a WebUntis outage right after a restart cannot leave you with an
empty feed) and ships a healthcheck. `config.yaml` is mounted read-only;
secrets come from the environment.

## Google Calendar

One feed is one calendar in Google: *Other calendars → + → From URL*.

Things worth knowing before you file a bug:

- **Use the https URL.**
- **Google decides how often it fetches** — typically every few hours — and
  ignores `REFRESH-INTERVAL` in the feed. A short refresh interval here keeps
  the feed fresh; it does not make Google pull sooner. For an immediate look,
  open the feed URL directly.
- Google shows the URL as the calendar name at first. Rename it in the
  calendar settings.
- **A changed token means re-subscribing.** Google cannot edit the URL of an
  existing subscription; you have to remove it and add it again.

## Cancelled lessons

Google **hides events with `STATUS:CANCELLED`** in subscribed calendars. Set
that status and the lesson disappears entirely instead of being marked. So
by default this project does not set it:

| `app.cancelled_style` | Behaviour |
|------------------------|-----------|
| `mark` (default) | Event stays visible, title starts with `❌ Entfällt`, `TRANSP:TRANSPARENT` so the time no longer counts as busy |
| `status` | Sets `STATUS:CANCELLED` (spec-correct, usually invisible in Google) |
| `hide` | Cancelled lessons are left out entirely |

There is no strikethrough rendering for subscribed calendars in Google;
`mark` is as close as it gets. Per account, `include_cancelled: false` drops
them regardless.

### Reschedules and substitutions

When a school moves a lesson, WebUntis produces **two** entries: the old slot
counts as cancelled, the lesson happens at the new one. Both are linked, so
each end says where it points:

```
Tue 22 Sep 17:00   ❌ Verlegt · Netzwerktechnik … → Di 15.09. 18:40
Tue 15 Sep 18:40   ➡️ Netzwerktechnik … · R102
                      Verlegt – ursprünglich Di 22.09. 17:00.
```

The state comes first in the title on purpose: Google truncates titles in
month and week view, and `❌ Verlegt …` has to survive that. Cancelled
lessons drop the room — it is meaningless at that point.

If only the teacher or room changed, the title gets `⚠️` and the description
names the swap (`Lehrer: MY statt VS`).

This needs the REST view (`fetch_online_info`). The older JSON-RPC interface
only reports that *something* deviates, never what.

## Online lessons

Lessons flagged as online in WebUntis get `💻` in the title, the category
`Online`, and — if one is stored — the meeting link in the `URL` property, in
the description, and as `LOCATION` when no room is assigned.

Two things from practice:

- Many schools set the online flag but store no URL (WebUntis then returns
  the placeholder `"0"`). Such values are discarded and the event just says
  no link is available.
- More often the link sits in the lesson text. That text is searched too, and
  a link found there marks the lesson as online.

## Colours

Schools assign a colour per subject in WebUntis. Those are carried over when
`use_untis_colors` is on.

RFC 7986 only allows **CSS colour names** for `COLOR`, not hex, so the
nearest named colour is used and the exact value is attached as
`X-APPLE-CALENDAR-COLOR`. **Google ignores both** for subscribed calendars —
it paints the whole subscription in one colour. Apple Calendar and several
other clients honour them.

## Refresh

In server mode a background task refreshes the feeds. The interval follows
the time of day, because a timetable does not change overnight:

```yaml
refresh_interval_minutes: 15   # during active hours
refresh_idle_minutes: 120      # outside (0 = always the same interval)
active_hours_start: 6
active_hours_end: 22
```

Feed requests are always answered **from disk** and never trigger a WebUntis
call, which keeps responses fast and means client polling creates no load. A
live fetch only happens when no file exists yet or the background task
clearly stalled. Concurrent requests for the same account are collapsed
behind a lock.

Transient network errors (connection resets, timeouts, 429, 5xx) are retried
with growing backoff. `4xx` responses are **not** retried — repeated failed
logins get WebUntis accounts locked.

## Monitoring

The service can send a heartbeat to an external monitor after each refresh
cycle, but only when every enabled account has fresh data:

```yaml
server:
  heartbeat_url_env: "HEARTBEAT_URL"
  stale_after_minutes: 0    # 0 = three times the refresh interval
```

It is deliberately a dead man's switch. If the service hangs, crashes or a
login stops working, the ping stops and your monitor raises the alarm — so
this project needs no mail delivery or alerting logic of its own. Works with
Healthchecks.io, Uptime Kuma push monitors and anything else expecting an
HTTP call. On failure `/fail` is appended, which Healthchecks.io understands
as an immediate failure.

This matters more than it sounds: the failure mode this project was built
around is a sync that breaks and stays broken for months because nothing
tells you.

## Security

**No credentials belong in this repository.** `config.yaml`, `.env` and
`out/` are in `.gitignore` — the real config holds usernames and schools, and
generated `.ics` files contain complete timetables with teacher names.

Put passwords and tokens in a root-owned file that systemd reads:

```bash
sudo install -m 600 -o root -g root /dev/null /etc/untis-ics.env
sudo nano /etc/untis-ics.env        # template: .env.example
```

```ini
[Service]
User=untis
EnvironmentFile=/etc/untis-ics.env
```

systemd reads the file **as root** and passes the values to the unprivileged
service, which cannot read the file itself. If a web server runs under the
same user on that host, a compromised web application cannot read your
WebUntis passwords — with plaintext in `config.yaml` it could.

This is not encryption: the service must know the password to log in. It only
limits who can read it off disk.

Defaults that matter:

| Setting | Effect |
|---------|--------|
| `docs_enabled: false` | `/docs`, `/redoc` and `/openapi.json` are not served. They describe the attack surface and the Swagger UI loads JavaScript from a third-party CDN. |
| `status_token_env` | `/status` exposes account keys, schools and error text, so it needs a token. Without one it answers 404 — fail-closed, so it cannot be left open by accident. |
| `security_headers: true` | `X-Content-Type-Options`, `X-Frame-Options`, a restrictive CSP and `Referrer-Policy: no-referrer`, so the token cannot leak through the referrer when a feed URL is opened in a browser. |
| `redact_tokens_in_logs: true` | The token has to sit in the query string because Google cannot pass it any other way. Without this filter every token ends up in your journal and anything that ships logs. |

Also:

- An unknown account and a wrong token return the **same** 404. Different
  errors would let someone enumerate valid account keys.
- Tokens are compared with `secrets.compare_digest`.
- Feeds are served `Cache-Control: private` so shared caches and proxies do
  not retain timetables.
- Give every feed its **own** token. A shared one means whoever has one URL
  can read every timetable.

Not included: rate limiting. Add it in your reverse proxy if the feed is
publicly reachable.

## A note on language

Code, comments and documentation are English. The **calendar output is
German** — `❌ Entfällt`, `Verlegt`, `Lehrer:`, `3. Stunde` — because WebUntis
is a German-speaking-market product and the people reading these calendars are
at schools in Germany, Austria and Switzerland. Making that text
translatable would be a welcome contribution; it is currently hardcoded in
`ics.py`.

## Troubleshooting

| Symptom | Cause |
|---------|-------|
| `404` on `/WebUntis/jsonrpc.do` | The school moved to another server, or `school` is wrong. `untis-ics check` shows the correct one. |
| `bad credentials` | Password or user expired. Try the WebUntis web login first. |
| Calendar empty in Google | Check `/status`. If the feed has data, Google simply has not fetched again yet. |
| Feed returns `404` | Wrong token or unknown account key — both answer identically on purpose. |
| No teacher names | Class logins (`personType 1`) do not receive a teacher field from WebUntis. Nothing to fix on this side. |

A failed fetch never overwrites an existing `.ics` file with an empty
calendar, so a brief WebUntis outage does not empty your calendar.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
```

```
untis_calendar/
  config.py         # YAML + environment, validation
  school_lookup.py  # school name -> current WebUntis server
  untis_direct.py   # JSON-RPC client (authenticate, getTimetable)
  untis_rest.py     # REST enrichment: online lessons, reschedules, colours
  untis_client.py   # fetch, map, filter, merge consecutive lessons
  ics.py            # ICS generation
  colors.py         # Untis hex colours -> CSS names
  retry.py          # backoff for transient network errors
  heartbeat.py      # dead man's switch ping
  server.py         # FastAPI feeds + background refresh
  __main__.py       # CLI: generate | check | serve
```

## Docs

- [ARCHITECTURE.md](ARCHITECTURE.md) — design decisions and internals
- [DEPLOYMENT.md](DEPLOYMENT.md) — full deployment walkthrough
- [CHANGELOG.md](CHANGELOG.md)

## License

MIT — see [LICENSE](LICENSE).
