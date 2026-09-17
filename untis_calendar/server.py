from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, Response

from . import heartbeat
from .config import AccountConfig, AppConfig, Config
from .ics import events_to_ics
from .logging_config import setup_logging
from .untis_client import UntisClient

logger = logging.getLogger(__name__)


_TOKEN_IN_URL = re.compile(r"(?i)([?&](?:token|access_token)=)[^&\s\"']+")


class RedactTokensFilter(logging.Filter):
    """Replace tokens in access logs with ***.

    The feed token has to sit in the query string - Google cannot pass it any
    other way. Uvicorn logs the full URL, so without this every token ends up
    in the journal and in anything that ships logs onward.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(
                _TOKEN_IN_URL.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args
            )
        if isinstance(record.msg, str):
            record.msg = _TOKEN_IN_URL.sub(r"\1***", record.msg)
        return True


def token_matches(expected: str | None, given: str | None) -> bool:
    """Constant-time comparison so the token cannot be guessed by timing."""
    if not expected:
        return True  # no token configured -> the feed is open
    if not given:
        return False
    return secrets.compare_digest(expected, given)


def stale_threshold_minutes(cfg: Config) -> int:
    """Age at which an account's data counts as stale."""
    if cfg.server.stale_after_minutes > 0:
        return cfg.server.stale_after_minutes
    base = max(cfg.app.refresh_interval_minutes, cfg.app.refresh_idle_minutes)
    return max(3 * base, 30)


def compute_interval_minutes(app: AppConfig, now_local: datetime | None = None) -> int:
    """Minutes to wait until the next background refresh.

    Often during active hours, rarely outside them - a timetable does not
    change overnight, and polling at the same rate around the clock loads
    WebUntis for nothing.
    """
    if app.refresh_idle_minutes <= 0:
        return app.refresh_interval_minutes

    if now_local is None:
        try:
            now_local = datetime.now(ZoneInfo(app.timezone))
        except Exception:
            now_local = datetime.now()

    start, end = app.active_hours_start, app.active_hours_end
    hour = now_local.hour
    if start <= end:
        active = start <= hour < end
    else:
        # Window spanning midnight, e.g. 22 to 6
        active = hour >= start or hour < end
    return app.refresh_interval_minutes if active else app.refresh_idle_minutes


class FeedState:
    """Remembers the last success/failure per account for /status."""

    def __init__(self) -> None:
        self.last_success: datetime | None = None
        self.last_error: str | None = None
        self.last_error_at: datetime | None = None
        self.event_count: int = 0


def create_app(config_path: str) -> FastAPI:
    setup_logging()
    cfg = Config.load(config_path)
    client = UntisClient(cfg.app)
    out_dir = Path(cfg.app.output_dir)
    states: dict[str, FeedState] = {a.key: FeedState() for a in cfg.accounts}
    # Stops concurrent requests from fetching the same account at once
    locks: dict[str, asyncio.Lock] = {a.key: asyncio.Lock() for a in cfg.accounts}

    def refresh_account(account: AccountConfig) -> bytes | None:
        """Fetch fresh data and write it out.

        Returns None when the fetch failed - the existing file is then left
        untouched.
        """
        state = states[account.key]
        out_file = out_dir / account.calendar.file_name
        try:
            events = client.fetch_events(account)
        except Exception as e:
            state.last_error = str(e)
            state.last_error_at = datetime.now(timezone.utc)
            logger.error("Fetch for '%s' failed: %s", account.key, e)
            logger.debug("Details:", exc_info=True)
            return None

        if not events and out_file.exists() and out_file.stat().st_size > 200:
            # Suspicious: there was data before and none now. Do not overwrite.
            state.last_error = "Empty result - keeping the existing file"
            state.last_error_at = datetime.now(timezone.utc)
            logger.warning("Empty result for '%s' - keeping %s", account.key, out_file)
            return None

        ics_bytes = events_to_ics(
            events,
            calendar_name=account.calendar.display_name or f"Stundenplan {account.key}",
            refresh_minutes=cfg.app.refresh_interval_minutes or 60,
            subject_style=cfg.app.subject_style,
            cancelled_style=cfg.app.cancelled_style,
            timezone_name=cfg.app.timezone,
            use_colors=cfg.app.use_untis_colors,
        )
        out_file.write_bytes(ics_bytes)
        state.last_success = datetime.now(timezone.utc)
        state.event_count = len(events)
        state.last_error = None
        logger.info("Updated: %s (%d events)", out_file, len(events))
        return ics_bytes

    def healthy() -> tuple[bool, list[str]]:
        """Does every enabled account have fresh data?"""
        limit = timedelta(minutes=stale_threshold_minutes(cfg))
        now = datetime.now(timezone.utc)
        problems = []
        for account in cfg.active_accounts:
            st = states[account.key]
            if st.last_success is None:
                problems.append(f"{account.key}: no successful fetch yet")
            elif now - st.last_success > limit:
                age = int((now - st.last_success).total_seconds() // 60)
                problems.append(f"{account.key}: last success {age} min ago")
        return not problems, problems

    async def refresh_loop() -> None:
        while True:
            for account in cfg.active_accounts:
                try:
                    await asyncio.to_thread(refresh_account, account)
                except Exception:
                    logger.exception("Unexpected error refreshing '%s'", account.key)

            url = cfg.server.heartbeat
            if url:
                ok, problems = healthy()
                if not ok:
                    logger.warning("Heartbeat reporting problems: %s", "; ".join(problems))
                await asyncio.to_thread(
                    heartbeat.send, url, ok, 10, "; ".join(problems) or "all accounts up to date"
                )

            minutes = compute_interval_minutes(cfg.app)
            logger.debug("Next refresh in %d minutes", minutes)
            await asyncio.sleep(minutes * 60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if cfg.app.refresh_interval_minutes > 0:
            task = asyncio.create_task(refresh_loop())
            logger.info(
                "Background refresh active: every %d min between %02d:00 and %02d:00, "
                "otherwise every %d min",
                cfg.app.refresh_interval_minutes,
                cfg.app.active_hours_start,
                cfg.app.active_hours_end,
                cfg.app.refresh_idle_minutes,
            )
        try:
            yield
        finally:
            if task:
                task.cancel()

    if cfg.server.redact_tokens_in_logs:
        flt = RedactTokensFilter()
        for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
            logging.getLogger(name).addFilter(flt)

    # Without docs_enabled, do not serve /docs, /redoc and /openapi.json: the
    # service is publicly reachable and the docs only describe how to attack it.
    docs = cfg.server.docs_enabled
    app = FastAPI(
        title="Untis → ICS",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        if cfg.server.security_headers:
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("X-Frame-Options", "DENY")
            # Stops the token leaking through the referrer when someone opens
            # a feed URL in a browser.
            response.headers.setdefault("Referrer-Policy", "no-referrer")
            response.headers.setdefault(
                "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
            )
        return response

    @app.get("/health")
    def health():
        return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

    @app.get("/status")
    def status(token: str | None = None):
        """Per-account overview - shows immediately when a feed is stuck.

        Exposes account keys, schools and error text, so it requires a token.
        Without one configured the endpoint stays hidden rather than being
        left open by accident.
        """
        expected = cfg.server.status_secret
        if not expected or not token_matches(expected, token):
            raise HTTPException(404, detail="Not Found")

        ok, problems = healthy()
        out: dict = {
            "healthy": ok,
            "problems": problems,
            "stale_after_minutes": stale_threshold_minutes(cfg),
            "accounts": {},
        }
        for acc in cfg.accounts:
            st = states[acc.key]
            f = out_dir / acc.calendar.file_name
            out["accounts"][acc.key] = {
                "enabled": acc.enabled,
                "school": acc.school,
                "file": str(f),
                "file_exists": f.exists(),
                "file_size": f.stat().st_size if f.exists() else 0,
                "file_modified": (
                    datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat()
                    if f.exists()
                    else None
                ),
                "events": st.event_count,
                "last_success": st.last_success.isoformat() if st.last_success else None,
                "last_error": st.last_error,
                "last_error_at": st.last_error_at.isoformat() if st.last_error_at else None,
            }
        return out

    def _is_stale(out_file) -> bool:
        """Does this request need a fresh fetch?

        With background refresh enabled the server always answers from the
        file, which keeps responses fast and creates no load per request. A
        live fetch only happens when nothing exists yet or the background task
        has clearly stalled.
        """
        if not out_file.exists():
            return True
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(
            out_file.stat().st_mtime, timezone.utc
        )

        if cfg.app.refresh_interval_minutes > 0:
            grace = timedelta(
                minutes=3 * max(cfg.app.refresh_interval_minutes, cfg.app.refresh_idle_minutes)
            )
            return age > grace
        return age > timedelta(seconds=cfg.app.cache_ttl_seconds)

    @app.get("/calendar/{account_key}.ics")
    async def get_calendar(account_key: str, request: Request, token: str | None = None):
        account = next((a for a in cfg.accounts if a.key == account_key), None)

        # An unknown account and a wrong token deliberately return the same
        # response. Different errors would reveal which account keys exist.
        if account is None or (
            account.calendar.web_feed and not token_matches(account.calendar.feed_token, token)
        ):
            if account is None:
                logger.info("Feed request for unknown account '%s'", account_key)
            else:
                logger.warning("Feed request with wrong token for '%s'", account_key)
            raise HTTPException(404, detail="Not Found")

        out_file = out_dir / account.calendar.file_name

        # Never fetch live for disabled accounts: otherwise every request
        # (Google included) triggers a login attempt, which risks locking the
        # account when the password has expired.
        stale = account.enabled and _is_stale(out_file)

        ics_bytes: bytes | None = None
        if stale:
            async with locks[account.key]:
                # Check again: while waiting, another request or the
                # background refresh may already have finished.
                if _is_stale(out_file):
                    ics_bytes = await asyncio.to_thread(refresh_account, account)

        if ics_bytes is None:
            # No fresh fetch possible (or needed) -> serve the last good state
            if not out_file.exists():
                raise HTTPException(
                    503,
                    detail="Calendar could not be generated and no cached "
                    "version is available.",
                )
            ics_bytes = out_file.read_bytes()

        # "private": the feed is token-gated and holds personal data, so
        # shared caches must not retain it.
        headers = {"Cache-Control": f"private, max-age={cfg.app.cache_ttl_seconds}"}
        if cfg.server.etag:
            etag = '"' + hashlib.md5(ics_bytes).hexdigest() + '"'  # nosec - nur ETag
            headers["ETag"] = etag
            if request.headers.get("If-None-Match") == etag:
                return Response(status_code=304, headers=headers)
        if cfg.server.last_modified and out_file.exists():
            mtime = datetime.fromtimestamp(out_file.stat().st_mtime, timezone.utc)
            headers["Last-Modified"] = format_datetime(mtime, usegmt=True)

        headers["Content-Disposition"] = f'inline; filename="{account.calendar.file_name}"'
        return Response(
            content=ics_bytes, media_type="text/calendar; charset=utf-8", headers=headers
        )

    return app
