from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from icalendar import Calendar, Event, vText
from icalendar.prop import vDuration

from .models import LessonEvent


def events_to_ics(events: Iterable[LessonEvent], calendar_name: Optional[str] = None,
                  refresh_minutes: int = 60, subject_style: str = "long") -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//untis-calendar//v1//DE")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")

    if calendar_name:
        # Sorgt dafür, dass Google/Apple den Kalender sinnvoll benennen
        cal.add("x-wr-calname", calendar_name)
        cal.add("name", calendar_name)
    cal.add("x-wr-timezone", "Europe/Berlin")

    # Refresh-Hinweis für Clients (Google ignoriert das teilweise, schadet aber nicht)
    dur = vDuration(timedelta(minutes=refresh_minutes))
    cal.add("refresh-interval", dur, parameters={"VALUE": "DURATION"})
    cal.add("x-published-ttl", dur)

    now_utc = datetime.now(timezone.utc)

    for e in events:
        ve = Event()
        ve.add("uid", e.uid)
        ve.add("dtstamp", now_utc)
        ve.add("last-modified", now_utc)
        # UTC-Zeiten: von allen Clients (insb. Google) zuverlässig verstanden
        ve.add("dtstart", e.start.astimezone(timezone.utc))
        ve.add("dtend", e.end.astimezone(timezone.utc))

        summary = e.subject_display(subject_style)
        if e.room:
            summary = f"{summary} · {e.room}"
        if e.status == "cancelled":
            summary = f"❌ Entfällt: {summary}"
        elif e.status == "substitution":
            summary = f"⚠️ {summary}"
        ve.add("summary", vText(summary))

        # LOCATION bleibt die Raumnummer - danach sucht man im Gebäude.
        if e.room:
            ve.add("location", vText(e.room))

        desc_parts = []
        teachers = e.teacher_display()
        if teachers:
            # Kuerzel in Klammern, falls es sich vom Klarnamen unterscheidet
            if e.teachers_long and e.teachers and e.teachers_long != e.teachers:
                paired = ", ".join(
                    f"{lang} ({kurz})"
                    for lang, kurz in zip(e.teachers_long, e.teachers)
                )
                desc_parts.append(f"Lehrer: {paired}")
            else:
                desc_parts.append(f"Lehrer: {', '.join(teachers)}")

        if e.subject_long and e.subject_long != e.subject:
            desc_parts.append(f"Fach: {e.subject} – {e.subject_long}")
        else:
            desc_parts.append(f"Fach: {e.subject}")

        if e.room:
            room_txt = e.room
            if e.room_long and e.room_long != e.room:
                room_txt = f"{e.room} – {e.room_long}"
            desc_parts.append(f"Raum: {room_txt}")

        if e.groups:
            desc_parts.append(f"Klasse: {', '.join(e.groups)}")

        if e.status == "cancelled":
            desc_parts.append("Diese Stunde entfällt.")
        elif e.status == "substitution":
            desc_parts.append("Vertretung / Änderung.")
        if e.notes:
            desc_parts.append(e.notes)
        desc_parts.append(f"Quelle: {e.source_school}/{e.account_key}")
        ve.add("description", vText("\n".join(desc_parts)))

        if e.status == "cancelled":
            ve.add("status", "CANCELLED")
            ve.add("transp", "TRANSPARENT")  # blockiert die Zeit nicht
        else:
            ve.add("status", "CONFIRMED")
            ve.add("transp", "OPAQUE")

        cats = [e.subject]
        if e.color_key:
            cats.append(e.color_key)
        ve.add("categories", cats)
        cal.add_component(ve)

    return cal.to_ical()
