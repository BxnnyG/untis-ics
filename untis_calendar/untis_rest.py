"""Anreicherung der Stundenplandaten über die neuere WebUntis-REST-API.

Die alte JSON-RPC-Schnittstelle (untis_direct.py) liefert weder Online-
Unterricht noch Stundentexte. Die REST-Ansicht, die auch das Web-Frontend
benutzt, kennt pro Stunde zusaetzlich:

    videoCall        {"videoCallUrl": ..., "active": true}
    lessonText / periodText / periodInfo / substText

Diese Klasse holt genau diese Zusatzinfos und liefert sie als Map
period-id -> Extras. Faellt der Abruf aus, laeuft der Sync ohne Extras
weiter - die Basisdaten kommen weiterhin aus JSON-RPC.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any, Dict, Iterator, List, Optional

import requests

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"')]+")

# Platzhalter, die WebUntis statt einer echten URL liefert
PLACEHOLDER_URLS = {"", "0", "-", "null", "none"}


class LessonExtras:
    __slots__ = ("online", "meeting_url", "texts")

    def __init__(self) -> None:
        self.online: bool = False
        self.meeting_url: Optional[str] = None
        self.texts: List[str] = []

    def __repr__(self) -> str:  # pragma: no cover - nur Debug
        return f"LessonExtras(online={self.online}, url={self.meeting_url!r}, texts={self.texts!r})"


def _clean_url(raw: Any) -> Optional[str]:
    """Gibt nur echte http(s)-Links zurueck, keine Platzhalter wie '0'."""
    if not raw:
        return None
    s = str(raw).strip()
    if s.lower() in PLACEHOLDER_URLS:
        return None
    if not s.startswith(("http://", "https://")):
        return None
    return s


def _find_url_in_text(*texts: Any) -> Optional[str]:
    """Viele Lehrkraefte kleben den Meeting-Link einfach in den Stundentext."""
    for t in texts:
        if not t:
            continue
        m = URL_RE.search(str(t))
        if m:
            return m.group(0).rstrip(".,;)")
    return None


class UntisRestSession:
    def __init__(self, server: str, school: str, username: str, password: str,
                 verify_ssl: bool = True, timeout: int = 25):
        self.server = server if server.startswith("http") else f"https://{server}"
        self.school = school
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.session = requests.Session()

    def login(self) -> "UntisRestSession":
        resp = self.session.post(
            f"{self.server}/WebUntis/j_spring_security_check",
            data={
                "school": self.school,
                "j_username": self.username,
                "j_password": self.password,
                "token": "",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        if "JSESSIONID" not in self.session.cookies:
            raise RuntimeError("REST-Login lieferte keine Session")
        return self

    def logout(self) -> None:
        try:
            self.session.get(f"{self.server}/WebUntis/saml/logout",
                             verify=self.verify_ssl, timeout=self.timeout)
        except Exception as e:
            logger.debug("REST-Logout ignoriert: %s", e)

    def _week_data(self, element_id: int, element_type: int, day: date) -> List[Dict[str, Any]]:
        resp = self.session.get(
            f"{self.server}/WebUntis/api/public/timetable/weekly/data",
            params={
                "elementType": element_type,
                "elementId": element_id,
                "date": day.isoformat(),
                "formatId": 1,
            },
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        try:
            periods = data["data"]["result"]["data"]["elementPeriods"][str(element_id)]
        except (KeyError, TypeError):
            return []
        return periods if isinstance(periods, list) else []

    def fetch_extras(self, element_id: int, element_type: int,
                     start: date, end: date) -> Dict[str, LessonExtras]:
        """Extras je Perioden-ID fuer den Zeitraum."""
        out: Dict[str, LessonExtras] = {}
        for monday in _mondays(start, end):
            try:
                periods = self._week_data(element_id, element_type, monday)
            except Exception as e:
                logger.warning("REST-Woche %s nicht abrufbar: %s", monday, e)
                continue

            for p in periods:
                pid = str(p.get("id") or "")
                if not pid:
                    continue
                ex = out.setdefault(pid, LessonExtras())

                vc = p.get("videoCall") or {}
                if isinstance(vc, dict):
                    if vc.get("active"):
                        ex.online = True
                    url = _clean_url(vc.get("videoCallUrl"))
                    if url:
                        ex.meeting_url = url

                texts = [p.get(k) for k in
                         ("lessonText", "periodText", "periodInfo", "substText")]
                ex.texts = [str(t).strip() for t in texts if t and str(t).strip()]

                if not ex.meeting_url:
                    found = _find_url_in_text(*texts)
                    if found:
                        ex.meeting_url = found
                        ex.online = True
        return out


def _mondays(start: date, end: date) -> Iterator[date]:
    """Jeden Wochenanfang im Zeitraum - die REST-Ansicht arbeitet wochenweise."""
    cur = start - timedelta(days=start.weekday())
    while cur <= end:
        yield cur
        cur += timedelta(days=7)


def fetch_lesson_extras(server: str, school: str, username: str, password: str,
                        element_id: int, element_type: int,
                        start: date, end: date,
                        verify_ssl: bool = True) -> Dict[str, LessonExtras]:
    """Bequemer Einstieg. Wirft nicht - im Fehlerfall kommt eine leere Map."""
    sess = UntisRestSession(server, school, username, password, verify_ssl)
    try:
        sess.login()
        return sess.fetch_extras(element_id, element_type, start, end)
    except Exception as e:
        logger.warning("REST-Anreicherung nicht verfuegbar: %s", e)
        return {}
    finally:
        sess.logout()
