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
    """Sichtbar gekennzeichnet und zeitlich nicht blockierend.
    Zum STATUS siehe test_cancelled_stays_visible_by_default."""
    ics = events_to_ics([ev(status="cancelled")])
    cal = Calendar.from_ical(ics)
    vev = cal.walk("VEVENT")[0]
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


# --- Entfall -----------------------------------------------------------------

def test_cancelled_stays_visible_by_default():
    """Google blendet STATUS:CANCELLED aus - der Termin waere dann komplett
    weg statt sichtbar gekennzeichnet."""
    cal = Calendar.from_ical(events_to_ics([ev(status="cancelled")]))
    vev = cal.walk("VEVENT")[0]
    assert str(vev["STATUS"]) == "CONFIRMED"
    assert "Entfällt" in str(vev["SUMMARY"])


def test_cancelled_never_blocks_time():
    cal = Calendar.from_ical(events_to_ics([ev(status="cancelled")]))
    assert str(cal.walk("VEVENT")[0]["TRANSP"]) == "TRANSPARENT"


def test_cancelled_style_status_emits_cancelled():
    cal = Calendar.from_ical(events_to_ics([ev(status="cancelled")],
                                           cancelled_style="status"))
    assert str(cal.walk("VEVENT")[0]["STATUS"]) == "CANCELLED"


def test_cancelled_style_hide_drops_event():
    ics = events_to_ics([ev(status="cancelled"), ev(uid="u2")],
                        cancelled_style="hide")
    assert len(Calendar.from_ical(ics).walk("VEVENT")) == 1


# --- Online-Unterricht -------------------------------------------------------

def test_online_lesson_is_marked():
    cal = Calendar.from_ical(events_to_ics([ev(online=True)]))
    assert "💻" in str(cal.walk("VEVENT")[0]["SUMMARY"])


def test_meeting_url_lands_in_url_property():
    link = "https://meet.example.org/abc"
    cal = Calendar.from_ical(events_to_ics([ev(online=True, meeting_url=link)]))
    vev = cal.walk("VEVENT")[0]
    assert str(vev["URL"]) == link
    assert link in str(vev["DESCRIPTION"])


def test_online_without_room_puts_link_in_location():
    """Ohne Raum ist der Meeting-Link die nuetzlichste Ortsangabe."""
    link = "https://meet.example.org/abc"
    cal = Calendar.from_ical(events_to_ics([ev(room=None, online=True, meeting_url=link)]))
    assert str(cal.walk("VEVENT")[0]["LOCATION"]) == link


def test_online_without_link_says_so():
    cal = Calendar.from_ical(events_to_ics([ev(online=True)]))
    assert "noch kein Link" in str(cal.walk("VEVENT")[0]["DESCRIPTION"])


def test_room_wins_over_online_in_summary():
    """Hybrid-Stunde: Raumnummer ist nuetzlicher, 💻 kennzeichnet Online."""
    cal = Calendar.from_ical(events_to_ics([ev(room="R101", online=True)]))
    su = str(cal.walk("VEVENT")[0]["SUMMARY"])
    assert su.endswith("R101") and su.startswith("💻")


def test_cancelled_beats_online_marker():
    cal = Calendar.from_ical(events_to_ics([ev(status="cancelled", online=True)]))
    assert str(cal.walk("VEVENT")[0]["SUMMARY"]).startswith("❌")


# --- Verlegung und Vertretung in der Anzeige ---------------------------------

from datetime import datetime as _dt


def test_cancelled_names_new_slot_in_title():
    """Beim Entfall interessiert vor allem, wohin die Stunde verlegt wurde."""
    e = ev(status="cancelled",
           moved_to=_dt(2026, 9, 15, 18, 40, tzinfo=timezone.utc))
    su = str(Calendar.from_ical(events_to_ics([e])).walk("VEVENT")[0]["SUMMARY"])
    assert su.startswith("❌ Verlegt")
    assert "Di 15.09. 18:40" in su


def test_cancelled_drops_room_from_title_and_location():
    """Der Raum einer entfallenen Stunde ist belanglos."""
    cal = Calendar.from_ical(events_to_ics([ev(status="cancelled", room="R101")]))
    vev = cal.walk("VEVENT")[0]
    assert "R101" not in str(vev["SUMMARY"])
    assert "LOCATION" not in vev


def test_moved_lesson_shows_origin():
    e = ev(status="moved",
           moved_from=_dt(2026, 9, 22, 17, 0, tzinfo=timezone.utc))
    vev = Calendar.from_ical(events_to_ics([e])).walk("VEVENT")[0]
    assert str(vev["SUMMARY"]).startswith("➡️")
    assert "Di 22.09. 17:00" in str(vev["DESCRIPTION"])


def test_substitution_lists_what_changed():
    e = ev(status="substitution", substitutions=[("Lehrer", "VS", "MY")])
    desc = str(Calendar.from_ical(events_to_ics([e])).walk("VEVENT")[0]["DESCRIPTION"])
    assert "Lehrer: MY statt VS" in desc


def test_status_word_comes_first_for_truncation():
    """Google kuerzt Titel in der Gitteransicht - der Zustand muss vorne stehen."""
    e = ev(status="cancelled", subject_long="Ein sehr langer Fachname hier")
    su = str(Calendar.from_ical(events_to_ics([e])).walk("VEVENT")[0]["SUMMARY"])
    assert su[:12].startswith("❌ Entfällt")


def test_categories_reflect_state():
    for status, cat in (("cancelled", "Entfall"), ("moved", "Verlegt"),
                        ("substitution", "Vertretung")):
        cal = Calendar.from_ical(events_to_ics([ev(status=status)]))
        cats = cal.walk("VEVENT")[0]["CATEGORIES"].cats
        assert cat in [str(c) for c in cats], (status, cats)
