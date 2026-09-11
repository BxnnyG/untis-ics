from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Response

from .config import AccountConfig, Config
from .untis_client import UntisClient
from .ics import events_to_ics
from .logging_config import setup_logging

logger = logging.getLogger(__name__)


class FeedState:
    """Merkt sich pro Account den letzten Erfolg/Fehler für /status."""

    def __init__(self) -> None:
        self.last_success: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.last_error_at: Optional[datetime] = None
        self.event_count: int = 0


def create_app(config_path: str) -> FastAPI:
    setup_logging()
    cfg = Config.load(config_path)
    client = UntisClient(cfg.app)
    out_dir = Path(cfg.app.output_dir)
    states: Dict[str, FeedState] = {a.key: FeedState() for a in cfg.accounts}

    def refresh_account(account: AccountConfig) -> Optional[bytes]:
        """Holt frische Daten und schreibt sie.

        Gibt None zurück, wenn der Abruf fehlschlug - die vorhandene Datei
        bleibt dann unangetastet.
        """
        state = states[account.key]
        out_file = out_dir / account.calendar.file_name
        try:
            events = client.fetch_events(account)
        except Exception as e:
            state.last_error = str(e)
            state.last_error_at = datetime.now(timezone.utc)
            logger.error("Abruf für '%s' fehlgeschlagen: %s", account.key, e)
            logger.debug("Details:", exc_info=True)
            return None

        if not events and out_file.exists() and out_file.stat().st_size > 200:
            # Verdächtig: vorher gab es Daten, jetzt nichts. Nicht überschreiben.
            state.last_error = "Leeres Ergebnis - vorhandene Datei behalten"
            state.last_error_at = datetime.now(timezone.utc)
            logger.warning("Leeres Ergebnis für '%s' - behalte %s", account.key, out_file)
            return None

        ics_bytes = events_to_ics(
            events,
            calendar_name=account.calendar.display_name or f"Stundenplan {account.key}",
            refresh_minutes=cfg.app.refresh_interval_minutes or 60,
            subject_style=cfg.app.subject_style,
        )
        out_file.write_bytes(ics_bytes)
        state.last_success = datetime.now(timezone.utc)
        state.event_count = len(events)
        state.last_error = None
        logger.info("Aktualisiert: %s (%d Termine)", out_file, len(events))
        return ics_bytes

    async def refresh_loop() -> None:
        interval = cfg.app.refresh_interval_minutes
        while True:
            for account in cfg.active_accounts:
                try:
                    await asyncio.to_thread(refresh_account, account)
                except Exception:
                    logger.exception("Unerwarteter Fehler im Refresh von '%s'", account.key)
            await asyncio.sleep(interval * 60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if cfg.app.refresh_interval_minutes > 0:
            task = asyncio.create_task(refresh_loop())
            logger.info("Hintergrund-Refresh alle %d Minuten aktiv",
                        cfg.app.refresh_interval_minutes)
        try:
            yield
        finally:
            if task:
                task.cancel()

    app = FastAPI(title="Untis → ICS", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

    @app.get("/status")
    def status():
        """Übersicht pro Account - zeigt sofort, wenn ein Feed klemmt."""
        out = {}
        for acc in cfg.accounts:
            st = states[acc.key]
            f = out_dir / acc.calendar.file_name
            out[acc.key] = {
                "enabled": acc.enabled,
                "school": acc.school,
                "file": str(f),
                "file_exists": f.exists(),
                "file_size": f.stat().st_size if f.exists() else 0,
                "file_modified": (
                    datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat()
                    if f.exists() else None
                ),
                "events": st.event_count,
                "last_success": st.last_success.isoformat() if st.last_success else None,
                "last_error": st.last_error,
                "last_error_at": st.last_error_at.isoformat() if st.last_error_at else None,
            }
        return out

    @app.get("/calendar/{account_key}.ics")
    async def get_calendar(account_key: str, request: Request, token: Optional[str] = None):
        account = next((a for a in cfg.accounts if a.key == account_key), None)
        if not account:
            raise HTTPException(404, detail="Account nicht gefunden")
        if account.calendar.web_feed:
            expected = account.calendar.feed_token
            if expected and token != expected:
                raise HTTPException(401, detail="Ungültiger Token")

        out_file = out_dir / account.calendar.file_name

        # Deaktivierte Accounts nie live abrufen: sonst loest jeder Abruf
        # (z. B. von Google) einen Login-Versuch aus - bei abgelaufenem
        # Passwort ein Sperr-Risiko.
        stale = account.enabled
        if out_file.exists() and account.enabled:
            mtime = datetime.fromtimestamp(out_file.stat().st_mtime, timezone.utc)
            if datetime.now(timezone.utc) - mtime < timedelta(seconds=cfg.app.cache_ttl_seconds):
                stale = False

        ics_bytes: Optional[bytes] = None
        if stale:
            ics_bytes = await asyncio.to_thread(refresh_account, account)

        if ics_bytes is None:
            # Frischer Abruf nicht möglich (oder nicht nötig) -> letzten guten Stand liefern
            if not out_file.exists():
                raise HTTPException(
                    503, detail="Kalender konnte nicht erzeugt werden und es liegt "
                                "kein zwischengespeicherter Stand vor."
                )
            ics_bytes = out_file.read_bytes()

        headers = {"Cache-Control": f"public, max-age={cfg.app.cache_ttl_seconds}"}
        if cfg.server.etag:
            etag = '"' + hashlib.md5(ics_bytes).hexdigest() + '"'  # nosec - nur ETag
            headers["ETag"] = etag
            if request.headers.get("If-None-Match") == etag:
                return Response(status_code=304, headers=headers)
        if cfg.server.last_modified and out_file.exists():
            mtime = datetime.fromtimestamp(out_file.stat().st_mtime, timezone.utc)
            headers["Last-Modified"] = format_datetime(mtime, usegmt=True)

        headers["Content-Disposition"] = f'inline; filename="{account.calendar.file_name}"'
        return Response(content=ics_bytes, media_type="text/calendar; charset=utf-8",
                        headers=headers)

    return app
