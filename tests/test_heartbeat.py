"""Lebenszeichen an einen externen Ueberwachungsdienst."""

from __future__ import annotations

import pytest
import requests

from untis_calendar import heartbeat
from untis_calendar.config import Config
from untis_calendar.server import stale_threshold_minutes


class FakePost:
    def __init__(self, exc: Exception | None = None, status: int = 200):
        self.calls: list[tuple[str, str]] = []
        self.exc = exc
        self.status = status

    def __call__(self, url, data=None, timeout=None):
        self.calls.append((url, data))
        if self.exc:
            raise self.exc
        resp = requests.Response()
        resp.status_code = self.status
        return resp


def test_success_pings_plain_url(monkeypatch):
    post = FakePost()
    monkeypatch.setattr(heartbeat.requests, "post", post)
    assert heartbeat.send("https://hc.example/abc", ok=True) is True
    assert post.calls[0][0] == "https://hc.example/abc"


def test_failure_appends_fail_path(monkeypatch):
    """Healthchecks.io-Konvention: /fail meldet den Fehler sofort, statt auf
    das Ausbleiben des naechsten Pings zu warten."""
    post = FakePost()
    monkeypatch.setattr(heartbeat.requests, "post", post)
    heartbeat.send("https://hc.example/abc", ok=False)
    assert post.calls[0][0] == "https://hc.example/abc/fail"


def test_trailing_slash_does_not_double_up(monkeypatch):
    post = FakePost()
    monkeypatch.setattr(heartbeat.requests, "post", post)
    heartbeat.send("https://hc.example/abc/", ok=False)
    assert post.calls[0][0] == "https://hc.example/abc/fail"


def test_network_error_never_raises(monkeypatch):
    """Ein fehlgeschlagenes Lebenszeichen darf den Sync nicht stoeren."""
    monkeypatch.setattr(
        heartbeat.requests, "post", FakePost(exc=requests.exceptions.ConnectionError())
    )
    assert heartbeat.send("https://hc.example/abc") is False


def test_detail_is_truncated(monkeypatch):
    post = FakePost()
    monkeypatch.setattr(heartbeat.requests, "post", post)
    heartbeat.send("https://hc.example/abc", detail="x" * 5000)
    assert len(post.calls[0][1]) == 2000


# --- Schwelle fuer "veraltet" -----------------------------------------------


def _cfg(tmp_path, **app):

    import yaml

    out = tmp_path / "out"
    out.mkdir()
    data = {
        "app": {"output_dir": str(out), **app},
        "accounts": [
            {
                "key": "a",
                "school": "s",
                "username": "u",
                "password": "p",
                "calendar": {"file_name": "a.ics"},
            }
        ],
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data))
    return Config.load(p)


def test_threshold_defaults_to_three_intervals(tmp_path):
    cfg = _cfg(tmp_path, refresh_interval_minutes=15, refresh_idle_minutes=120)
    assert stale_threshold_minutes(cfg) == 360


def test_threshold_has_a_floor(tmp_path):
    """Bei sehr kurzen Intervallen soll nicht bei jedem Aussetzer Alarm sein."""
    cfg = _cfg(tmp_path, refresh_interval_minutes=1, refresh_idle_minutes=1)
    assert stale_threshold_minutes(cfg) == 30


def test_explicit_threshold_wins(tmp_path):
    cfg = _cfg(tmp_path, refresh_interval_minutes=15)
    cfg.server.stale_after_minutes = 45
    assert stale_threshold_minutes(cfg) == 45


@pytest.mark.parametrize("value,expected", [(None, None), ("", None)])
def test_no_heartbeat_configured(tmp_path, value, expected):
    cfg = _cfg(tmp_path)
    cfg.server.heartbeat_url = value
    assert cfg.server.heartbeat == expected
