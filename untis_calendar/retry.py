"""Wiederholung bei voruebergehenden Netzwerkfehlern."""

from __future__ import annotations

import logging
import random
import time
from typing import Callable, TypeVar

import requests

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Nur diese Statuscodes sind es wert, wiederholt zu werden. Alles andere
# (401, 403, 404) aendert sich beim zweiten Versuch nicht.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

TRANSIENT_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def is_transient(exc: BaseException) -> bool:
    """Lohnt sich ein zweiter Versuch?

    Ein abgelehnter Login wird bewusst NICHT als voruebergehend gewertet -
    wiederholte Fehlversuche sperren bei WebUntis das Konto.
    """
    if isinstance(exc, TRANSIENT_EXCEPTIONS):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        resp = getattr(exc, "response", None)
        return resp is not None and resp.status_code in RETRY_STATUS
    return False


def with_retry(
    func: Callable[[], T],
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 20.0,
    retry_on: Callable[[BaseException], bool] = is_transient,
    description: str = "Aufruf",
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> T:
    """Fuehrt func aus und wiederholt bei voruebergehenden Fehlern.

    Wartezeit verdoppelt sich je Versuch, mit etwas Zufall obendrauf, damit
    mehrere Accounts nicht im Gleichtakt erneut anklopfen.
    """
    last: BaseException | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return func()
        except BaseException as exc:
            if not retry_on(exc) or attempt >= attempts:
                raise
            last = exc
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            delay += jitter() * base_delay
            logger.warning(
                "%s fehlgeschlagen (Versuch %d/%d): %s - erneut in %.1fs",
                description,
                attempt,
                attempts,
                exc,
                delay,
            )
            sleep(delay)
    raise last if last else RuntimeError("with_retry ohne Versuch verlassen")
