from datetime import datetime, timezone

from icalendar import Calendar

from untis_calendar.models import LessonEvent
from untis_calendar.ics import events_to_ics


def ev(**over):
    d = dict(
        uid="u1@untis-calendar",
        start=datetime(2026, 9, 9, 7, 30, tzinfo=timezone.utc),
        end=datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc),
        subject="D", room="R101", teachers=["LR"], groups=["10A"],
        status="scheduled", notes=None, color_key=None,
        source_id="1", source_school="musterschule", account_key="schueler1",
    )
    d.update(over)
    return LessonEvent(**d)


def test_ics_parses_and_has_name():
    ics = events_to_ics([ev()], calendar_name="Stundenplan Musterschule", refresh_minutes=30)
    cal = Calendar.from_ical(ics)
    assert cal.get("X-WR-CALNAME") == "Stundenplan Musterschule"
    assert len(cal.walk("VEVENT")) == 1


def test_refresh_interval_is_valid_iso_duration():
    """Muss PT30M sein - '0:30:00' waere kein gueltiger iCal-Wert."""
    ics = events_to_ics([ev()], refresh_minutes=30)
    assert b"REFRESH-INTERVAL;VALUE=DURATION:PT30M" in ics


def test_cancelled_event_marked_and_transparent():
    ics = events_to_ics([ev(status="cancelled")])
    cal = Calendar.from_ical(ics)
    vev = cal.walk("VEVENT")[0]
    assert str(vev["STATUS"]) == "CANCELLED"
    assert str(vev["TRANSP"]) == "TRANSPARENT"
    assert "Entfällt" in str(vev["SUMMARY"])


def test_times_are_utc():
    ics = events_to_ics([ev()])
    assert b"DTSTART:20260909T073000Z" in ics


def test_empty_calendar_still_valid():
    cal = Calendar.from_ical(events_to_ics([], calendar_name="leer"))
    assert cal.walk("VEVENT") == []


def test_summary_uses_long_subject_and_room():
    e = ev(subject_long="Entwicklung vernetzter Prozesse", room="1012")
    ics = events_to_ics([e], subject_style="long")
    cal = Calendar.from_ical(ics)
    assert str(cal.walk("VEVENT")[0]["SUMMARY"]) == "Entwicklung vernetzter Prozesse · 1012"


def test_summary_respects_short_style():
    e = ev(subject_long="DEUTSCH")
    cal = Calendar.from_ical(events_to_ics([e], subject_style="short"))
    assert str(cal.walk("VEVENT")[0]["SUMMARY"]).startswith("D ·")


def test_description_pairs_teacher_name_and_code():
    e = ev(teachers=["LR"], teachers_long=["Mustermann"])
    cal = Calendar.from_ical(events_to_ics([e]))
    assert "Lehrer: Mustermann (LR)" in str(cal.walk("VEVENT")[0]["DESCRIPTION"])


def test_location_stays_room_number():
    """Im Gebaeude sucht man R101, nicht 'EIT-Elektrotechnik'."""
    e = ev(room="R101", room_long="Hauptgebaeude")
    cal = Calendar.from_ical(events_to_ics([e]))
    assert str(cal.walk("VEVENT")[0]["LOCATION"]) == "R101"
