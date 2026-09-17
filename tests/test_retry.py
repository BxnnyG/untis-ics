"""Retrying network failures - and where it must not happen."""

from __future__ import annotations

import pytest
import requests

from untis_calendar.retry import is_transient, with_retry


def _http_error(status: int) -> requests.exceptions.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.exceptions.HTTPError(f"{status}", response=resp)


class Counter:
    """Fails the first n calls, then succeeds."""

    def __init__(self, fails: int, exc: BaseException):
        self.fails = fails
        self.exc = exc
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.fails:
            raise self.exc
        return "ok"


def test_succeeds_without_retry():
    c = Counter(0, RuntimeError())
    assert with_retry(c, sleep=lambda _: None) == "ok"
    assert c.calls == 1


def test_retries_connection_error():
    c = Counter(2, requests.exceptions.ConnectionError())
    assert with_retry(c, sleep=lambda _: None) == "ok"
    assert c.calls == 3


def test_gives_up_after_attempts():
    c = Counter(99, requests.exceptions.Timeout())
    with pytest.raises(requests.exceptions.Timeout):
        with_retry(c, attempts=3, sleep=lambda _: None)
    assert c.calls == 3


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_server_errors_are_retried(status):
    c = Counter(1, _http_error(status))
    assert with_retry(c, sleep=lambda _: None) == "ok"
    assert c.calls == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried(status):
    """A rejected login must NOT be retried - repeated failed attempts lock the
    WebUntis account."""
    c = Counter(99, _http_error(status))
    with pytest.raises(requests.exceptions.HTTPError):
        with_retry(c, sleep=lambda _: None)
    assert c.calls == 1


def test_unknown_exceptions_are_not_retried():
    c = Counter(99, ValueError("kaputt"))
    with pytest.raises(ValueError):
        with_retry(c, sleep=lambda _: None)
    assert c.calls == 1


def test_auth_error_is_not_transient():
    from untis_calendar.untis_direct import UntisAuthError

    assert is_transient(UntisAuthError("bad credentials")) is False


def test_backoff_grows_and_is_capped():
    delays: list[float] = []
    c = Counter(99, requests.exceptions.Timeout())
    with pytest.raises(requests.exceptions.Timeout):
        with_retry(
            c,
            attempts=5,
            base_delay=1.0,
            max_delay=4.0,
            sleep=delays.append,
            jitter=lambda: 0.0,
        )
    assert delays == [1.0, 2.0, 4.0, 4.0]


def test_jitter_is_added():
    delays: list[float] = []
    c = Counter(1, requests.exceptions.Timeout())
    with_retry(c, base_delay=2.0, sleep=delays.append, jitter=lambda: 0.5)
    assert delays == [3.0]
