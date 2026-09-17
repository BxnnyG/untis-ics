"""Tests für das Feed-Verhalten und die Absicherung des Servers."""

import logging
import textwrap

import pytest
from fastapi.testclient import TestClient

from untis_calendar.server import RedactTokensFilter, create_app, token_matches

CONFIG = """
app:
  output_dir: "{out}"
  cache_ttl_seconds: 0
  refresh_interval_minutes: 0
server:
  status_token: "{status_token}"
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


def _client(tmp_path, monkeypatch, status_token="statusgeheim"):
    out = tmp_path / "out"
    out.mkdir()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(textwrap.dedent(CONFIG).format(out=out, status_token=status_token))

    # Jeder echte Abruf waere hier ein Fehler -> hart abbrechen
    def boom(*a, **kw):
        raise AssertionError("Es wurde ein Untis-Login versucht")

    monkeypatch.setattr("untis_calendar.server.UntisClient.fetch_events", boom)
    c = TestClient(create_app(str(cfg)))
    c.out = out
    return c


@pytest.fixture
def client(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        yield c


def test_disabled_account_never_triggers_login(client):
    """Sperr-Risiko: ein deaktivierter Account darf durch einen Feed-Abruf
    keinen Login ausloesen, auch wenn die Datei veraltet ist."""
    (client.out / "aus.ics").write_bytes(b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
    r = client.get("/calendar/aus.ics", params={"token": "geheim"})
    assert r.status_code == 200
    assert r.content.startswith(b"BEGIN:VCALENDAR")


def test_disabled_without_cache_returns_503(client):
    """Kein Login, aber auch keine Daten -> ehrlicher Fehler statt leerem Kalender."""
    assert client.get("/calendar/aus.ics", params={"token": "geheim"}).status_code == 503


# --- Absicherung ------------------------------------------------------------


def test_wrong_token_is_indistinguishable_from_unknown_account(client):
    """Unterschiedliche Fehler wuerden verraten, welche Account-Keys existieren."""
    falsch = client.get("/calendar/aus.ics", params={"token": "falsch"})
    unbekannt = client.get("/calendar/gibtsnicht.ics", params={"token": "falsch"})
    assert falsch.status_code == unbekannt.status_code == 404
    assert falsch.json() == unbekannt.json()


def test_missing_token_rejected(client):
    assert client.get("/calendar/aus.ics").status_code == 404


def test_status_requires_token(client):
    assert client.get("/status").status_code == 404
    assert client.get("/status", params={"token": "falsch"}).status_code == 404
    assert client.get("/status", params={"token": "statusgeheim"}).status_code == 200


def test_status_without_configured_token_stays_hidden(tmp_path, monkeypatch):
    """Fail-closed: ohne konfigurierten Token bleibt /status verborgen,
    statt versehentlich offen zu stehen."""
    with _client(tmp_path, monkeypatch, status_token="") as c:
        assert c.get("/status").status_code == 404
        assert c.get("/status", params={"token": ""}).status_code == 404


def test_status_content_when_authorised(client):
    body = client.get("/status", params={"token": "statusgeheim"}).json()
    assert body["aus"]["enabled"] is False


def test_health_stays_public(client):
    """Fuer Monitoring, verraet nichts ueber Accounts."""
    r = client.get("/health")
    assert r.status_code == 200
    assert set(r.json()) == {"status", "time"}


def test_api_docs_disabled_by_default(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path


def test_security_headers_present(client):
    h = client.get("/health").headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"]


def test_feed_is_not_publicly_cacheable(client):
    """Tokengeschuetzte Personendaten duerfen nicht in geteilten Caches landen."""
    (client.out / "aus.ics").write_bytes(b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
    cc = client.get("/calendar/aus.ics", params={"token": "geheim"}).headers["Cache-Control"]
    assert cc.startswith("private")
    assert "public" not in cc


# --- Token-Vergleich und Log-Redaction --------------------------------------


def test_token_matches_is_exact():
    assert token_matches("abc", "abc") is True
    assert token_matches("abc", "abcd") is False
    assert token_matches("abc", "") is False
    assert token_matches("abc", None) is False


def test_no_configured_token_means_open_feed():
    assert token_matches(None, None) is True


def test_log_filter_redacts_token():
    """Der Token steht zwangslaeufig im Query-String - er darf nicht ins Journal."""
    rec = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s"',
        ("1.2.3.4", "GET /calendar/a.ics?token=SUPERGEHEIM HTTP/1.1"),
        None,
    )
    RedactTokensFilter().filter(rec)
    rendered = rec.getMessage()
    assert "SUPERGEHEIM" not in rendered
    assert "token=***" in rendered


def test_log_filter_leaves_other_args_alone():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s %d", ("x", 200), None)
    RedactTokensFilter().filter(rec)
    assert rec.getMessage() == "x 200"
