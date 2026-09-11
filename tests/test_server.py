"""Tests für das Feed-Verhalten des Servers."""
import textwrap

import pytest
from fastapi.testclient import TestClient

from untis_calendar.server import create_app

CONFIG = """
app:
  output_dir: "{out}"
  cache_ttl_seconds: 0
  refresh_interval_minutes: 0
accounts:
  - key: "aus"
    enabled: false
    school: "musterschule"
    server: "musterschule.webuntis.com"
    username: "u"
    password: "p"
    calendar:
      file_name: "aus.ics"
      web_feed: true
      token: "geheim"
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(textwrap.dedent(CONFIG).format(out=out))

    # Jeder echte Abruf waere hier ein Fehler -> hart abbrechen
    def boom(*a, **kw):
        raise AssertionError("Es wurde ein Untis-Login versucht")

    monkeypatch.setattr("untis_calendar.server.UntisClient.fetch_events", boom)
    with TestClient(create_app(str(cfg))) as c:
        c.out = out
        yield c


def test_disabled_account_never_triggers_login(client):
    """Sperr-Risiko: ein deaktivierter Account darf durch einen Feed-Abruf
    keinen Login ausloesen, auch wenn die Datei veraltet ist."""
    (client.out / "aus.ics").write_bytes(b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
    r = client.get("/calendar/aus.ics", params={"token": "geheim"})
    assert r.status_code == 200
    assert r.content.startswith(b"BEGIN:VCALENDAR")


def test_wrong_token_rejected(client):
    assert client.get("/calendar/aus.ics", params={"token": "falsch"}).status_code == 401


def test_missing_token_rejected(client):
    assert client.get("/calendar/aus.ics").status_code == 401


def test_unknown_account_404(client):
    assert client.get("/calendar/gibtsnicht.ics", params={"token": "geheim"}).status_code == 404


def test_disabled_without_cache_returns_503(client):
    """Kein Login, aber auch keine Daten -> ehrlicher Fehler statt leerem Kalender."""
    assert client.get("/calendar/aus.ics", params={"token": "geheim"}).status_code == 503


def test_status_lists_accounts(client):
    body = client.get("/status").json()
    assert body["aus"]["enabled"] is False
