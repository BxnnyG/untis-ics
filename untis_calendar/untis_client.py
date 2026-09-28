from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

import requests

from .config import AccountConfig, AppConfig
from .models import FetchResult, LessonEvent
from .school_lookup import resolve_server
from .untis_direct import DirectUntisSession, UntisError, direct_untis_login
from .untis_rest import fetch_lesson_extras
from .utils import stable_uid, tz_aware

logger = logging.getLogger(__name__)

# WebUntis element types
ELEMENT_TYPES = {"class": 1, "teacher": 2, "subject": 3, "room": 4, "student": 5}

# Block size for fetching the school year. Schools can limit how far ahead
# (or back) students may look; small blocks mean a refused one loses little.
CHUNK_DAYS = 28


def _ymd(value: Any) -> date:
    """20260928 -> date(2026, 9, 28)"""
    s = str(value)
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"not a YYYYMMDD date: {value!r}")
    return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))


def pick_schoolyear(years: list[dict[str, Any]], today: date) -> tuple[date, date] | None:
    """The school year containing today, or else the next one (summer break)."""
    parsed = []
    for y in years or []:
        try:
            parsed.append((_ymd(y["startDate"]), _ymd(y["endDate"])))
        except (KeyError, TypeError, ValueError):
            continue
    for first, last in parsed:
        if first <= today <= last:
            return first, last
    upcoming = sorted(p for p in parsed if p[0] > today)
    return upcoming[0] if upcoming else None


def chunks(first: date, last: date, backwards: bool = False) -> list[tuple[date, date]]:
    """Split first..last into CHUNK_DAYS blocks, optionally starting at the end."""
    out = []
    step = timedelta(days=CHUNK_DAYS - 1)
    if backwards:
        cur = last
        while cur >= first:
            lo = max(cur - step, first)
            out.append((lo, cur))
            cur = lo - timedelta(days=1)
    else:
        cur = first
        while cur <= last:
            hi = min(cur + step, last)
            out.append((cur, hi))
            cur = hi + timedelta(days=1)
    return out


class UntisClient:
    def __init__(self, app: AppConfig):
        self.app = app

    def fetch_events(
        self, account: AccountConfig, now: datetime | None = None
    ) -> list[LessonEvent]:
        """Lessons in the configured window."""
        return self.fetch(account, now).events

    def fetch(
        self, account: AccountConfig, now: datetime | None = None, schoolyear: bool = False
    ) -> FetchResult:
        """Fetch the timetable for one account.

        The window around today is always fetched and must succeed. With
        ``schoolyear`` the rest of the current school year is fetched on top,
        as far as the school allows - that part is best effort.

        Raises on failure. Deliberately does NOT return an empty result on
        error - otherwise a brief outage would overwrite the last known good
        calendar with an empty one.
        """
        now = now or datetime.now()
        start = (now - timedelta(days=self.app.window_days_before)).date()
        end = (now + timedelta(days=self.app.window_days_after)).date()

        server = account.server or resolve_server(account.school)
        if not server:
            raise UntisError(
                f"No server found for school '{account.school}'. "
                f"Set 'server:' in the config or check the school name."
            )

        with direct_untis_login(
            server=server,
            school=account.school,
            username=account.username,
            password=account.get_password(),
            verify_ssl=account.verify_ssl,
        ) as sess:
            logger.info("Fetching timetable for %s from %s to %s", account.key, start, end)
            element = self._element_kwargs(account)
            raw_list = sess.timetable(start=start, end=end, element=element)
            covered = [(start, end)]
            if schoolyear:
                more, more_covered = self._fetch_schoolyear(sess, element, now.date(), start, end)
                raw_list += more
                covered += more_covered
            logger.info("Received %d raw entries for %s", len(raw_list), account.key)
            person_id, person_type = sess.person_id, sess.person_type
            timegrid = sess.timegrid() if self.app.show_period_numbers else {}

        # Only the REST view knows about online lessons and lesson texts.
        # If that fails, the sync continues without those extras.
        extras = {}
        if self.app.fetch_online_info and person_id and person_type:
            # The REST view goes week by week, so skip the weeks past the last
            # lesson - a school year has plenty of those.
            lesson_days = []
            for r in raw_list:
                try:
                    lesson_days.append(_ymd(r.get("date")))
                except ValueError:
                    continue
            extras = fetch_lesson_extras(
                server=server,
                school=account.school,
                username=account.username,
                password=account.get_password(),
                element_id=person_id,
                element_type=person_type,
                start=min(first for first, _ in covered),
                end=max([end, *lesson_days]),
                verify_ssl=account.verify_ssl,
            )
            online_count = sum(1 for e in extras.values() if e.online)
            if online_count:
                logger.info("REST: %d lesson(s) flagged as online", online_count)

        events = [self._map_raw_to_event(r, account, extras, timegrid) for r in raw_list]

        if self.app.link_to_webuntis:
            # The canonical entry point, same shape the school search returns.
            deep_link = f"https://{server}/WebUntis/?school={account.school}"
            for ev in events:
                if ev.online and not ev.meeting_url:
                    ev.source_url = deep_link
        self._link_moved_lessons(events)
        events = [e for e in events if self._filter_event(e, account)]
        if not account.include_cancelled:
            events = [e for e in events if e.status != "cancelled"]
        events.sort(key=lambda e: (e.start, e.subject))
        if self.app.merge_consecutive:
            events = self._merge_consecutive(events)
        logger.info("Result: %d events for %s", len(events), account.key)
        return FetchResult(events=events, covered=covered, schoolyear=schoolyear)

    def _fetch_schoolyear(
        self,
        sess: DirectUntisSession,
        element: dict[str, Any] | None,
        today: date,
        start: date,
        end: date,
    ) -> tuple[list[dict[str, Any]], list[tuple[date, date]]]:
        """The school year around the window, block by block.

        Walks outwards from the window and stops at the first refused block:
        a school that limits how far students may look refuses everything
        beyond that point too. Only blocks that came back count as covered.
        """
        try:
            year = pick_schoolyear(sess.schoolyears(), today)
        except (UntisError, requests.RequestException) as e:
            logger.warning("School year unknown, fetching the window only: %s", e)
            return [], []
        if not year:
            logger.info("No current school year in WebUntis, fetching the window only")
            return [], []

        # Without the archive the past is dropped anyway; no point fetching it.
        first = year[0] if self.app.archive else start
        day = timedelta(days=1)
        raw: list[dict[str, Any]] = []
        covered: list[tuple[date, date]] = []
        for blocks in (chunks(first, start - day, backwards=True), chunks(end + day, year[1])):
            for lo, hi in blocks:
                try:
                    raw += sess.timetable(start=lo, end=hi, element=element)
                except (UntisError, requests.RequestException) as e:
                    logger.info("Timetable from %s not available, stopping there: %s", lo, e)
                    break
                covered.append((lo, hi))
        if covered:
            logger.info(
                "School year: %s to %s fetched",
                min(lo for lo, _ in covered),
                max(hi for _, hi in covered),
            )
        return raw, covered

    def _element_kwargs(self, account: AccountConfig) -> dict[str, Any] | None:
        """Element for getTimetable. None -> the logged-in user is used."""
        el = account.element
        if not el or not el.type:
            return None
        type_id = ELEMENT_TYPES.get(el.type)
        if not type_id:
            logger.warning("Unknown element.type '%s' - ignored", el.type)
            return None
        if el.id is None:
            logger.warning(
                "element.type '%s' set but no element.id - "
                "the JSON-RPC API needs a numeric id. Ignoring.",
                el.type,
            )
            return None
        return {"id": el.id, "type": type_id}

    def _map_raw_to_event(
        self,
        r: dict[str, Any],
        account: AccountConfig,
        extras: dict[str, Any] | None = None,
        timegrid: dict[int, str] | None = None,
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

        # code: "cancelled" = dropped, "irregular" = substitution/change
        code = str(r.get("code", "") or "").lower()
        if r.get("cancelled") or code in {"cancelled", "canceled", "cancel"}:
            status = "cancelled"
        elif code == "irregular":
            # "irregular" only means "something deviates". What exactly, only
            # the REST view reveals (moved vs. substituted).
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
        color = None
        substitutions: list[tuple] = []
        extra = (extras or {}).get(source_id)
        if extra is not None:
            online = extra.online
            meeting_url = extra.meeting_url
            note_parts.extend(extra.texts)
            substitutions = list(extra.substitutions)
            moved_from = self._slot_to_dt(extra.moved_from, tz)
            moved_to = self._slot_to_dt(extra.moved_to, tz)
            if account.color_map.get(subject) is None:
                color = extra.color
            # Some logins (class logins in particular) get no teacher through
            # JSON-RPC, although the web frontend shows one.
            if not teachers and extra.teachers:
                teachers = list(extra.teachers)
                teachers_long = list(extra.teachers_long)

            if extra.cell_state == "SHIFT" or moved_from:
                status = "moved"
            elif substitutions and status == "scheduled":
                status = "substitution"

        notes = " | ".join(dict.fromkeys(note_parts)) or None

        # Anchor the UID to the Untis period id: if a lesson moves (room,
        # time, substitution) it stays the same calendar entry and gets updated
        # instead of duplicated.
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
            color=color,
            periods=self._periods_for(r, timegrid),
        )

    @staticmethod
    def _periods_for(r: dict[str, Any], timegrid: dict[int, str] | None) -> list[str]:
        """Period name for a start time, when the timegrid is known."""
        if not timegrid:
            return []
        start = r.get("startTime")
        if start is None:
            return []
        name = timegrid.get(int(start))
        return [name] if name else []

    @staticmethod
    def _slot_to_dt(slot: tuple | None, tz: str) -> datetime | None:
        """(20260922, 1700) -> datetime 2026-09-22 17:00 in the target zone."""
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
        """Link a cancelled source lesson to where it now takes place.

        The REST view omits cancelled lessons but does know, for the moved
        lesson, where it originally sat. That lets the reverse direction be
        filled in, so the cancelled slot can say where the lesson went.
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
        """Merge back-to-back identical lessons into a single block.

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
                for name in ev.periods:
                    if name not in prev.periods:
                        prev.periods.append(name)
                continue
            merged.append(ev)
        return merged

    def _filter_event(self, ev: LessonEvent, account: AccountConfig) -> bool:
        inc = account.filters.include_subjects
        exc = account.filters.exclude_subjects
        if inc and ev.subject not in inc:
            return False
        return not (exc and ev.subject in exc)
