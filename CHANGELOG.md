# Changelog

Notable changes to this project. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions follow
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-09-17

First public release. The project existed before this, but silently produced
empty calendars for months; this release is where that was fixed and the
service was made fit to run in public.

### Fixed

- **Schools migrating between WebUntis servers broke every sync.** A
  hardcoded host returns `404` on `/WebUntis/jsonrpc.do` once a school is
  moved. The responsible server is now resolved through the official school
  search, and a `404` triggers one retry against the newly resolved host.
  The legacy JSON-RPC API is *not* discontinued, which the `404` suggests.
- **Failed fetches overwrote working calendars.** `fetch_events()` swallowed
  every exception and returned an empty list, which was then written out as
  an empty calendar. Errors now propagate and the last good file is kept.
- **Disabled accounts could still trigger logins** through the feed endpoint,
  risking account lockout with an expired password.
- `element:` built parameters for the old library instead of the JSON-RPC
  API and never worked.
- `REFRESH-INTERVAL` was emitted as `1:00:00` instead of `PT1H` and was
  therefore not a valid iCalendar duration.
- `x-wr-timezone` was hardcoded to `Europe/Berlin` despite `app.timezone`
  being configurable.
- `requests` was missing from the dependencies and only came in transitively.
- `logging_config` used `str | None` without `from __future__ import
  annotations`, so importing it failed on older interpreters.
- Removed `auth.py`, which patched `requests.Session.request` globally and
  permanently disabled TLS verification process-wide.

### Added

- **Cancelled, rescheduled and substituted lessons are readable.** Google
  hides `STATUS:CANCELLED` in subscribed calendars, so cancelled lessons stay
  visible and marked instead. Reschedules link both ends (`→ Di 15.09.
  18:40`), substitutions name what changed.
- **Online lessons** are detected via the REST view, including meeting links
  from the `videoCall` field or the lesson text. Placeholder URLs (`"0"`) are
  discarded.
- **Subject colours** from WebUntis, mapped to the nearest CSS colour name
  for RFC 7986 `COLOR` plus the exact value as `X-APPLE-CALENDAR-COLOR`.
- **Period numbers** from `getTimegridUnits` (`3. Stunde`, `1.-2. Stunde`).
- **Heartbeat** to an external monitor as a dead man's switch, so a sync that
  stops working does not go unnoticed again.
- **Retry with backoff** for transient network errors. `4xx` is never
  retried, because repeated failed logins lock WebUntis accounts.
- Consecutive identical lessons are merged into one event.
- Time-of-day dependent refresh interval; feed requests are served from disk
  and never trigger a WebUntis call.
- `check` command for diagnosing accounts, `enabled` flag per account,
  `/status` endpoint.
- Environment overrides for every `app.*` and `server.*` setting.
- Docker image and compose file.
- Test suite and ruff configuration.

### Changed

- Minimum Python is **3.10**. Pydantic cannot evaluate `str | None`
  annotations on 3.9 without an extra backport dependency, and 3.9 reached
  end of life in October 2025 — carrying it was cost without benefit.

### Security

- `/status` was reachable without authentication and exposed account keys,
  school login names, file paths and error text. Now token-protected and
  fail-closed.
- Feed tokens were written to access logs in plaintext and ended up in the
  system journal. They are redacted now.
- Feeds were served `Cache-Control: public` despite being token-protected
  personal data. Now `private`.
- An unknown account and a wrong token returned different status codes,
  making account keys enumerable. Both return `404` now.
- `/docs`, `/redoc` and `/openapi.json` are disabled by default.
- Token comparison uses `secrets.compare_digest`.
- Added `X-Content-Type-Options`, `X-Frame-Options`, a restrictive CSP and
  `Referrer-Policy: no-referrer`.
- Passwords and tokens moved out of `config.yaml` into an environment file
  that the service user cannot read.

[1.0.0]: https://github.com/BxnnyG/untis-ics/releases/tag/v1.0.0
