from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from .config import AccountConfig, AppConfig
from .models import LessonEvent
from .school_lookup import resolve_server
from .untis_direct import UntisError, direct_untis_login
from .untis_rest import fetch_lesson_extras
from .utils import stable_uid, tz_aware

logger = logging.getLogger(__name__)

# WebUntis Element-Typen
ELEMENT_TYPES = {"class": 1, "teacher": 2, "subject": 3, "room": 4, "student": 5}


class UntisClient:
    def __init__(self, app: AppConfig):
        self.app = app

    def fetch_events(
        self, account: AccountConfig, now: datetime | None = None
    ) -> list[LessonEvent]:
        """Holt den Stundenplan für einen Account.

        Wirft bei Fehlern eine Exception. Bewusst KEIN leeres Ergebnis bei
        Fehlern zurückgeben - sonst überschreibt ein kurzer Ausfall den
        zuletzt bekannten guten Kalender mit einem leeren.
        """
        now = now or datetime.now()
        start = (now - timedelta(days=self.app.window_days_before)).date()
        end = (now + timedelta(days=self.app.window_days_after)).date()

        server = account.server or resolve_server(account.school)
        if not server:
            raise UntisError(
                f"Kein Server für Schule '{account.school}' gefunden. "
                f"Bitte 'server:' in der Config setzen oder Schulnamen prüfen."
            )

        with direct_untis_login(
            server=server,
            school=account.school,
            username=account.username,
            password=account.get_password(),
            verify_ssl=account.verify_ssl,
        ) as sess:
            logger.info("Abrufe Stundenplan für %s von %s bis %s", account.key, start, end)
            raw_list = sess.timetable(start=start, end=end, element=self._element_kwargs(account))
            logger.info("Empfangen: %d Roheinträge für %s", len(raw_list), account.key)
            person_id, person_type = sess.person_id, sess.person_type

        # Online-Unterricht und Stundentexte kennt nur die REST-Ansicht.
        # Schlaegt das fehl, laeuft der Sync ohne diese Extras weiter.
        extras = {}
        if self.app.fetch_online_info and person_id and person_type:
            extras = fetch_lesson_extras(
                server=server,
                school=account.school,
                username=account.username,
                password=account.get_password(),
                element_id=person_id,
                element_type=person_type,
                start=start,
                end=end,
                verify_ssl=account.verify_ssl,
            )
            online_count = sum(1 for e in extras.values() if e.online)
            if online_count:
                logger.info("REST: %d Stunde(n) als Online-Unterricht markiert", online_count)

        events = [self._map_raw_to_event(r, account, extras) for r in raw_list]
        self._link_moved_lessons(events)
        events = [e for e in events if self._filter_event(e, account)]
        if not account.include_cancelled:
            events = [e for e in events if e.status != "cancelled"]
        events.sort(key=lambda e: (e.start, e.subject))
        if self.app.merge_consecutive:
            events = self._merge_consecutive(events)
        logger.info("Ergebnis: %d Termine für %s", len(events), account.key)
        return events

    def _element_kwargs(self, account: AccountConfig) -> dict[str, Any] | None:
        """Element für getTimetable. None -> eingeloggter Benutzer wird verwendet."""
        el = account.element
        if not el or not el.type:
            return None
        type_id = ELEMENT_TYPES.get(el.type)
        if not type_id:
            logger.warning("Unbekannter element.type '%s' - ignoriert", el.type)
            return None
        if el.id is None:
            logger.warning(
                "element.type '%s' gesetzt, aber keine element.id - "
                "die JSON-RPC-API braucht eine numerische ID. Wird ignoriert.",
                el.type,
            )
            return None
        return {"id": el.id, "type": type_id}

    def _map_raw_to_event(
        self, r: dict[str, Any], account: AccountConfig, extras: dict[str, Any] | None = None
    ) -> LessonEvent:
        tz = self.app.timezone
        school = account.school
        acct = account.key

        start_dt, end_dt = self._parse_times(r, tz)

        su = r.get("su") or r.get("subject")
        ro = r.get("ro") or r.get("room")
        te = r.get("te") or r.get("teachers")

        subject = self._first_name(su) or "Unbekannt"
        subject_long = self._first_name(su, long=True)
        room = self._first_name(ro)
        room_long = self._first_name(ro, long=True)
        teachers = self._all_names(te)
        teachers_long = self._all_names(te, long=True)
        groups = self._all_names(r.get("kl") or r.get("groups"))

        # code: "cancelled" = entfällt, "irregular" = Vertretung/Änderung
        code = str(r.get("code", "") or "").lower()
        if r.get("cancelled") or code in {"cancelled", "canceled", "cancel"}:
            status = "cancelled"
        elif code == "irregular":
            # "irregular" heisst nur "irgendwas weicht ab". Was genau, sagt
            # erst die REST-Ansicht (verlegt vs. Vertretung).
            status = "substitution"
        else:
            status = "scheduled"

        note_parts = [
            str(r.get(k)).strip()
            for k in ("substText", "lstext", "info", "activityType")
            if r.get(k) and str(r.get(k)).strip() and str(r.get(k)).strip() != "Unterricht"
        ]

        source_id = str(r.get("id") or r.get("lstid") or "")

        online = False
        meeting_url = None
        moved_from = moved_to = None
        substitutions: list[tuple] = []
        extra = (extras or {}).get(source_id)
        if extra is not None:
            online = extra.online
            meeting_url = extra.meeting_url
            note_parts.extend(extra.texts)
            substitutions = list(extra.substitutions)
            moved_from = self._slot_to_dt(extra.moved_from, tz)
            moved_to = self._slot_to_dt(extra.moved_to, tz)

            if extra.cell_state == "SHIFT" or moved_from:
                status = "moved"
            elif substitutions and status == "scheduled":
                status = "substitution"

        notes = " | ".join(dict.fromkeys(note_parts)) or None

        # UID stabil an der Untis-Perioden-ID festmachen: verschiebt sich eine
        # Stunde (Raum/Zeit/Vertretung), bleibt es derselbe Kalendereintrag und
        # wird aktualisiert statt dupliziert.
        if source_id:
            uid = stable_uid(school, acct, "lesson", source_id)
        else:
            uid = stable_uid(
                school, acct, subject, start_dt.isoformat(), end_dt.isoformat(), room or ""
            )

        return LessonEvent(
            uid=uid,
            start=start_dt,
            end=end_dt,
            subject=subject,
            room=room,
            teachers=teachers,
            groups=groups,
            status=status,
            notes=notes,
            color_key=account.color_map.get(subject),
            source_id=source_id,
            source_school=school,
            account_key=acct,
            subject_long=subject_long,
            teachers_long=teachers_long,
            room_long=room_long,
            online=online,
            meeting_url=meeting_url,
            moved_from=moved_from,
            moved_to=moved_to,
            substitutions=substitutions,
        )

    @staticmethod
    def _slot_to_dt(slot: tuple | None, tz: str) -> datetime | None:
        """(20260922, 1700) -> datetime 2026-09-22 17:00 in der Zielzone."""
        if not slot:
            return None
        ymd, hhmm = slot
        ymd = str(ymd)
        if len(ymd) != 8 or not ymd.isdigit():
            return None
        try:
            return tz_aware(
                datetime(
                    int(ymd[0:4]), int(ymd[4:6]), int(ymd[6:8]), int(hhmm) // 100, int(hhmm) % 100
                ),
                tz,
            )
        except ValueError:
            return None

    @staticmethod
    def _link_moved_lessons(events: list[LessonEvent]) -> None:
        """Entfallene Quell-Stunde mit ihrem neuen Termin verknuepfen.

        Die REST-Ansicht liefert entfallene Stunden nicht mit, kennt aber bei
        der verlegten Stunde den Ursprungstermin. Daraus laesst sich die
        Gegenrichtung ergaenzen, damit am entfallenen Termin steht, wohin die
        Stunde verschoben wurde.
        """
        by_slot: dict[tuple, LessonEvent] = {
            (e.start.date(), e.start.hour, e.start.minute): e
            for e in events
            if e.status == "cancelled"
        }
        for ev in events:
            if not ev.moved_from:
                continue
            key = (ev.moved_from.date(), ev.moved_from.hour, ev.moved_from.minute)
            src = by_slot.get(key)
            if src is not None and src.moved_to is None:
                src.moved_to = ev.start

    def _parse_times(self, d: dict[str, Any], tz: str) -> tuple[datetime, datetime]:
        if d.get("start") and d.get("end"):
            s = datetime.fromisoformat(str(d["start"]))
            e = datetime.fromisoformat(str(d["end"]))
            return tz_aware(s, tz), tz_aware(e, tz)

        ymd = str(d.get("date"))
        if len(ymd) != 8 or not ymd.isdigit():
            raise ValueError(f"Unerwartetes Datumsformat in Untis-Eintrag: {d!r}")
        y, m, day = int(ymd[0:4]), int(ymd[4:6]), int(ymd[6:8])

        # startTime/endTime sind HHMM als Integer (730 = 07:30, 1445 = 14:45)
        time_s = int(d.get("startTime") or 800)
        time_e = int(d.get("endTime") or 0)
        s = datetime(y, m, day, time_s // 100, time_s % 100)
        if time_e:
            e = datetime(y, m, day, time_e // 100, time_e % 100)
        else:
            e = s + timedelta(minutes=45)
        if e <= s:
            logger.warning("Endzeit <= Startzeit in %r - setze +45 Min", d)
            e = s + timedelta(minutes=45)
        return tz_aware(s, tz), tz_aware(e, tz)

    @staticmethod
    def _first_name(val: Any, long: bool = False) -> str | None:
        names = UntisClient._all_names(val, long=long)
        return names[0] if names else None

    @staticmethod
    def _all_names(val: Any, long: bool = False) -> list[str]:
        if val is None:
            return []
        if isinstance(val, str):
            return [val] if val else []
        if isinstance(val, dict):
            val = [val]
        if not isinstance(val, list):
            return [str(val)]
        out: list[str] = []
        for item in val:
            if isinstance(item, dict):
                if long:
                    name = item.get("longname") or item.get("longName") or item.get("name")
                else:
                    name = item.get("name") or item.get("longname") or item.get("longName")
                if name:
                    out.append(str(name))
            elif item:
                out.append(str(item))
        return out

    @staticmethod
    def _merge_consecutive(events: list[LessonEvent]) -> list[LessonEvent]:
        """Fasst direkt aufeinanderfolgende, identische Stunden zu einem Block zusammen.

        Aus 2x45 Min Deutsch (7:30-8:15, 8:15-9:00) wird ein Termin 7:30-9:00.
        Kurze Pausen (bis 30 Min) zwischen gleichen Stunden werden überbrückt.
        """
        merged: list[LessonEvent] = []
        for ev in events:
            prev = merged[-1] if merged else None
            if (
                prev
                and prev.subject == ev.subject
                and prev.room == ev.room
                and prev.teachers == ev.teachers
                and prev.subject_long == ev.subject_long
                and prev.online == ev.online
                and prev.meeting_url == ev.meeting_url
                and prev.moved_from == ev.moved_from
                and prev.moved_to == ev.moved_to
                and prev.substitutions == ev.substitutions
                and prev.groups == ev.groups
                and prev.status == ev.status
                and prev.notes == ev.notes
                and prev.start.date() == ev.start.date()
                and prev.end <= ev.start <= prev.end + timedelta(minutes=30)
            ):
                prev.end = max(prev.end, ev.end)
                continue
            merged.append(ev)
        return merged

    def _filter_event(self, ev: LessonEvent, account: AccountConfig) -> bool:
        inc = account.filters.include_subjects
        exc = account.filters.exclude_subjects
        if inc and ev.subject not in inc:
            return False
        return not (exc and ev.subject in exc)
