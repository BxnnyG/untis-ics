"""Accounts from the environment - the bare docker-compose.yml setup."""

import os
import textwrap

import pytest
from fastapi.testclient import TestClient

from untis_calendar.__main__ import main
from untis_calendar.config import Config
from untis_calendar.server import create_app

TOKEN = "a" * 24


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    """No stray UNTIS_* from the machine running the tests, and no config.yaml."""
    for name in list(os.environ):
        if name.startswith("UNTIS_") or name in ("STATUS_TOKEN", "HEARTBEAT_URL"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("UNTIS_APP_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.chdir(tmp_path)


def _account(monkeypatch, sfx="", school="musterschule", user="Anna"):
    if school:
        monkeypatch.setenv(f"UNTIS_SCHOOL{sfx}", school)
    monkeypatch.setenv(f"UNTIS_USERNAME{sfx}", user)
    monkeypatch.setenv(f"UNTIS_PASSWORD{sfx}", f"pw-{user}")
    monkeypatch.setenv(f"UNTIS_FEED_TOKEN{sfx}", f"{user}-{TOKEN}")


def test_single_account(tmp_path, monkeypatch):
    _account(monkeypatch)
    cfg = Config.load(tmp_path / "config.yaml")

    [acc] = cfg.accounts
    assert acc.key == "timetable"
    assert acc.school == "musterschule"
    assert acc.username == "Anna"
    assert acc.get_password() == "pw-Anna"
    assert acc.calendar.feed_token == f"Anna-{TOKEN}"
    assert acc.calendar.file_name == "timetable.ics"
    assert acc.calendar.display_name == "Stundenplan Anna"
    assert cfg.app.output_dir == str(tmp_path / "out")


def test_secrets_are_referenced_not_copied(tmp_path, monkeypatch):
    """Same as config.yaml: the model holds the variable name, not the value."""
    _account(monkeypatch)
    [acc] = Config.load(tmp_path / "config.yaml").accounts
    assert acc.password is None
    assert acc.password_env == "UNTIS_PASSWORD"
    assert acc.calendar.token is None
    assert acc.calendar.token_env == "UNTIS_FEED_TOKEN"


def test_more_accounts_share_the_school(tmp_path, monkeypatch):
    _account(monkeypatch)
    _account(monkeypatch, "_2", school=None, user="Ben")
    _account(monkeypatch, "_10", school="andereschule", user="Cleo")
    monkeypatch.setenv("UNTIS_FEED_NAME_2", "ben")

    a, b, c = Config.load(tmp_path / "config.yaml").accounts
    assert (a.key, b.key, c.key) == ("timetable", "ben", "timetable10")
    assert b.school == "musterschule"
    assert c.school == "andereschule"
    assert b.get_password() == "pw-Ben"
    assert c.calendar.feed_token == f"Cleo-{TOKEN}"


def test_missing_values_are_named(tmp_path, monkeypatch):
    monkeypatch.setenv("UNTIS_USERNAME", "Anna")
    monkeypatch.setenv("UNTIS_PASSWORD", "")  # left empty in the compose file
    with pytest.raises(ValueError) as e:
        Config.load(tmp_path / "config.yaml")
    msg = str(e.value)
    assert "UNTIS_SCHOOL" in msg
    assert "UNTIS_PASSWORD" in msg
    assert "UNTIS_FEED_TOKEN" in msg
    assert "UNTIS_USERNAME," not in msg


def test_typo_in_suffix_is_not_silently_dropped(tmp_path, monkeypatch):
    _account(monkeypatch)
    monkeypatch.setenv("UNTIS_PASSWORD_3", "x")  # but no UNTIS_USERNAME_3
    with pytest.raises(ValueError, match="UNTIS_USERNAME_3"):
        Config.load(tmp_path / "config.yaml")


def test_short_token_rejected(tmp_path, monkeypatch):
    _account(monkeypatch)
    monkeypatch.setenv("UNTIS_FEED_TOKEN", "1234")
    with pytest.raises(ValueError, match="too short"):
        Config.load(tmp_path / "config.yaml")


def test_feed_name_must_be_url_safe(tmp_path, monkeypatch):
    _account(monkeypatch)
    monkeypatch.setenv("UNTIS_FEED_NAME", "../etc")
    with pytest.raises(ValueError, match="UNTIS_FEED_NAME"):
        Config.load(tmp_path / "config.yaml")


def test_nothing_configured_explains_both_ways(tmp_path):
    with pytest.raises(FileNotFoundError, match="UNTIS_USERNAME"):
        Config.load(tmp_path / "config.yaml")


def test_config_file_wins(tmp_path, monkeypatch):
    _account(monkeypatch)
    p = tmp_path / "config.yaml"
    p.write_text(
        textwrap.dedent(
            """
            app: {}
            accounts:
              - key: "ausdatei"
                school: "s"
                username: "u"
                password: "p"
                calendar:
                  file_name: "f.ics"
            """
        )
    )
    [acc] = Config.load(p).accounts
    assert acc.key == "ausdatei"


def test_directory_instead_of_file(tmp_path):
    """What Docker leaves behind when the mounted config.yaml is missing."""
    (tmp_path / "config.yaml").mkdir()
    with pytest.raises(IsADirectoryError, match="does not exist on the host"):
        Config.load(tmp_path / "config.yaml")


def test_status_and_heartbeat_use_the_documented_names(tmp_path, monkeypatch):
    _account(monkeypatch)
    monkeypatch.setenv("STATUS_TOKEN", "status-geheim")
    monkeypatch.setenv("HEARTBEAT_URL", "https://hc.example/ping")
    monkeypatch.setenv("UNTIS_APP_TIMEZONE", "Europe/Vienna")
    cfg = Config.load(tmp_path / "config.yaml")
    assert cfg.server.status_secret == "status-geheim"
    assert cfg.server.heartbeat == "https://hc.example/ping"
    assert cfg.app.timezone == "Europe/Vienna"


def test_server_protects_the_feed(tmp_path, monkeypatch):
    _account(monkeypatch)
    monkeypatch.setenv("UNTIS_APP_REFRESH_INTERVAL_MINUTES", "0")
    out = tmp_path / "out"
    out.mkdir()
    (out / "timetable.ics").write_bytes(b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
    monkeypatch.setenv("UNTIS_APP_CACHE_TTL_SECONDS", "999999")

    def boom(*a, **kw):
        raise AssertionError("no login expected")

    monkeypatch.setattr("untis_calendar.server.UntisClient.fetch_events", boom)

    with TestClient(create_app("config.yaml")) as c:
        assert c.get("/calendar/timetable.ics").status_code == 404
        assert c.get("/calendar/timetable.ics", params={"token": "falsch"}).status_code == 404
        r = c.get("/calendar/timetable.ics", params={"token": f"Anna-{TOKEN}"})
        assert r.status_code == 200
        assert r.content.startswith(b"BEGIN:VCALENDAR")


def test_find_school(monkeypatch, capsys):
    monkeypatch.setattr(
        "untis_calendar.school_lookup.search_schools",
        lambda q: [{"displayName": "Gymnasium Musterstadt", "loginName": "gym-ms", "server": "x"}],
    )
    assert main(["find-school", "Muster", "stadt"]) == 0
    assert "school : gym-ms" in capsys.readouterr().out


def test_config_defaults_to_config_yaml(monkeypatch):
    seen = []
    monkeypatch.setattr("untis_calendar.__main__.cmd_check", lambda args: seen.append(args.config))
    main(["check"])
    assert seen == ["config.yaml"]
