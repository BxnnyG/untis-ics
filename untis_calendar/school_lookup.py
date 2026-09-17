"""Auflösung von Schulname -> WebUntis-Server über die offizielle Schulsuche.

Hintergrund: WebUntis verschiebt Schulen regelmäßig zwischen Servern
(z. B. alt-server.webuntis.com -> musterschule.webuntis.com). Ein fest in der
Config verdrahteter Server führt dann zu HTTP 404 auf /WebUntis/jsonrpc.do
und der Sync bricht still zusammen. Darum fragen wir den zuständigen Server
bei Bedarf zur Laufzeit ab.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

SCHOOLSEARCH_URL = "https://schoolsearch.webuntis.com/schoolquery2"

# Ergebnisse sind sehr langlebig -> im Prozess cachen (Server-Betrieb)
_CACHE: dict[str, tuple[float, str | None]] = {}
_CACHE_TTL = 24 * 3600


def search_schools(query: str, timeout: int = 20) -> list[dict[str, Any]]:
    """Rohe Schulsuche. Gibt die Liste der Treffer zurück (evtl. leer)."""
    payload = {
        "id": "school-lookup",
        "method": "searchSchool",
        "params": [{"search": query}],
        "jsonrpc": "2.0",
    }
    resp = requests.post(SCHOOLSEARCH_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        # z. B. -6003 "too many results" bei zu unspezifischer Suche
        raise RuntimeError(f"Schulsuche fehlgeschlagen: {data['error']}")
    return data.get("result", {}).get("schools", []) or []


def resolve_server(school: str, timeout: int = 20) -> str | None:
    """Ermittelt den aktuellen Server-Host für einen Schul-Loginnamen.

    Gibt None zurück, wenn die Schule nicht eindeutig gefunden wurde.
    """
    key = school.lower()
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    server: str | None = None
    try:
        schools = search_schools(school, timeout=timeout)
        # Exakter Treffer auf loginName hat Vorrang
        for s in schools:
            if str(s.get("loginName", "")).lower() == key:
                server = s.get("server")
                break
        if not server and len(schools) == 1:
            server = schools[0].get("server")
    except Exception as e:
        logger.warning("Schulsuche für '%s' fehlgeschlagen: %s", school, e)
        return None

    _CACHE[key] = (now, server)
    if server:
        logger.info("Schule '%s' aufgelöst auf Server '%s'", school, server)
    else:
        logger.warning("Schule '%s' konnte nicht aufgelöst werden", school)
    return server
