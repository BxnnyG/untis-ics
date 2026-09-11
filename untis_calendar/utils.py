from __future__ import annotations

import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo


def tz_aware(dt: datetime, tzname: str) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo(tzname))
    return dt.astimezone(ZoneInfo(tzname))


def stable_uid(*parts: str) -> str:
    raw = "|".join(parts)
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"{h}@untis-calendar"
