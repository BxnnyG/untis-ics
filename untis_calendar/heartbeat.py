"""Liveness ping to an external monitor.

Deliberately a dead man's switch: the ping only goes out when every enabled
account has fresh data. If it stops - because the service hangs, crashed, or a
login no longer works - the external monitor raises the alarm. That way this
project needs neither mail delivery nor alerting logic of its own.

Works with Healthchecks.io, Uptime Kuma push monitors, and anything else that
just waits for an HTTP call.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)


def send(url: str, ok: bool = True, timeout: int = 10, detail: str | None = None) -> bool:
    """Send the ping. Returns whether it was accepted.

    With ok=False, "/fail" is appended - the Healthchecks.io convention for
    reporting a failure immediately instead of waiting for the next ping to go
    missing. Services that do not know it either ignore the path or answer
    404; neither matters.
    """
    target = url.rstrip("/") + ("" if ok else "/fail")
    try:
        resp = requests.post(target, data=(detail or "")[:2000], timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        # A failed ping must never disturb the sync itself.
        logger.warning("Heartbeat to %s failed: %s", target, e)
        return False
    logger.debug("Heartbeat sent: %s", target)
    return True
