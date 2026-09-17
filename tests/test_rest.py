"""Enrichment through the REST view (online lessons, lesson texts)."""

from datetime import date

from untis_calendar.untis_rest import _clean_url, _find_url_in_text, _mondays


def test_placeholder_urls_are_rejected():
    """WebUntis returns '0' instead of a URL when no link is stored."""
    for junk in ("0", "", "-", "null", "none", None):
        assert _clean_url(junk) is None


def test_non_http_values_rejected():
    assert _clean_url("meet.example.org") is None
    assert _clean_url("javascript:alert(1)") is None


def test_real_url_accepted():
    assert _clean_url("  https://meet.example.org/x  ") == "https://meet.example.org/x"


def test_url_found_in_lesson_text():
    """Many teachers simply paste the link into the lesson text."""
    got = _find_url_in_text("", "Bitte via https://meet.example.org/raum1 beitreten")
    assert got == "https://meet.example.org/raum1"


def test_trailing_punctuation_stripped():
    assert _find_url_in_text("Link: https://meet.example.org/x.") == "https://meet.example.org/x"


def test_no_url_returns_none():
    assert _find_url_in_text("kein Link hier", None, "") is None


def test_mondays_covers_range():
    got = list(_mondays(date(2026, 9, 9), date(2026, 9, 23)))
    assert got == [date(2026, 9, 7), date(2026, 9, 14), date(2026, 9, 21)]


def test_mondays_single_week():
    assert list(_mondays(date(2026, 9, 8), date(2026, 9, 10))) == [date(2026, 9, 7)]
