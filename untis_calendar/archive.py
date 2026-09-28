"""Keep lessons that WebUntis no longer returns.

The fetch window is deliberately small - asking WebUntis for a whole year on
every refresh is wasteful and slow. But that means anything older than
``window_days_before`` silently disappears from the feed, and with it the
answer to "where was I on that Tuesday in March".

So every lesson ever seen is written to a small JSON store next to the
generated calendar and merged back in on the next run. Entries are keyed by
the event UID, which is anchored to the WebUntis period id: a lesson that
later moves or gets cancelled updates its archived copy instead of producing
a duplicate.

The same store holds the rest of the school year between its (rarer)
fetches. For the days a fetch covered, WebUntis is authoritative: a stored
lesson it no longer returns was removed - for example when the school
publishes a new timetable version with new period ids - and is dropped.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import AppConfig
from .models import FetchResult, LessonEvent

logger = logging.getLogger(__name__)

# Bumped only if the stored shape changes in a way old files cannot satisfy.
SCHEMA_VERSION = 1

_DATETIME_FIELDS = ("start", "end", "moved_from", "moved_to")


def store_path(out_dir: Path, file_name: str) -> Path:
    """Archive file belonging to a calendar file: a.ics -> a.archive.json."""
    return out_dir / (Path(file_name).stem + ".archive.json")


def _event_to_dict(e: LessonEvent) -> dict:
    d = dict(e.__dict__)
    for key in _DATETIME_FIELDS:
        value = d.get(key)
        d[key] = value.isoformat() if isinstance(value, datetime) else None
    # Tuples do not survive a JSON round trip; keep them as lists.
    d["substitutions"] = [list(s) for s in (d.get("substitutions") or [])]
    return d


def _event_from_dict(d: dict) -> LessonEvent | None:
    try:
        data = dict(d)
        for key in _DATETIME_FIELDS:
            raw = data.get(key)
            data[key] = datetime.fromisoformat(raw) if raw else None
        data["substitutions"] = [tuple(s) for s in (data.get("substitutions") or [])]
        # Drop unknown keys so an older store still loads after a model change.
        allowed = set(LessonEvent.__dataclass_fields__)
        return LessonEvent(**{k: v for k, v in data.items() if k in allowed})
    except Exception as exc:
        logger.warning("Skipping unreadable archive entry: %s", exc)
        return None


def _read(path: Path) -> dict:
    """The whole stored payload. Missing, damaged or foreign -> empty."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Archive %s unreadable (%s) - starting empty", path, exc)
        return {}

    if not isinstance(raw, dict) or raw.get("version") != SCHEMA_VERSION:
        logger.warning(
            "Archive %s has version %s, expected %s - starting empty",
            path,
            raw.get("version") if isinstance(raw, dict) else None,
            SCHEMA_VERSION,
        )
        return {}
    return raw


def load(path: Path) -> dict[str, dict]:
    """Read the store. A missing or damaged file yields an empty archive."""
    events = _read(path).get("events")
    return events if isinstance(events, dict) else {}


def save(path: Path, store: dict[str, dict], schoolyear_fetched_at: str | None = None) -> None:
    """Write the store atomically so an interrupted run cannot truncate it."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload: dict = {"version": SCHEMA_VERSION, "events": store}
    if schoolyear_fetched_at:
        payload["schoolyear_fetched_at"] = schoolyear_fetched_at
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def schoolyear_due(out_dir: Path, file_name: str, hours: int, now: datetime | None = None) -> bool:
    """Is it time to fetch the whole school year again?"""
    stamp = _read(store_path(out_dir, file_name)).get("schoolyear_fetched_at")
    try:
        last = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return True
    return (now or datetime.now(timezone.utc)) - last >= timedelta(hours=hours)


def _start_day(raw: dict) -> date | None:
    try:
        return datetime.fromisoformat(raw["start"]).date()
    except (KeyError, TypeError, ValueError):
        return None


def forget_removed(
    store: dict[str, dict],
    fresh: Iterable[LessonEvent],
    covered: Iterable[tuple[date, date]],
) -> int:
    """Drop stored lessons inside the covered days that WebUntis no longer has."""
    ranges = list(covered)
    if not ranges:
        return 0
    keep = {e.uid for e in fresh}
    gone = []
    for uid, raw in store.items():
        day = _start_day(raw)
        if uid not in keep and day and any(lo <= day <= hi for lo, hi in ranges):
            gone.append(uid)
    for uid in gone:
        del store[uid]
    return len(gone)


def trim(store: dict[str, dict], keep_from: date | None, keep_until: date | None) -> int:
    """Drop lessons outside keep_from..keep_until (either end may be open)."""
    gone = []
    for uid, raw in store.items():
        day = _start_day(raw)
        if day is None:
            continue
        if (keep_from and day < keep_from) or (keep_until and day > keep_until):
            gone.append(uid)
    for uid in gone:
        del store[uid]
    return len(gone)


def merge(store: dict[str, dict], events: Iterable[LessonEvent]) -> int:
    """Fold freshly fetched lessons into the store. Returns the new count."""
    added = 0
    for e in events:
        if e.uid not in store:
            added += 1
        store[e.uid] = _event_to_dict(e)
    return added


def prune(store: dict[str, dict], retention_days: int, today: date | None = None) -> int:
    """Drop entries older than the retention. 0 keeps everything."""
    if retention_days <= 0:
        return 0
    cutoff = (today or date.today()) - timedelta(days=retention_days)
    stale = []
    for uid, raw in store.items():
        start = raw.get("start")
        if not start:
            continue
        try:
            if datetime.fromisoformat(start).date() < cutoff:
                stale.append(uid)
        except ValueError:
            stale.append(uid)
    for uid in stale:
        del store[uid]
    return len(stale)


def to_events(store: dict[str, dict]) -> list[LessonEvent]:
    events = [_event_from_dict(d) for d in store.values()]
    return [e for e in events if e is not None]


def combine(
    out_dir: Path,
    file_name: str,
    fresh: list[LessonEvent],
    retention_days: int = 0,
    covered: Iterable[tuple[date, date]] = (),
    keep_from: date | None = None,
    keep_until: date | None = None,
    schoolyear_fetched: bool = False,
) -> list[LessonEvent]:
    """Merge fresh lessons with the archive and return everything to publish.

    The freshly fetched lessons always win for their own UIDs, so a lesson
    that changed since it was archived is corrected rather than duplicated.
    Within ``covered`` they are the whole truth, see forget_removed().
    """
    path = store_path(out_dir, file_name)
    raw = _read(path)
    store = raw.get("events") if isinstance(raw.get("events"), dict) else {}
    stamp = raw.get("schoolyear_fetched_at")
    if schoolyear_fetched:
        stamp = datetime.now(timezone.utc).isoformat()

    removed = forget_removed(store, fresh, covered)
    added = merge(store, fresh)
    pruned = prune(store, retention_days) + trim(store, keep_from, keep_until)
    save(path, store, stamp)

    logger.info(
        "Archive %s: %d entries (+%d new, -%d removed in WebUntis, -%d pruned)",
        path.name,
        len(store),
        added,
        removed,
        pruned,
    )
    events = to_events(store)
    events.sort(key=lambda e: (e.start, e.subject))
    return events


def update(
    out_dir: Path,
    file_name: str,
    result: FetchResult,
    app: AppConfig,
    today: date | None = None,
) -> list[LessonEvent]:
    """Store a fetch result and return everything to publish, per the config.

    Without ``archive`` the past beyond the window is dropped; without
    ``fetch_schoolyear`` everything beyond the window is, so switching it off
    cannot leave months of stale lessons behind.
    """
    today = today or date.today()
    return combine(
        out_dir,
        file_name,
        result.events,
        retention_days=app.archive_retention_days if app.archive else 0,
        covered=result.covered,
        keep_from=None if app.archive else today - timedelta(days=app.window_days_before),
        keep_until=None if app.fetch_schoolyear else today + timedelta(days=app.window_days_after),
        schoolyear_fetched=result.schoolyear,
    )
