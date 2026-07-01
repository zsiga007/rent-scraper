from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from filters import DateWindow, SearchFilters
from portals.zoopla import (
    _DEPOSIT_PAT,
    _extract_detail_data,
    _extract_search_data,
    _listing_from_rsc,
    _parse_zoopla_date,
    _strip_html,
    build_search_url,
)

_FILTERS = SearchFilters(
    max_beds=1,
    max_price_pcm=2250,
    radius_miles=3.0,
    available_from=DateWindow(earliest=date(2026, 8, 22), latest=date(2026, 9, 11)),
    furnished_only=True,
    exclude_student=True,
    exclude_retirement=True,
    exclude_house_share=True,
)

_LOCATION = "Tottenham Court Road Station, London"


def _rsc(content: str) -> str:
    """Minimal HTML containing a single RSC push chunk with *content* as the payload."""
    return f"self.__next_f.push([1,{json.dumps(content)}]);"


def _qs(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


# ── build_search_url ──────────────────────────────────────────────────────────


def test_build_search_url_structure() -> None:
    url = build_search_url(_FILTERS, _LOCATION)
    assert "/to-rent/property/1-bedroom/station/tube/tottenham-court-road/" in url


def test_build_search_url_params() -> None:
    url = build_search_url(_FILTERS, _LOCATION)
    qs = _qs(url)
    assert qs.get("furnished_state") == ["furnished"]
    assert qs.get("price_max") == ["2250"]
    assert qs.get("is_student_accommodation") == ["false"]
    assert qs.get("is_retirement_home") == ["false"]
    assert qs.get("is_shared_accommodation") == ["false"]


def test_build_search_url_no_pn_on_page_1() -> None:
    url = build_search_url(_FILTERS, _LOCATION, page=1)
    assert "pn=" not in url


def test_build_search_url_pn_on_later_pages() -> None:
    url = build_search_url(_FILTERS, _LOCATION, page=3)
    assert _qs(url).get("pn") == ["3"]


def test_build_search_url_omits_price_min_when_unset() -> None:
    assert "price_min" not in _qs(build_search_url(_FILTERS, _LOCATION))


def test_build_search_url_includes_price_min_when_set() -> None:
    f = SearchFilters(
        max_beds=1,
        max_price_pcm=2000,
        min_price_pcm=1250,
        radius_miles=3.0,
        available_from=_FILTERS.available_from,
    )
    qs = _qs(build_search_url(f, _LOCATION))
    assert qs["price_min"] == ["1250"]
    assert qs["price_max"] == ["2000"]


# ── _parse_zoopla_date ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "s,expected",
    [
        ("22nd Jul 2026", date(2026, 7, 22)),
        ("1st Jan 2027", date(2027, 1, 1)),
        ("3rd Aug 2026", date(2026, 8, 3)),
        ("2nd Sep 2026", date(2026, 9, 2)),
        ("22 July 2026", date(2026, 7, 22)),  # long month, no ordinal suffix
        ("1 August 2026", date(2026, 8, 1)),
    ],
)
def test_parse_zoopla_date_valid(s: str, expected: date) -> None:
    assert _parse_zoopla_date(s) == expected


@pytest.mark.parametrize("s", [None, "", "immediately", "ASAP", "unknown date"])
def test_parse_zoopla_date_invalid(s: str | None) -> None:
    assert _parse_zoopla_date(s) is None


# ── _strip_html ───────────────────────────────────────────────────────────────


def test_strip_html_br_becomes_newline() -> None:
    assert "\n" in _strip_html("line1<br>line2")


def test_strip_html_removes_tags() -> None:
    assert _strip_html("<p><strong>Hello</strong></p>") == "Hello"


# ── _DEPOSIT_PAT ─────────────────────────────────────────────────────────────


def test_deposit_pat_basic() -> None:
    m = _DEPOSIT_PAT.search("Deposit: £1,800")
    assert m is not None
    assert m.group(1) == "1,800"


def test_deposit_pat_with_bond() -> None:
    m = _DEPOSIT_PAT.search("Deposit / Bond: £2,100")
    assert m is not None
    assert m.group(1) == "2,100"


def test_deposit_pat_rejects_boilerplate() -> None:
    text = "Security Deposit (per tenancy. Rent under £50,000 per year): £1,800"
    assert _DEPOSIT_PAT.search(text) is None


# ── _listing_from_rsc ─────────────────────────────────────────────────────────


def test_listing_from_rsc_basic() -> None:
    raw: dict[str, Any] = {
        "listingId": "12345",
        "listingUris": {"detail": "/to-rent/details/12345/"},
        "address": "Flat 1, London N1",
        "priceUnformatted": 1800,
        "availableFrom": "22nd Aug 2026",
        "summaryDescription": "A nice flat.",
        "features": [{"iconId": "bed", "content": 1}],
    }
    lst = _listing_from_rsc(raw)
    assert lst.source == "zoopla"
    assert lst.listing_id == "12345"
    assert lst.url == "https://www.zoopla.co.uk/to-rent/details/12345/"
    assert lst.price_pcm == 1800
    assert lst.beds == 1
    assert lst.available_from == date(2026, 8, 22)
    assert lst.address == "Flat 1, London N1"
    assert lst.description == "A nice flat."


def test_listing_from_rsc_immediately() -> None:
    raw: dict[str, Any] = {
        "listingId": "99",
        "listingUris": {"detail": "/to-rent/99/"},
        "priceUnformatted": 1500,
        "availableFrom": "immediately",
        "features": [],
    }
    lst = _listing_from_rsc(raw)
    assert lst.available_from == date.today() + timedelta(days=1)


# ── _extract_search_data ──────────────────────────────────────────────────────


def test_extract_search_data_parses_listings() -> None:
    # Use compact separators — the pagination regex requires no space between "pagination":{
    inner = json.dumps(
        {
            "regularListingsFormatted": [{"listingId": "abc"}, {"listingId": "def"}],
            "pagination": {"pageNumberMax": 3},
        },
        separators=(",", ":"),
    )
    listings, pagination = _extract_search_data(_rsc(inner))
    assert len(listings) == 2
    assert listings[0]["listingId"] == "abc"
    assert pagination["pageNumberMax"] == 3


def test_extract_search_data_no_match() -> None:
    listings, pagination = _extract_search_data("no rsc chunks here")
    assert listings == []
    assert pagination == {}


# ── _extract_detail_data ──────────────────────────────────────────────────────


def test_extract_detail_data_furnish_type() -> None:
    html = _rsc('{"furnishedState":"furnished"}')
    furnish, deposit, desc, ctax = _extract_detail_data(html)
    assert furnish == "Furnished"


def test_extract_detail_data_deposit() -> None:
    html = _rsc("Deposit: £1,800")
    _, deposit, _, _ = _extract_detail_data(html)
    assert deposit == 1800


def test_extract_detail_data_description_from_ld_json() -> None:
    ld = json.dumps({"@type": "RealEstateListing", "description": "A lovely flat."})
    html = f'<script type="application/ld+json">{ld}</script>'
    _, _, desc, _ = _extract_detail_data(html)
    assert desc == "A lovely flat."
