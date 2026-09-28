"""Teachers from the REST view, and fetching the whole school year."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from itertools import pairwise

import pytest

from untis_calendar import archive
from untis_calendar.config import AccountConfig, AppConfig, CalendarConfig
from untis_calendar.models import FetchResult, LessonEvent
from untis_calendar.untis_client import CHUNK_DAYS, UntisClient, chunks, pick_schoolyear
from untis_calendar.untis_direct import UntisError
from untis_calendar.untis_rest import parse_week

ACCOUNT = AccountConfig(
    key="acc",
    school="musterschule",
    server="musterschule.webuntis.com",
    username="u",
    password="p",
    calendar=CalendarConfig(file_name="a.ics"),
)


# --- Teachers ----------------------------------------------------------------

# Shape of /WebUntis/api/public/timetable/weekly/data for a class login: the
# teacher sits in the period's elements and is named in the registry.
WEEK = {
    "elementPeriods": {
        "4720": [
            {
                "id": 1001,
                "date": 20260928,
                "startTime": 800,
                "endTime": 930,
                "studentGroup": "ITD_FI51_HORM",
                "cellState": "STANDARD",
                "elements": [
                    {"type": 1, "id": 4720, "orgId": 0},
                    {"type": 2, "id": 77, "orgId": 0},
                    {"type": 3, "id": 12, "orgId": 0},
                    {"type": 4, "id": 99, "orgId": 0},
                ],
            },
            {
                # Teacher hidden by the school: id without a registry entry
                "id": 1002,
                "date": 20260928,
                "startTime": 945,
                "endTime": 1115,
                "elements": [{"type": 2, "id": 555, "orgId": 0}],
            },
            {
                # Substitution: MY stands in for HORM
                "id": 1003,
                "date": 20260929,
                "startTime": 800,
                "endTime": 930,
                "elements": [{"type": 2, "id": 78, "orgId": 77}],
            },
        ]
    },
    "elements": [
        {"type": 1, "id": 4720, "name": "FI51", "longName": "FI51"},
        {"type": 2, "id": 77, "name": "HORM", "longName": "Hormann"},
        {"type": 2, "id": 78, "name": "MY", "longName": ""},
        {"type": 3, "id": 12, "name": "ITD", "longName": "IT-Dienste", "backColor": "80ffff"},
        {"type": 4, "id": 99, "name": "C004", "longName": "C004"},
    ],
}


def _week():
    out: dict = {}
    parse_week(WEEK, 4720, out)
    return out


def test_rest_view_names_the_teacher():
    ex = _week()["1001"]
    assert ex.teachers == ["HORM"]
    assert ex.teachers_long == ["Hormann"]
    assert ex.color == "#80ffff"


def test_hidden_teacher_stays_empty():
    assert _week()["1002"].teachers == []


def test_substitute_is_the_teacher_and_the_swap_is_kept():
    ex = _week()["1003"]
    assert ex.teachers == ["MY"]
    assert ex.teachers_long == []  # no long name -> the short one is shown
    assert ex.substitutions == [("Lehrer", "HORM", "MY")]


def _raw(**over):
    d = {
        "id": 1001,
        "date": 20260928,
        "startTime": 800,
        "endTime": 930,
        "su": [{"id": 12, "name": "ITD", "longname": "IT-Dienste"}],
        "ro": [{"id": 99, "name": "C004"}],
    }
    d.update(over)
    return d


def test_class_login_gets_the_teacher_from_rest():
    """JSON-RPC sends no "te" for class logins; the web frontend shows HORM."""
    ev = UntisClient(AppConfig())._map_raw_to_event(_raw(), ACCOUNT, _week())
    assert ev.teachers == ["HORM"]
    assert ev.teacher_display() == ["Hormann"]


def test_teacher_ids_without_names_also_fall_back():
    ev = UntisClient(AppConfig())._map_raw_to_event(_raw(te=[{"id": 77}]), ACCOUNT, _week())
    assert ev.teachers == ["HORM"]


def test_json_rpc_teacher_wins_when_present():
    ev = UntisClient(AppConfig())._map_raw_to_event(
        _raw(te=[{"id": 77, "name": "HO", "longname": "Anders"}]), ACCOUNT, _week()
    )
    assert ev.teachers == ["HO"]
    assert ev.teacher_display() == ["Anders"]


# --- School year -------------------------------------------------------------

YEARS = [
    {"id": 1, "name": "2025/2026", "startDate": 20250908, "endDate": 20260731},
    {"id": 2, "name": "2026/2027", "startDate": 20260907, "endDate": 20270730},
]


def test_current_schoolyear():
    assert pick_schoolyear(YEARS, date(2026, 9, 28)) == (date(2026, 9, 7), date(2027, 7, 30))


def test_summer_break_takes_the_next_year():
    assert pick_schoolyear(YEARS, date(2026, 8, 15)) == (date(2026, 9, 7), date(2027, 7, 30))


def test_no_schoolyear():
    assert pick_schoolyear(YEARS, date(2030, 1, 1)) is None
    assert pick_schoolyear([{"startDate": "kaputt"}], date(2026, 9, 28)) is None


@pytest.mark.parametrize("backwards", [False, True])
def test_chunks_cover_without_gaps(backwards):
    first, last = date(2026, 9, 7), date(2027, 7, 30)
    got = chunks(first, last, backwards=backwards)
    ordered = sorted(got)
    assert ordered[0][0] == first and ordered[-1][1] == last
    for (_, hi), (lo, _) in pairwise(ordered):
        assert lo == hi + timedelta(days=1)
    assert all((hi - lo).days < CHUNK_DAYS for lo, hi in got)
    # Walks outwards from the window: backwards starts at the end
    assert got[0][1 if backwards else 0] == (last if backwards else first)


class FakeSession:
    """Stands in for DirectUntisSession. Refuses everything past ``visible``."""

    def __init__(self, visible_until: date, years=YEARS, years_error=None):
        self.visible_until = visible_until
        self.years = years
        self.years_error = years_error
        self.calls: list[tuple[date, date]] = []
        self.person_id, self.person_type = 4720, 1

    def timetable(self, start, end, element=None):
        self.calls.append((start, end))
        if start > self.visible_until:
            raise UntisError("WebUntis API Error (-7004): no allowed date")
        # One lesson on every Monday of the range
        day = start + timedelta(days=(7 - start.weekday()) % 7)
        out = []
        while day <= min(end, self.visible_until):
            out.append(_raw(id=int(day.strftime("%Y%m%d")), date=int(day.strftime("%Y%m%d"))))
            day += timedelta(days=7)
        return out

    def schoolyears(self):
        if self.years_error:
            raise self.years_error
        return self.years

    def timegrid(self):
        return {}


@pytest.fixture
def fake(monkeypatch):
    holder: dict = {"rest": []}

    def install(session):
        @contextmanager
        def login(**kw):
            yield session

        def extras(**kw):
            holder["rest"].append((kw["start"], kw["end"]))
            return {}

        monkeypatch.setattr("untis_calendar.untis_client.direct_untis_login", login)
        monkeypatch.setattr("untis_calendar.untis_client.fetch_lesson_extras", extras)
        return holder

    return install


NOW = datetime(2026, 9, 28, 10, 0)


def test_window_only_by_default_call(fake):
    session = FakeSession(visible_until=date(2027, 7, 30))
    fake(session)
    result = UntisClient(AppConfig()).fetch(ACCOUNT, now=NOW)
    assert session.calls == [(date(2026, 9, 25), date(2026, 10, 26))]
    assert result.covered == session.calls
    assert result.schoolyear is False


def test_schoolyear_is_fetched_as_far_as_visible(fake):
    session = FakeSession(visible_until=date(2026, 12, 20))
    rest = fake(session)
    result = UntisClient(AppConfig()).fetch(ACCOUNT, now=NOW, schoolyear=True)

    assert result.schoolyear is True
    # The past part of the school year came back ...
    assert min(lo for lo, _ in result.covered) == date(2026, 9, 7)
    # ... the future up to the visibility limit, and nothing claimed beyond it
    assert max(hi for _, hi in result.covered) < date(2026, 12, 20) + timedelta(days=CHUNK_DAYS)
    assert all(lo <= date(2026, 12, 20) for lo, _ in result.covered)
    # Stopped at the first refusal instead of asking for every later block
    refused = [c for c in session.calls if c[0] > date(2026, 12, 20)]
    assert len(refused) == 1
    assert max(e.start.date() for e in result.events) == date(2026, 12, 14)
    # REST only up to the last lesson, not the whole school year
    assert rest["rest"] == [(date(2026, 9, 7), date(2026, 12, 14))]


def test_without_archive_the_past_is_not_fetched(fake):
    session = FakeSession(visible_until=date(2027, 7, 30))
    fake(session)
    result = UntisClient(AppConfig(archive=False)).fetch(ACCOUNT, now=NOW, schoolyear=True)
    assert min(lo for lo, _ in result.covered) == date(2026, 9, 25)
    assert max(hi for _, hi in result.covered) == date(2027, 7, 30)


def test_unknown_schoolyear_falls_back_to_the_window(fake):
    session = FakeSession(visible_until=date(2027, 7, 30), years_error=UntisError("nope"))
    fake(session)
    result = UntisClient(AppConfig()).fetch(ACCOUNT, now=NOW, schoolyear=True)
    assert result.covered == [(date(2026, 9, 25), date(2026, 10, 26))]


def test_window_failure_is_still_fatal(fake):
    """The window must succeed - only the extra school-year blocks are optional."""
    fake(FakeSession(visible_until=date(2020, 1, 1)))
    with pytest.raises(UntisError):
        UntisClient(AppConfig()).fetch(ACCOUNT, now=NOW, schoolyear=True)


# --- Archive: covered days are authoritative ---------------------------------


def ev(uid, day, subject="D"):
    start = datetime(*day, 8, 0, tzinfo=timezone.utc)
    return LessonEvent(
        uid=uid,
        start=start,
        end=start + timedelta(minutes=45),
        subject=subject,
        room=None,
        teachers=[],
        groups=[],
        status="scheduled",
        notes=None,
        color_key=None,
        source_id=uid,
        source_school="s",
        account_key="acc",
    )


def test_lesson_removed_in_webuntis_disappears(tmp_path):
    """New timetable version, new period ids: the old lessons must not linger."""
    week = (date(2026, 9, 28), date(2026, 10, 4))
    archive.combine(tmp_path, "a.ics", [ev("alt", (2026, 9, 29)), ev("mar", (2026, 3, 10))])
    published = archive.combine(tmp_path, "a.ics", [ev("neu", (2026, 9, 29))], covered=[week])
    # Replaced inside the fetched week, history outside it untouched
    assert {e.uid for e in published} == {"neu", "mar"}


def test_nothing_is_removed_without_coverage(tmp_path):
    archive.combine(tmp_path, "a.ics", [ev("alt", (2026, 9, 29))])
    assert {e.uid for e in archive.combine(tmp_path, "a.ics", [])} == {"alt"}


def test_schoolyear_stamp_survives_window_runs(tmp_path):
    now = datetime.now(timezone.utc)
    assert archive.schoolyear_due(tmp_path, "a.ics", 12)

    archive.combine(tmp_path, "a.ics", [ev("x", (2026, 9, 29))], schoolyear_fetched=True)
    assert not archive.schoolyear_due(tmp_path, "a.ics", 12)
    # A plain window run must not forget when the school year was fetched
    archive.combine(tmp_path, "a.ics", [ev("x", (2026, 9, 29))])
    assert not archive.schoolyear_due(tmp_path, "a.ics", 12)
    assert archive.schoolyear_due(tmp_path, "a.ics", 12, now=now + timedelta(hours=13))


def _result(*events, covered=(), schoolyear=False):
    return FetchResult(events=list(events), covered=list(covered), schoolyear=schoolyear)


def test_update_without_archive_keeps_only_window_and_future(tmp_path):
    app = AppConfig(archive=False, window_days_before=3)
    today = date(2026, 9, 28)
    got = archive.update(
        tmp_path,
        "a.ics",
        _result(ev("alt", (2026, 9, 1)), ev("heute", (2026, 9, 28)), ev("mai", (2027, 5, 3))),
        app,
        today=today,
    )
    assert {e.uid for e in got} == {"heute", "mai"}


def test_switching_schoolyear_off_drops_far_lessons(tmp_path):
    today = date(2026, 9, 28)
    archive.update(
        tmp_path, "a.ics", _result(ev("mai", (2027, 5, 3)), schoolyear=True), AppConfig(), today
    )
    got = archive.update(
        tmp_path,
        "a.ics",
        _result(ev("heute", (2026, 9, 28))),
        AppConfig(fetch_schoolyear=False),
        today,
    )
    assert {e.uid for e in got} == {"heute"}


def test_generate_fetches_the_schoolyear_only_when_due(tmp_path, monkeypatch):
    from untis_calendar.__main__ import main

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""
app:
  output_dir: "{tmp_path / "out"}"
accounts:
  - key: "acc"
    school: "s"
    server: "s.webuntis.com"
    username: "u"
    password: "p"
    calendar:
      file_name: "a.ics"
"""
    )
    seen = []

    def fetch(self, account, now=None, schoolyear=False):
        seen.append(schoolyear)
        return _result(ev("x", (2026, 9, 29)), schoolyear=schoolyear)

    monkeypatch.setattr("untis_calendar.__main__.UntisClient.fetch", fetch)
    assert main(["generate", "--config", str(cfg)]) == 0
    assert main(["generate", "--config", str(cfg)]) == 0
    assert seen == [True, False]
