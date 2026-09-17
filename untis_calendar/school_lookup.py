"""Resolve a school name to its WebUntis server via the official search.

WebUntis regularly moves schools between servers (for example
old-server.webuntis.com -> myschool.webuntis.com). A server hardcoded in the
config then yields HTTP 404 on /WebUntis/jsonrpc.do and the sync collapses
silently, so the responsible host is looked up at runtime when needed.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .retry import with_retry

logger = logging.getLogger(__name__)

SCHOOLSEARCH_URL = "https://schoolsearch.webuntis.com/schoolquery2"

# Results are very long-lived, so cache them in-process for server mode.
_CACHE: dict[str, tuple[float, str | None]] = {}
_CACHE_TTL = 24 * 3600


def search_schools(query: str, timeout: int = 20) -> list[dict[str, Any]]:
    """Raw school search. Returns the list of matches, possibly empty."""
    payload = {
        "id": "school-lookup",
        "method": "searchSchool",
        "params": [{"search": query}],
        "jsonrpc": "2.0",
    }
    resp = with_retry(
        lambda: requests.post(SCHOOLSEARCH_URL, json=payload, timeout=timeout),
        description="school search",
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        # e.g. -6003 "too many results" when the query is too vague
        raise RuntimeError(f"School search failed: {data['error']}")
    return data.get("result", {}).get("schools", []) or []


def resolve_server(school: str, timeout: int = 20) -> str | None:
    """Find the current server host for a school login name.

    Returns None when the school could not be identified unambiguously.
    """
    key = school.lower()
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    server: str | None = None
    try:
        schools = search_schools(school, timeout=timeout)
        # An exact loginName match wins
        for s in schools:
            if str(s.get("loginName", "")).lower() == key:
                server = s.get("server")
                break
        if not server and len(schools) == 1:
            server = schools[0].get("server")
    except Exception as e:
        logger.warning("School search for '%s' failed: %s", school, e)
        return None

    _CACHE[key] = (now, server)
    if server:
        logger.info("School '%s' resolved to server '%s'", school, server)
    else:
        logger.warning("School '%s' could not be resolved", school)
    return server
