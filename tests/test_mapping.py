"""Tests gegen das reale WebUntis-JSON-RPC-Format (getTimetable)."""

import pytest
from pydantic import ValidationError

from untis_calendar.config import AccountConfig, AppConfig, CalendarConfig, Config
from untis_calendar.untis_client import UntisClient


def make_account(**over):
    data = {
        "key": "acc", "school": "musterschule", "username": "u", "password": "p",
        "calendar": CalendarConfig(file_name="a.ics"),
    }
    data.update(over)
    return AccountConfig(**data)


def raw(**over):
    d = {
        "id": 2452977, "date": 20260909, "startTime": 730, "endTime": 815,
        "kl": [{"id": 4720, "name": "10A", "longname": "Klasse 10A"}],
        "te": [{"id": 248, "name": "LR", "longname": "Mustermann"}],
        "su": [{"id": 333, "name": "D", "longname": "DEUTSCH"}],
        "ro": [{"id": 430, "name": "R101", "longname": "Hauptgebaeude"}],
        "activityType": "Unterricht",
    }
    d.update(over)
    return d


@pytest.fixture
def client():
    return UntisClient(AppConfig())


def test_maps_real_untis_payload(client):
    ev = client._map_raw_to_event(raw(), make_account())
    assert ev.subject == "D"
    assert ev.room == "R101"
    assert ev.teachers == ["LR"]
    assert ev.groups == ["10A"]
    assert ev.status == "scheduled"
    assert (ev.start.hour, ev.start.minute) == (7, 30)
    assert (ev.end.hour, ev.end.minute) == (8, 15)


def test_hhmm_times_are_not_treated_as_minutes(client):
    """1445 muss 14:45 sein, nicht 24h+ nach Mitternacht."""
    ev = client._map_raw_to_event(raw(startTime=1445, endTime=1530), make_account())
    assert (ev.start.hour, ev.start.minute) == (14, 45)
    assert (ev.end.hour, ev.end.minute) == (15, 30)


def test_cancelled_and_substitution_status(client):
    assert client._map_raw_to_event(raw(code="cancelled"), make_account()).status == "cancelled"
    assert client._map_raw_to_event(raw(code="irregular"), make_account()).status == "substitution"


def test_uid_is_stable_across_room_change(client):
    """Gleiche Untis-ID -> gleiche UID, damit Google den Termin aktualisiert
    statt einen zweiten anzulegen."""
    a = make_account()
    u1 = client._map_raw_to_event(raw(), a).uid
    u2 = client._map_raw_to_event(raw(ro=[{"id": 9, "name": "X999"}], startTime=900, endTime=945), a).uid
    assert u1 == u2


def test_uid_differs_between_accounts(client):
    u1 = client._map_raw_to_event(raw(), make_account(key="a")).uid
    u2 = client._map_raw_to_event(raw(), make_account(key="b")).uid
    assert u1 != u2


def test_merge_consecutive_lessons(client):
    a = make_account()
    evs = [
        client._map_raw_to_event(raw(id=1, startTime=730, endTime=815), a),
        client._map_raw_to_event(raw(id=2, startTime=815, endTime=900), a),
        client._map_raw_to_event(raw(id=3, startTime=925, endTime=1010,
                                     su=[{"id": 540, "name": "EVP"}]), a),
    ]
    merged = client._merge_consecutive(evs)
    assert len(merged) == 2
    assert (merged[0].start.hour, merged[0].start.minute) == (7, 30)
    assert (merged[0].end.hour, merged[0].end.minute) == (9, 0)
    assert merged[1].subject == "EVP"


def test_merge_does_not_join_different_rooms(client):
    a = make_account()
    evs = [
        client._map_raw_to_event(raw(id=1, startTime=730, endTime=815), a),
        client._map_raw_to_event(raw(id=2, startTime=815, endTime=900,
                                     ro=[{"id": 9, "name": "OTHER"}]), a),
    ]
    assert len(client._merge_consecutive(evs)) == 2


def test_missing_endtime_falls_back_to_45min(client):
    ev = client._map_raw_to_event(raw(endTime=None), make_account())
    assert (ev.end - ev.start).total_seconds() == 45 * 60


def test_bad_date_raises(client):
    with pytest.raises(ValueError):
        client._map_raw_to_event(raw(date="kaputt"), make_account())


def test_element_kwargs_uses_numeric_type(client):
    a = make_account(element={"type": "class", "id": 4720})
    assert client._element_kwargs(a) == {"id": 4720, "type": 1}


def test_element_without_id_is_ignored(client):
    """Die JSON-RPC-API kann mit einem Namen nichts anfangen."""
    a = make_account(element={"type": "class", "name": "10A"})
    assert client._element_kwargs(a) is None


def test_filters(client):
    a = make_account(filters={"exclude_subjects": ["D"]})
    ev = client._map_raw_to_event(raw(), a)
    assert client._filter_event(ev, a) is False


def test_duplicate_calendar_files_rejected():
    with pytest.raises(ValidationError):
        Config.model_validate({
            "app": {}, "accounts": [
                {"key": "a", "school": "s", "username": "u", "password": "p",
                 "calendar": {"file_name": "same.ics"}},
                {"key": "b", "school": "s", "username": "u", "password": "p",
                 "calendar": {"file_name": "same.ics"}},
            ],
        })


def test_duplicate_account_keys_rejected():
    with pytest.raises(ValidationError):
        Config.model_validate({
            "app": {}, "accounts": [
                {"key": "a", "school": "s", "username": "u", "password": "p",
                 "calendar": {"file_name": "a.ics"}},
                {"key": "a", "school": "s", "username": "u", "password": "p",
                 "calendar": {"file_name": "b.ics"}},
            ],
        })


# --- Klarnamen (longname) --------------------------------------------------

def test_long_names_are_extracted(client):
    ev = client._map_raw_to_event(raw(), make_account())
    assert ev.subject == "D" and ev.subject_long == "DEUTSCH"
    assert ev.teachers == ["LR"] and ev.teachers_long == ["Mustermann"]
    assert ev.room == "R101" and ev.room_long == "Hauptgebaeude"


def test_subject_display_styles(client):
    ev = client._map_raw_to_event(raw(), make_account())
    assert ev.subject_display("long") == "DEUTSCH"
    assert ev.subject_display("short") == "D"
    assert ev.subject_display("both") == "D - DEUTSCH"


def test_display_falls_back_when_no_longname(client):
    """Untis liefert longname nicht immer - dann muss das Kuerzel greifen."""
    ev = client._map_raw_to_event(raw(su=[{"id": 1, "name": "XY"}],
                                      te=[{"id": 2, "name": "AB"}]), make_account())
    assert ev.subject_display("long") == "XY"
    assert ev.teacher_display() == ["AB"]


def test_teacher_display_prefers_full_name(client):
    ev = client._map_raw_to_event(raw(), make_account())
    assert ev.teacher_display() == ["Mustermann"]


def test_invalid_subject_style_rejected():
    from untis_calendar.config import AppConfig
    with pytest.raises(ValidationError):
        AppConfig(subject_style="bunt")


# --- Online-Unterricht aus der REST-Anreicherung -----------------------------

def test_extras_are_applied(client):
    from untis_calendar.untis_rest import LessonExtras
    ex = LessonExtras()
    ex.online = True
    ex.meeting_url = "https://meet.example.org/x"
    ex.texts = ["Bitte Kamera an"]

    ev = client._map_raw_to_event(raw(id=4711), make_account(), {"4711": ex})
    assert ev.online is True
    assert ev.meeting_url == "https://meet.example.org/x"
    assert "Bitte Kamera an" in ev.notes


def test_event_without_extras_is_offline(client):
    ev = client._map_raw_to_event(raw(), make_account(), {})
    assert ev.online is False and ev.meeting_url is None


def test_merge_does_not_mix_online_and_presence(client):
    from untis_calendar.untis_rest import LessonExtras
    ex = LessonExtras()
    ex.online = True
    a = make_account()
    evs = [
        client._map_raw_to_event(raw(id=1, startTime=730, endTime=815), a, {}),
        client._map_raw_to_event(raw(id=2, startTime=815, endTime=900), a, {"2": ex}),
    ]
    assert len(client._merge_consecutive(evs)) == 2


# --- Verlegte Stunden --------------------------------------------------------

def _extras_moved_from(slot):
    from untis_calendar.untis_rest import LessonExtras
    ex = LessonExtras()
    ex.cell_state = "SHIFT"
    ex.moved_from = slot
    return ex


def test_shift_sets_status_moved(client):
    ev = client._map_raw_to_event(raw(id=7), make_account(),
                                  {"7": _extras_moved_from((20260922, 1700))})
    assert ev.status == "moved"
    assert ev.moved_from.hour == 17 and ev.moved_from.day == 22


def test_cancelled_source_gets_linked_to_new_slot(client):
    """Die REST-Ansicht liefert entfallene Stunden nicht mit; die Gegen-
    richtung muss aus der verlegten Stunde ergaenzt werden."""
    a = make_account()
    cancelled = client._map_raw_to_event(
        raw(id=1, date=20260922, startTime=1700, endTime=1745, code="cancelled"), a)
    moved = client._map_raw_to_event(
        raw(id=2, date=20260915, startTime=1840, endTime=1925), a,
        {"2": _extras_moved_from((20260922, 1700))})

    client._link_moved_lessons([cancelled, moved])
    assert cancelled.moved_to is not None
    assert (cancelled.moved_to.day, cancelled.moved_to.hour) == (15, 18)


def test_linking_ignores_unrelated_cancellations(client):
    a = make_account()
    cancelled = client._map_raw_to_event(
        raw(id=1, date=20260923, startTime=900, endTime=945, code="cancelled"), a)
    moved = client._map_raw_to_event(
        raw(id=2, date=20260915, startTime=1840, endTime=1925), a,
        {"2": _extras_moved_from((20260922, 1700))})
    client._link_moved_lessons([cancelled, moved])
    assert cancelled.moved_to is None


def test_substitution_details_are_kept(client):
    from untis_calendar.untis_rest import LessonExtras
    ex = LessonExtras()
    ex.substitutions = [("Lehrer", "VS", "MY"), ("Raum", "R101", "R204")]
    ev = client._map_raw_to_event(raw(id=3), make_account(), {"3": ex})
    assert ev.status == "substitution"
    assert ("Raum", "R101", "R204") in ev.substitutions


def test_slot_to_dt_rejects_garbage(client):
    assert client._slot_to_dt(None, "Europe/Berlin") is None
    assert client._slot_to_dt(("kaputt", 800), "Europe/Berlin") is None
