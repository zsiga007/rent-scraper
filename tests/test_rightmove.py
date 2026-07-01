from __future__ import annotations

import json
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from filters import DateWindow, SearchFilters
from portals.rightmove import (
    _deref,
    _listing_from_search_result,
    _parse_iso_date,
    _parse_uk_date,
    _strip_html,
    build_search_url,
    iter_search_results,
)

_FILTERS = SearchFilters(
    max_beds=1,
    max_price_pcm=2250,
    radius_miles=3.0,
    available_from=DateWindow(earliest=date(2026, 8, 22), latest=date(2026, 9, 11)),
    exclude_student=True,
    exclude_retirement=True,
    exclude_house_share=True,
)


def _qs(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


# ── build_search_url ──────────────────────────────────────────────────────────


def test_build_search_url_basic_params() -> None:
    url = build_search_url(_FILTERS, "STATION^9272", "Tottenham Court Road Station")
    qs = _qs(url)
    assert qs["locationIdentifier"] == ["STATION^9272"]
    assert qs["maxBedrooms"] == ["1"]
    assert qs["maxPrice"] == ["2250"]
    assert qs["radius"] == ["3.0"]


def test_build_search_url_dont_show() -> None:
    url = build_search_url(_FILTERS, "STATION^9272", "Loc")
    qs = _qs(url)
    dont = qs["dontShow"][0].split(",")
    assert "student" in dont
    assert "retirement" in dont
    assert "houseShare" in dont


def test_build_search_url_index() -> None:
    url = build_search_url(_FILTERS, "STATION^9272", "Loc", index=24)
    assert _qs(url)["index"] == ["24"]


def test_build_search_url_omits_min_when_unset() -> None:
    url = build_search_url(_FILTERS, "STATION^9272", "Loc")
    qs = _qs(url)
    assert "minBedrooms" not in qs
    assert "minPrice" not in qs


def test_build_search_url_includes_min_when_set() -> None:
    f = SearchFilters(
        max_beds=1,
        min_beds=1,
        max_price_pcm=3000,
        min_price_pcm=700,
        radius_miles=0.0,
        available_from=_FILTERS.available_from,
    )
    qs = _qs(build_search_url(f, "STATION^9272", "Loc"))
    assert qs["minBedrooms"] == ["1"]
    assert qs["minPrice"] == ["700"]


def test_build_search_url_furnished_only_sends_furnish_types() -> None:
    # _FILTERS defaults furnished_only=True
    qs = _qs(build_search_url(_FILTERS, "STATION^9272", "Loc"))
    assert qs["furnishTypes"] == ["furnished"]


def test_build_search_url_omits_furnish_types_when_not_furnished_only() -> None:
    f = SearchFilters(
        max_beds=1,
        max_price_pcm=2250,
        radius_miles=3.0,
        available_from=_FILTERS.available_from,
        furnished_only=False,
    )
    qs = _qs(build_search_url(f, "STATION^9272", "Loc"))
    assert "furnishTypes" not in qs


# ── _parse_iso_date ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "s,expected",
    [
        ("2026-08-22T00:00:00Z", date(2026, 8, 22)),
        ("2026-08-22", date(2026, 8, 22)),
        ("2026-09-11T12:00:00+01:00", date(2026, 9, 11)),
    ],
)
def test_parse_iso_date_valid(s: str, expected: date) -> None:
    assert _parse_iso_date(s) == expected


@pytest.mark.parametrize("s", [None, "", "not-a-date", "22/08/2026"])
def test_parse_iso_date_invalid(s: str | None) -> None:
    assert _parse_iso_date(s) is None


# ── _parse_uk_date ────────────────────────────────────────────────────────────


def test_parse_uk_date_valid() -> None:
    assert _parse_uk_date("22/08/2026") == date(2026, 8, 22)
    assert _parse_uk_date("01/01/2027") == date(2027, 1, 1)


@pytest.mark.parametrize("s", [None, "", "2026-08-22"])
def test_parse_uk_date_invalid(s: str | None) -> None:
    assert _parse_uk_date(s) is None


# ── _strip_html ───────────────────────────────────────────────────────────────


def test_strip_html_removes_tags() -> None:
    # Each tag becomes a space, so check content not exact spacing
    result = _strip_html("<p><b>Hello</b> world</p>")
    assert "Hello" in result and "world" in result and "<" not in result


def test_strip_html_passthrough() -> None:
    assert _strip_html("plain text") == "plain text"


# ── _deref ────────────────────────────────────────────────────────────────────


def test_deref_literal_str() -> None:
    flat: list[Any] = ["hello"]
    assert _deref(flat, 0) == "hello"


def test_deref_literal_int() -> None:
    flat: list[Any] = [42]
    assert _deref(flat, 0) == 42


def test_deref_dict_resolves_value_indices() -> None:
    # flat[0] schema: int values are indices into flat
    flat: list[Any] = [{"price": 1, "beds": 2}, 1800, 1]
    assert _deref(flat, 0) == {"price": 1800, "beds": 1}


def test_deref_nested_dicts() -> None:
    flat: list[Any] = [{"info": 1}, {"city": 2}, "London"]
    assert _deref(flat, 0) == {"info": {"city": "London"}}


def test_deref_list_value() -> None:
    # dict with a list value whose int elements are indices
    flat: list[Any] = [{"tags": 1}, [2, 3], "foo", "bar"]
    assert _deref(flat, 0) == {"tags": ["foo", "bar"]}


# ── _listing_from_search_result ───────────────────────────────────────────────


def test_listing_from_search_result_basic() -> None:
    raw: dict[str, Any] = {
        "id": 12345,
        "displayAddress": "Flat 1, London W1",
        "price": {"amount": 1800},
        "bedrooms": 1,
        "letAvailableDate": "2026-08-22T00:00:00Z",
        "summary": "A great flat.",
        "keyFeatures": [],
    }
    lst = _listing_from_search_result(raw)
    assert lst.source == "rightmove"
    assert lst.listing_id == "12345"
    assert lst.url == "https://www.rightmove.co.uk/properties/12345"
    assert lst.price_pcm == 1800
    assert lst.beds == 1
    assert lst.available_from == date(2026, 8, 22)
    assert lst.address == "Flat 1, London W1"
    assert lst.description == "A great flat."


def test_listing_from_search_result_studio_zero_beds_not_lost() -> None:
    # bedrooms=0 (studio) is a real, valid value — must not collapse to None.
    raw: dict[str, Any] = {
        "id": 1,
        "displayAddress": "Studio Flat",
        "price": {"amount": 1500},
        "bedrooms": 0,
        "letAvailableDate": "2026-08-22",
        "summary": "",
        "keyFeatures": [],
    }
    lst = _listing_from_search_result(raw)
    assert lst.beds == 0


def test_listing_from_search_result_weekly_price_converts_to_pcm() -> None:
    raw: dict[str, Any] = {
        "id": 1,
        "displayAddress": "Flat 1",
        "price": {
            "amount": 450,
            "frequency": "weekly",
            "displayPrices": [
                {"displayPrice": "£1,950 pcm", "displayPriceQualifier": ""},
                {"displayPrice": "£450 pw", "displayPriceQualifier": ""},
            ],
        },
        "bedrooms": 1,
        "letAvailableDate": "2026-08-22",
        "summary": "",
        "keyFeatures": [],
    }
    lst = _listing_from_search_result(raw)
    assert lst.price_pcm == 1950


def test_listing_from_search_result_weekly_price_without_display_prices() -> None:
    raw: dict[str, Any] = {
        "id": 1,
        "displayAddress": "Flat 1",
        "price": {"amount": 450, "frequency": "weekly"},
        "bedrooms": 1,
        "letAvailableDate": "2026-08-22",
        "summary": "",
        "keyFeatures": [],
    }
    lst = _listing_from_search_result(raw)
    assert lst.price_pcm == round(450 * 52 / 12)


def test_listing_from_search_result_council_tax() -> None:
    raw: dict[str, Any] = {
        "id": 99,
        "displayAddress": "Flat 2",
        "price": {"amount": 2000},
        "bedrooms": 1,
        "letAvailableDate": "2026-09-01",
        "summary": "",
        "keyFeatures": [
            {"description": "Council Tax Band B"},
            {"description": "Double glazing"},
        ],
    }
    lst = _listing_from_search_result(raw)
    assert lst.council_tax == "Council Tax Band B"
    assert "Double glazing" in lst.key_features


# ── iter_search_results ───────────────────────────────────────────────────────


def _next_data_html(properties: list[dict[str, Any]], total: int) -> str:
    payload = {
        "props": {
            "pageProps": {
                "searchResults": {
                    "properties": properties,
                    "pagination": {"total": total},
                }
            }
        }
    }
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'


def test_iter_search_results_paginates_through_all_pages() -> None:
    # pagination.total is a *page* count, not a listing count (confirmed
    # live: a search reporting total=42 had real distinct listings all the
    # way to index=984 — i.e. 42 pages of up to 24 each, not 42 listings).
    # The iterator must keep paginating until index // _PAGE_SIZE >= total.
    pages = {0: [{"id": i} for i in range(24)], 24: [{"id": i} for i in range(24, 48)]}
    requested_indices: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        index = int(request.url.params.get("index", "0"))
        requested_indices.append(index)
        return httpx.Response(200, text=_next_data_html(pages.get(index, []), total=2))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    listings = list(iter_search_results(client, _FILTERS, "STATION^9272", "Loc"))

    assert len(listings) == 48
    assert requested_indices == [0, 24]


def test_iter_search_results_stops_when_properties_empty() -> None:
    # Defends against a bad/inflated pagination.total: if a page comes back
    # empty, stop regardless of what total_pages claims.
    def handler(request: httpx.Request) -> httpx.Response:
        index = int(request.url.params.get("index", "0"))
        properties = [{"id": 1}] if index == 0 else []
        return httpx.Response(200, text=_next_data_html(properties, total=99))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    listings = list(iter_search_results(client, _FILTERS, "STATION^9272", "Loc"))

    assert len(listings) == 1
