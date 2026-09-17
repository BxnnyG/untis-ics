# Architecture

How the pieces fit together and why they are shaped that way.

## Data flow

```
config.yaml + environment
  └─> Config.load()                       config.py
      └─> UntisClient.fetch_events()      untis_client.py
          ├─> resolve_server()            school_lookup.py   (if no server configured)
          ├─> direct_untis_login()        untis_direct.py    JSON-RPC: authenticate
          │     ├─> getTimetable                             the actual lessons
          │     └─> getTimegridUnits                         period numbers
          ├─> fetch_lesson_extras()       untis_rest.py      REST: online, reschedules, colours
          ├─> _map_raw_to_event()                            raw JSON -> LessonEvent
          ├─> _link_moved_lessons()                          connect both ends of a reschedule
          ├─> _filter_event()                                include/exclude subjects
          └─> _merge_consecutive()                           double periods into one event
      └─> events_to_ics()                 ics.py
          └─> file in output_dir
              └─> served by server.py, or written by the CLI
```

## Two APIs, on purpose

WebUntis exposes an old JSON-RPC interface and a newer REST view. This
project uses both, for different things.

**JSON-RPC (`untis_direct.py`) is the source of truth.** It returns the
lessons, including cancelled ones, and is stable across the instances tested.

**REST (`untis_rest.py`) only enriches.** It knows things JSON-RPC does not:
whether a lesson is online, whether it was moved and from where, what was
substituted, and the colour a school assigned to a subject. Its results are
matched onto the JSON-RPC lessons by period id.

The split matters because the REST view **omits cancelled lessons entirely**.
Relying on it alone would silently drop exactly the events users care most
about. Enrichment is therefore optional and failure-tolerant: if the REST
call fails, the sync continues without those extras rather than aborting.

### Why not the `webuntis` library

The library adds a dependency and an abstraction layer without solving the
problems this project actually has — server migration, cancelled lessons,
online lessons, reschedules. Two direct HTTP clients are less code than
working around it.

## Design decisions

### Servers are resolved at runtime

A school moving between WebUntis servers makes `/WebUntis/jsonrpc.do` return
`404` on the old host. That looks like a discontinued API but is not.
`school_lookup.py` asks the official school search which host is responsible
and caches the answer for a day. `DirectUntisSession.login()` retries once
against a freshly resolved host when it sees a `404`.

Configuring `server:` explicitly still works and skips a lookup; the retry
covers the case where that configured value goes stale.

### Failures must not destroy data

The original failure mode: `fetch_events()` caught every exception, returned
an empty list, and the caller wrote that out as a valid but empty calendar,
replacing good data.

Now errors propagate and every writer decides deliberately:

- The CLI keeps the existing file and exits non-zero.
- The server keeps the existing file and serves the last good state.
- A suspiciously empty result — no events where a populated file already
  exists — is also treated as a failure rather than written out.

### Stable UIDs

Event UIDs hash the WebUntis period id, not the time or room. A lesson that
moves keeps its UID, so calendar clients update the existing entry instead of
leaving a stale duplicate. This is also what makes reschedule linking and any
future diffing possible.

### Disabled accounts never authenticate

`enabled: false` removes an account from background refresh *and* from live
fetches triggered by feed requests. Repeated failed logins lock WebUntis
accounts, so an account with an expired password must not keep trying — not
even because someone (or Google) polls its feed.

### Retries are narrow on purpose

`retry.py` retries connection errors, timeouts, `429` and `5xx`. It
deliberately does **not** retry `4xx`: retrying a rejected login is how you
get an account locked. `is_transient()` encodes that, and a test pins it.

### Presentation choices in `ics.py`

- **Cancelled lessons keep `STATUS:CONFIRMED` by default.** Google hides
  `STATUS:CANCELLED` in subscribed calendars, so setting it correctly makes
  the lesson vanish. `TRANSP:TRANSPARENT` frees the time instead, and the
  title carries the marker.
- **State comes first in the title.** Google truncates titles in grid views;
  `❌ Verlegt …` has to survive truncation.
- **`LOCATION` stays the room number**, not the room's descriptive name —
  that is what you need to find it. Cancelled lessons get no location at all.
- **Colours** are emitted as a CSS name (RFC 7986 allows nothing else) plus
  `X-APPLE-CALENDAR-COLOR` for the exact value.

## Refresh model

In server mode, a background task owns freshness. Feed requests are answered
from disk and never trigger a WebUntis call, so client polling — including
Google's — creates no load and always gets a fast response. A live fetch only
happens when no file exists or the background task has clearly stalled
(file older than three intervals). An `asyncio.Lock` per account collapses
concurrent requests.

The interval depends on the time of day: a timetable does not change at 3am,
so polling at the daytime rate all night is pure load on the school's server.

## Configuration

One YAML file, with two escape hatches:

- `password_env` / `token_env` / `status_token_env` / `heartbeat_url_env`
  read secrets from the environment, so the config file itself stays free of
  credentials and safe to commit as an example.
- Any `app.*` or `server.*` key can be overridden by `UNTIS_APP_*` /
  `UNTIS_SERVER_*`, which is how the container is configured. Applied
  overrides are logged, because silently altered configuration is miserable
  to debug.

Accounts are intentionally *not* configurable from the environment. Expressing
a list of nested objects in environment variables produces a worse interface
than a mounted file.

## Testing

Tests run against captured real WebUntis payloads rather than invented ones,
because the shape of that data is the thing most likely to surprise. Several
tests pin behaviour that is easy to regress and expensive to get wrong:

- `4xx` is never retried (account lockout).
- Disabled accounts never trigger a login.
- Wrong token and unknown account return identical responses.
- Cancelled lessons stay visible by default.
- `HHMM` times parse as clock times, not minute offsets.

## Known limits

- **Exams and homework** (`getExams`, `getHomeWork`) return
  `Method not found` on the instances tested — they are disabled server-side
  and cannot be fetched.
- **Class logins** (`personType 1`) receive no teacher field from WebUntis.
  The abbreviations appear only inside the student group string.
- **Google ignores per-event colours** and its own refresh cadence is not
  controllable from the feed.
- **No rate limiting.** That belongs in a reverse proxy.
