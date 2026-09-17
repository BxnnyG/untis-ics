"""Secrets kommen aus der Umgebung, nicht aus der config.yaml."""

import textwrap

import pytest

from untis_calendar.config import Config

CONFIG = """
app:
  output_dir: "{out}"
accounts:
  - key: "a"
    school: "musterschule"
    username: "u"
    password_env: "TEST_UNTIS_PASS"
    calendar:
      file_name: "a.ics"
      token_env: "TEST_FEED_TOKEN"
"""


@pytest.fixture
def cfg_path(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(CONFIG).format(out=out))
    return p


def test_password_and_token_come_from_env(cfg_path, monkeypatch):
    monkeypatch.setenv("TEST_UNTIS_PASS", "geheim")
    monkeypatch.setenv("TEST_FEED_TOKEN", "tok123")
    cfg = Config.load(cfg_path)
    acc = cfg.accounts[0]
    assert acc.get_password() == "geheim"
    assert acc.calendar.feed_token == "tok123"


def test_missing_env_gives_clear_error(cfg_path, monkeypatch):
    monkeypatch.delenv("TEST_UNTIS_PASS", raising=False)
    cfg = Config.load(cfg_path)
    with pytest.raises(ValueError, match="TEST_UNTIS_PASS"):
        cfg.accounts[0].get_password()


def test_existing_env_wins_over_dotenv(cfg_path, monkeypatch):
    """systemd setzt die Variable bereits - eine .env darf sie nicht kippen."""
    (cfg_path.parent / ".env").write_text("TEST_UNTIS_PASS=aus_datei\n")
    monkeypatch.setenv("TEST_UNTIS_PASS", "aus_systemd")
    cfg = Config.load(cfg_path)
    assert cfg.accounts[0].get_password() == "aus_systemd"


def test_dotenv_used_when_env_unset(cfg_path, monkeypatch):
    monkeypatch.delenv("TEST_UNTIS_PASS", raising=False)
    (cfg_path.parent / ".env").write_text("TEST_UNTIS_PASS=aus_datei\n")
    cfg = Config.load(cfg_path)
    assert cfg.accounts[0].get_password() == "aus_datei"
