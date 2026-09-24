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
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path

from .models import LessonEvent

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


def load(path: Path) -> dict[str, dict]:
    """Read the store. A missing or damaged file yields an empty archive."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Archive %s unreadable (%s) - starting empty", path, exc)
        return {}

    if raw.get("version") != SCHEMA_VERSION:
        logger.warning(
            "Archive %s has version %s, expected %s - starting empty",
            path,
            raw.get("version"),
            SCHEMA_VERSION,
        )
        return {}
    events = raw.get("events")
    return events if isinstance(events, dict) else {}


def save(path: Path, store: dict[str, dict]) -> None:
    """Write the store atomically so an interrupted run cannot truncate it."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {"version": SCHEMA_VERSION, "events": store}
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


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
) -> list[LessonEvent]:
    """Merge fresh lessons with the archive and return everything to publish.

    The freshly fetched lessons always win for their own UIDs, so a lesson
    that changed since it was archived is corrected rather than duplicated.
    """
    path = store_path(out_dir, file_name)
    store = load(path)
    before = len(store)
    merge(store, fresh)
    removed = prune(store, retention_days)
    save(path, store)

    logger.info(
        "Archive %s: %d entries (+%d new, -%d pruned)",
        path.name,
        len(store),
        len(store) - before + removed,
        removed,
    )
    events = to_events(store)
    events.sort(key=lambda e: (e.start, e.subject))
    return events
