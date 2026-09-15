from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

from icalendar import Calendar, Event, vText
from icalendar.prop import vDuration

from .models import LessonEvent

WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def _when(dt: datetime) -> str:
    """'Di 22.09. 17:00' - kurz genug fuer den Terminkopf."""
    return f"{WEEKDAYS[dt.weekday()]} {dt:%d.%m.} {dt:%H:%M}"


def _build_summary(e: LessonEvent, subject_style: str) -> str:
    """Terminueberschrift.

    Google kuerzt den Titel in der Monats-/Wochenansicht stark ab, deshalb
    steht der Zustand vorne: das Symbol und ein kurzes Wort sind auch dann
    noch sichtbar, wenn der Fachname abgeschnitten wird.
    """
    fach = e.subject_display(subject_style)

    if e.status == "cancelled":
        # Bei einer entfallenen Stunde ist der Raum belanglos - dafuer
        # interessiert, ob und wohin sie verlegt wurde.
        if e.moved_to:
            return f"❌ Verlegt · {fach} → {_when(e.moved_to)}"
        return f"❌ Entfällt · {fach}"

    ort = e.room or ("Online" if e.online else None)
    rest = f"{fach} · {ort}" if ort else fach

    if e.status == "moved":
        return f"➡️ {rest}"
    if e.status == "substitution":
        return f"⚠️ {rest}"
    if e.online:
        return f"💻 {rest}"
    return rest


def _build_description(e: LessonEvent) -> str:
    """Details. Das Wichtigste zuerst - Google zeigt die erste Zeile
    in der Terminvorschau."""
    head: List[str] = []

    if e.status == "cancelled":
        if e.moved_to:
            head.append(f"Entfällt hier – verlegt auf {_when(e.moved_to)}.")
        else:
            head.append("Diese Stunde entfällt.")
    elif e.status == "moved":
        if e.moved_from:
            head.append(f"Verlegt – ursprünglich {_when(e.moved_from)}.")
        else:
            head.append("Verlegte Stunde.")
    elif e.status == "substitution":
        head.append("Vertretung / Änderung.")

    for art, vorher, nachher in e.substitutions:
        head.append(f"{art}: {nachher} statt {vorher}")

    if e.online:
        if e.meeting_url:
            head.append(f"Online-Unterricht: {e.meeting_url}")
        else:
            head.append("Online-Unterricht (noch kein Link hinterlegt)")

    body: List[str] = []
    teachers = e.teacher_display()
    if teachers:
        # Kuerzel in Klammern, falls es sich vom Klarnamen unterscheidet
        if e.teachers_long and e.teachers and e.teachers_long != e.teachers:
            paired = ", ".join(
                f"{lang} ({kurz})"
                for lang, kurz in zip(e.teachers_long, e.teachers)
            )
            body.append(f"Lehrer: {paired}")
        else:
            body.append(f"Lehrer: {', '.join(teachers)}")

    if e.subject_long and e.subject_long != e.subject:
        body.append(f"Fach: {e.subject} – {e.subject_long}")
    else:
        body.append(f"Fach: {e.subject}")

    if e.room:
        room_txt = e.room
        if e.room_long and e.room_long != e.room:
            room_txt = f"{e.room} – {e.room_long}"
        body.append(f"Raum: {room_txt}")

    if e.groups:
        body.append(f"Klasse: {', '.join(e.groups)}")

    if e.notes:
        body.append(e.notes)
    body.append(f"Quelle: {e.source_school}/{e.account_key}")

    parts = head + ([""] if head else []) + body
    return "\n".join(parts)


def _categories(e: LessonEvent) -> List[str]:
    cats = [e.subject]
    if e.online:
        cats.append("Online")
    if e.status == "cancelled":
        cats.append("Entfall")
    elif e.status == "moved":
        cats.append("Verlegt")
    elif e.status == "substitution":
        cats.append("Vertretung")
    if e.color_key:
        cats.append(e.color_key)
    return cats


def events_to_ics(events: Iterable[LessonEvent], calendar_name: Optional[str] = None,
                  refresh_minutes: int = 60, subject_style: str = "long",
                  cancelled_style: str = "mark") -> bytes:
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
        if e.status == "cancelled" and cancelled_style == "hide":
            continue

        ve = Event()
        ve.add("uid", e.uid)
        ve.add("dtstamp", now_utc)
        ve.add("last-modified", now_utc)
        # UTC-Zeiten: von allen Clients (insb. Google) zuverlässig verstanden
        ve.add("dtstart", e.start.astimezone(timezone.utc))
        ve.add("dtend", e.end.astimezone(timezone.utc))

        ve.add("summary", vText(_build_summary(e, subject_style)))
        ve.add("description", vText(_build_description(e)))

        # LOCATION bleibt die Raumnummer - danach sucht man im Gebäude.
        # Bei Online-Unterricht ohne Raum kommt der Meeting-Link dorthin,
        # den machen Google und Apple in der Terminansicht anklickbar.
        # Entfallene Stunden brauchen keinen Ort.
        if e.status != "cancelled":
            if e.room:
                ve.add("location", vText(e.room))
            elif e.online:
                ve.add("location", vText(e.meeting_url or "Online"))

        if e.meeting_url and e.status != "cancelled":
            ve.add("url", e.meeting_url)

        if e.status == "cancelled":
            # Google blendet STATUS:CANCELLED in abonnierten Feeds aus - der
            # Termin waere dann komplett weg statt sichtbar gekennzeichnet.
            # Default "mark": sichtbar lassen, im Titel kennzeichnen und die
            # Zeit nicht mehr als belegt melden.
            ve.add("status", "CANCELLED" if cancelled_style == "status" else "CONFIRMED")
            ve.add("transp", "TRANSPARENT")
        else:
            ve.add("status", "CONFIRMED")
            ve.add("transp", "OPAQUE")

        ve.add("categories", _categories(e))
        cal.add_component(ve)

    return cal.to_ical()
