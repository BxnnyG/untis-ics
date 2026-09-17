"""Lebenszeichen an einen externen Ueberwachungsdienst.

Bewusst als Dead-Man's-Switch: gepingt wird nur, wenn alle aktiven Accounts
frische Daten haben. Bleibt der Ping aus - weil der Dienst haengt, abgestuerzt
ist oder ein Login nicht mehr geht - schlaegt der externe Dienst Alarm. Damit
braucht dieses Projekt selbst weder Mailversand noch Alarmlogik.

Kompatibel zu Healthchecks.io, Uptime Kuma (Push-Monitor) und allem anderen,
das auf einen HTTP-Aufruf wartet.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)


def send(url: str, ok: bool = True, timeout: int = 10, detail: str | None = None) -> bool:
    """Sendet das Lebenszeichen. Gibt zurueck, ob es angekommen ist.

    Bei ok=False wird "/fail" angehaengt - die Konvention von Healthchecks.io,
    um einen Fehlschlag sofort zu melden, statt auf das Ausbleiben des
    naechsten Pings zu warten. Dienste, die das nicht kennen, ignorieren den
    Pfad oder antworten mit 404; beides ist unkritisch.
    """
    target = url.rstrip("/") + ("" if ok else "/fail")
    try:
        resp = requests.post(target, data=(detail or "")[:2000], timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        # Ein fehlgeschlagenes Lebenszeichen darf den Sync nicht stoeren.
        logger.warning("Heartbeat an %s fehlgeschlagen: %s", target, e)
        return False
    logger.debug("Heartbeat gesendet: %s", target)
    return True
