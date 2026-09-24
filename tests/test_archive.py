"""Keeping lessons that WebUntis no longer returns."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from untis_calendar import archive
from untis_calendar.models import LessonEvent


def ev(uid="u1", day=(2026, 9, 24), subject="D", **over):
    start = datetime(*day, 8, 0, tzinfo=timezone.utc)
    data = {
        "uid": uid,
        "start": start,
        "end": start + timedelta(minutes=45),
        "subject": subject,
        "room": "R101",
        "teachers": ["MM"],
        "groups": ["10A"],
        "status": "scheduled",
        "notes": None,
        "color_key": None,
        "source_id": "1",
        "source_school": "musterschule",
        "account_key": "acc",
    }
    data.update(over)
    return LessonEvent(**data)


def test_roundtrip_preserves_event(tmp_path):
    p = tmp_path / "a.archive.json"
    store: dict = {}
    archive.merge(store, [ev(teachers_long=["Mustermann"], periods=["1"])])
    archive.save(p, store)

    back = archive.to_events(archive.load(p))
    assert len(back) == 1
    got = back[0]
    assert got.uid == "u1"
    assert got.subject == "D"
    assert got.teachers_long == ["Mustermann"]
    assert got.periods == ["1"]
    assert got.start == ev().start


def test_datetimes_survive_including_optional_ones(tmp_path):
    p = tmp_path / "a.archive.json"
    moved = datetime(2026, 10, 1, 17, 0, tzinfo=timezone.utc)
    store: dict = {}
    archive.merge(store, [ev(status="cancelled", moved_to=moved)])
    archive.save(p, store)

    got = archive.to_events(archive.load(p))[0]
    assert got.moved_to == moved
    assert got.moved_from is None
    assert got.status == "cancelled"


def test_substitution_tuples_survive_json(tmp_path):
    """JSON has no tuples; they must come back as tuples, not lists."""
    p = tmp_path / "a.archive.json"
    store: dict = {}
    archive.merge(store, [ev(substitutions=[("Lehrer", "VS", "MY")])])
    archive.save(p, store)

    got = archive.to_events(archive.load(p))[0]
    assert got.substitutions == [("Lehrer", "VS", "MY")]


def test_lesson_outside_the_window_is_kept(tmp_path):
    """The whole point: WebUntis stops returning old lessons, we do not."""
    old = ev(uid="old", day=(2026, 3, 10), subject="M")
    store: dict = {}
    archive.merge(store, [old])
    archive.save(archive.store_path(tmp_path, "a.ics"), store)

    # A later run only sees current lessons
    published = archive.combine(tmp_path, "a.ics", [ev(uid="new")])
    uids = {e.uid for e in published}
    assert uids == {"old", "new"}


def test_fresh_data_wins_over_archived_copy(tmp_path):
    """A lesson that changed must be corrected, not duplicated."""
    archive.combine(tmp_path, "a.ics", [ev(uid="u1", room="R101")])
    published = archive.combine(tmp_path, "a.ics", [ev(uid="u1", room="R999")])
    assert len(published) == 1
    assert published[0].room == "R999"


def test_results_are_sorted_by_start(tmp_path):
    archive.combine(tmp_path, "a.ics", [ev(uid="b", day=(2026, 9, 25))])
    published = archive.combine(tmp_path, "a.ics", [ev(uid="a", day=(2026, 9, 24))])
    assert [e.uid for e in published] == ["a", "b"]


def test_retention_drops_old_entries(tmp_path):
    store: dict = {}
    archive.merge(store, [ev(uid="old", day=(2020, 1, 1)), ev(uid="new")])
    removed = archive.prune(store, retention_days=30, today=date(2026, 9, 24))
    assert removed == 1
    assert set(store) == {"new"}


def test_retention_zero_keeps_everything():
    store: dict = {}
    archive.merge(store, [ev(uid="ancient", day=(2001, 1, 1))])
    assert archive.prune(store, retention_days=0, today=date(2026, 9, 24)) == 0
    assert set(store) == {"ancient"}


def test_missing_file_is_empty_not_an_error(tmp_path):
    assert archive.load(tmp_path / "nope.json") == {}


def test_corrupt_file_does_not_crash_the_sync(tmp_path):
    """A damaged archive must never stop the calendar from being written."""
    p = tmp_path / "a.archive.json"
    p.write_text("{not json", encoding="utf-8")
    assert archive.load(p) == {}


def test_version_mismatch_starts_empty(tmp_path):
    p = tmp_path / "a.archive.json"
    p.write_text(json.dumps({"version": 999, "events": {"x": {}}}), encoding="utf-8")
    assert archive.load(p) == {}


def test_unknown_fields_are_ignored(tmp_path):
    """An archive written by a newer version must still load."""
    p = tmp_path / "a.archive.json"
    store: dict = {}
    archive.merge(store, [ev()])
    store["u1"]["field_from_the_future"] = 42
    archive.save(p, store)
    assert len(archive.to_events(archive.load(p))) == 1


def test_store_path_derives_from_calendar_name(tmp_path):
    assert archive.store_path(tmp_path, "student1.ics").name == "student1.archive.json"


def test_save_is_atomic(tmp_path):
    """No .tmp left behind, and the real file is complete."""
    p = tmp_path / "a.archive.json"
    store: dict = {}
    archive.merge(store, [ev()])
    archive.save(p, store)
    assert p.exists()
    assert not list(tmp_path.glob("*.tmp"))
    assert json.loads(p.read_text())["version"] == archive.SCHEMA_VERSION
