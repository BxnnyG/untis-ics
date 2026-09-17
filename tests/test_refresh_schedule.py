"""Adaptive refresh interval: frequent by day, rare at night."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from untis_calendar.config import AppConfig
from untis_calendar.server import compute_interval_minutes


def cfg(**over):
    return AppConfig(**over)


def test_active_hours_use_short_interval():
    a = cfg(
        refresh_interval_minutes=15,
        refresh_idle_minutes=120,
        active_hours_start=6,
        active_hours_end=22,
    )
    assert compute_interval_minutes(a, datetime(2026, 9, 15, 7)) == 15
    assert compute_interval_minutes(a, datetime(2026, 9, 15, 21)) == 15


def test_night_uses_long_interval():
    """A timetable does not change at 3am."""
    a = cfg(
        refresh_interval_minutes=15,
        refresh_idle_minutes=120,
        active_hours_start=6,
        active_hours_end=22,
    )
    assert compute_interval_minutes(a, datetime(2026, 9, 15, 3)) == 120
    assert compute_interval_minutes(a, datetime(2026, 9, 15, 23)) == 120


def test_boundaries_are_inclusive_start_exclusive_end():
    a = cfg(
        refresh_interval_minutes=15,
        refresh_idle_minutes=120,
        active_hours_start=6,
        active_hours_end=22,
    )
    assert compute_interval_minutes(a, datetime(2026, 9, 15, 6)) == 15
    assert compute_interval_minutes(a, datetime(2026, 9, 15, 22)) == 120


def test_window_across_midnight():
    """Night-shift window, 22 to 6."""
    a = cfg(
        refresh_interval_minutes=10,
        refresh_idle_minutes=90,
        active_hours_start=22,
        active_hours_end=6,
    )
    assert compute_interval_minutes(a, datetime(2026, 9, 15, 23)) == 10
    assert compute_interval_minutes(a, datetime(2026, 9, 15, 2)) == 10
    assert compute_interval_minutes(a, datetime(2026, 9, 15, 12)) == 90


def test_idle_zero_disables_adaptive_behaviour():
    a = cfg(refresh_interval_minutes=20, refresh_idle_minutes=0)
    assert compute_interval_minutes(a, datetime(2026, 9, 15, 3)) == 20


def test_invalid_hour_rejected():
    with pytest.raises(ValidationError):
        cfg(active_hours_start=24)
