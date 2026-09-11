from datetime import datetime, timedelta
from untis_calendar.models import LessonEvent
from untis_calendar.ics import events_to_ics


def test_ics_builds_bytes():
    e = LessonEvent(
        uid="test@untis-calendar",
        start=datetime(2025, 1, 1, 8, 0),
        end=datetime(2025, 1, 1, 8, 45),
        subject="MATHE",
        room="R101",
        teachers=["Müller"],
        groups=["10a"],
        status="scheduled",
        notes=None,
        color_key=None,
        source_id="1",
        source_school="Schule",
        account_key="acc",
    )
    ics = events_to_ics([e])
    assert isinstance(ics, (bytes, bytearray))
    assert b"BEGIN:VEVENT" in ics
