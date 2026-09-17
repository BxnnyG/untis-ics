"""Direct WebUntis JSON-RPC client."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

import requests

from .retry import with_retry
from .school_lookup import resolve_server

logger = logging.getLogger(__name__)


class UntisError(RuntimeError):
    """Base error for WebUntis problems."""


class UntisAuthError(UntisError):
    """Login rejected (wrong user/password, or locked)."""


class UntisEndpointError(UntisError):
    """The JSON-RPC endpoint does not exist here (wrong server or school)."""


# WebUntis error codes
ERR_BAD_CREDENTIALS = -8504
ERR_NOT_AUTHENTICATED = -8520


class DirectUntisSession:
    """Direct JSON-RPC implementation, without the webuntis library."""

    def __init__(
        self,
        server: str,
        school: str,
        username: str,
        password: str,
        verify_ssl: bool = True,
        auto_resolve: bool = True,
    ):
        self.school = school
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.auto_resolve = auto_resolve
        self.session = requests.Session()
        self.session_id: str | None = None
        self.person_type: int | None = None
        self.person_id: int | None = None
        self.klasse_id: int | None = None
        self.request_id = 0
        self.server = self._normalize(server)

        if not verify_ssl:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    @staticmethod
    def _normalize(server: str) -> str:
        return server if server.startswith("http") else f"https://{server}"

    @property
    def url(self) -> str:
        return f"{self.server}/WebUntis/jsonrpc.do?school={self.school}"

    def _rpc_request(self, method: str, params: Any) -> Any:
        self.request_id += 1
        payload = {
            "id": str(self.request_id),
            "method": method,
            "params": params,
            "jsonrpc": "2.0",
        }
        headers = {"Content-Type": "application/json"}
        if self.session_id:
            headers["Cookie"] = f"JSESSIONID={self.session_id}"

        logger.debug("JSON-RPC Request: %s", method)

        def post():
            r = self.session.post(
                self.url, json=payload, headers=headers, verify=self.verify_ssl, timeout=30
            )
            # Surface 5xx/429 to with_retry; 4xx is left alone so a rejected
            # login is never retried.
            if r.status_code in (429, 500, 502, 503, 504):
                r.raise_for_status()
            return r

        resp = with_retry(post, description=f"JSON-RPC {method}")

        if resp.status_code == 404:
            raise UntisEndpointError(
                f"JSON-RPC endpoint not found: {self.url} "
                f"(server or school name is no longer correct)"
            )
        resp.raise_for_status()

        try:
            data = resp.json()
        except ValueError as e:
            raise UntisError(f"Invalid response from {self.url}: {resp.text[:200]}") from e

        if "error" in data:
            err = data["error"] or {}
            code = err.get("code")
            msg = err.get("message", "unknown")
            if code == ERR_BAD_CREDENTIALS:
                raise UntisAuthError(
                    f"Login rejected for '{self.username}' at school '{self.school}': {msg}"
                )
            raise UntisError(f"WebUntis API Error ({code}): {msg}")

        if not self.session_id and "JSESSIONID" in resp.cookies:
            self.session_id = resp.cookies["JSESSIONID"]

        return data.get("result")

    def login(self) -> DirectUntisSession:
        try:
            result = self._rpc_request(
                "authenticate",
                {
                    "user": self.username,
                    "password": self.password,
                    "client": "untis-calendar",
                },
            )
        except UntisEndpointError:
            # The school has probably moved servers -> resolve once and retry
            if not self.auto_resolve:
                raise
            new_server = resolve_server(self.school)
            if not new_server or self._normalize(new_server) == self.server:
                raise
            logger.warning(
                "Server for '%s' moved: %s -> %s", self.school, self.server, new_server
            )
            self.server = self._normalize(new_server)
            self.auto_resolve = False  # one retry only
            result = self._rpc_request(
                "authenticate",
                {
                    "user": self.username,
                    "password": self.password,
                    "client": "untis-calendar",
                },
            )

        if not isinstance(result, dict) or not result.get("sessionId"):
            raise UntisAuthError(f"Login for '{self.username}' returned no session: {result}")

        self.session_id = result.get("sessionId")
        self.person_type = result.get("personType")
        self.person_id = result.get("personId")
        self.klasse_id = result.get("klasseId")
        logger.info(
            "Login successful: %s @ %s (type: %s, id: %s)",
            self.username,
            self.school,
            self.person_type,
            self.person_id,
        )
        return self

    def logout(self) -> None:
        if self.session_id:
            try:
                self._rpc_request("logout", {})
            except Exception as e:
                logger.debug("Logout ignored: %s", e)
            finally:
                self.session_id = None

    def timetable(self, start: date, end: date, element: dict[str, Any] | None = None) -> list:
        """Fetch the timetable."""
        options: dict[str, Any] = {
            "startDate": start.strftime("%Y%m%d"),
            "endDate": end.strftime("%Y%m%d"),
            "showInfo": True,
            "showSubstText": True,
            "showLsText": True,
            "showStudentgroup": True,
            "klasseFields": ["id", "name", "longname"],
            "roomFields": ["id", "name", "longname"],
            "subjectFields": ["id", "name", "longname"],
            "teacherFields": ["id", "name", "longname"],
        }

        if element:
            options["element"] = element
        elif self.person_id and self.person_type:
            options["element"] = {"id": self.person_id, "type": self.person_type}
        else:
            raise UntisError(
                "Cannot determine which element to fetch the timetable for "
                "(neither configured nor derivable from the login)."
            )

        result = self._rpc_request("getTimetable", {"options": options})
        return result if isinstance(result, list) else []

    def timegrid(self) -> dict[int, str]:
        """Timegrid: start time (HHMM) -> period name ("1", "2", ...).

        Untis returns the grid per weekday. The times are usually identical;
        where they differ, the first day defining a time wins - good enough
        for a label.
        """
        try:
            days = self._rpc_request("getTimegridUnits", {})
        except UntisError as e:
            logger.warning("Timegrid not available: %s", e)
            return {}

        grid: dict[int, str] = {}
        for day in days or []:
            for unit in day.get("timeUnits") or []:
                start = unit.get("startTime")
                name = unit.get("name")
                if start is not None and name and int(start) not in grid:
                    grid[int(start)] = str(name)
        return grid


@contextmanager
def direct_untis_login(
    server: str, school: str, username: str, password: str, verify_ssl: bool = True
) -> Iterator[DirectUntisSession]:
    """Context manager for a direct WebUntis session."""
    session = DirectUntisSession(server, school, username, password, verify_ssl)
    try:
        session.login()
        yield session
    finally:
        session.logout()
