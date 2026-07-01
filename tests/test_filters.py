from __future__ import annotations

from datetime import date

from filters import DateWindow, SearchFilters

_WINDOW = DateWindow(earliest=date(2026, 8, 22), latest=date(2026, 9, 11))


def test_contains_earliest_boundary() -> None:
    assert _WINDOW.contains(date(2026, 8, 22))


def test_contains_latest_boundary() -> None:
    assert _WINDOW.contains(date(2026, 9, 11))


def test_contains_middle() -> None:
    assert _WINDOW.contains(date(2026, 9, 1))


def test_rejects_before() -> None:
    assert not _WINDOW.contains(date(2026, 8, 21))


def test_rejects_after() -> None:
    assert not _WINDOW.contains(date(2026, 9, 12))


def test_rejects_none() -> None:
    assert not _WINDOW.contains(None)


def test_search_filters_defaults() -> None:
    f = SearchFilters(max_beds=1, max_price_pcm=2000, radius_miles=1.0, available_from=_WINDOW)
    assert f.exclude_student is True
    assert f.exclude_retirement is True
    assert f.exclude_house_share is True
    assert f.furnished_only is True
