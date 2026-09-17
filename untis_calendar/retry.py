"""Retrying transient network failures."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

import requests

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Only these status codes are worth repeating. Anything else (401, 403, 404)
# will not change on a second attempt.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

TRANSIENT_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def is_transient(exc: BaseException) -> bool:
    """Is a second attempt worth it?

    A rejected login is deliberately NOT treated as transient - repeated failed
    attempts get WebUntis accounts locked.
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
    description: str = "call",
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> T:
    """Run func, retrying transient failures.

    The delay doubles per attempt, with a little randomness on top so several
    accounts do not come knocking again in lockstep.
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
                "%s failed (attempt %d/%d): %s - retrying in %.1fs",
                description,
                attempt,
                attempts,
                exc,
                delay,
            )
            sleep(delay)
    raise last if last else RuntimeError("with_retry exited without an attempt")
