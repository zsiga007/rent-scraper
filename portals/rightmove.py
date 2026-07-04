from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from datetime import date
from typing import Any
from urllib.parse import urlencode

import httpx

from filters import SearchFilters
from models import Listing

_SEARCH_BASE = "https://www.rightmove.co.uk/property-to-rent/find.html"
_DETAIL_BASE = "https://www.rightmove.co.uk/properties"
_PAGE_SIZE = 24
_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


# ── URL builder ──────────────────────────────────────────────────────────────


def _display_location_id(location_name: str) -> str:
    return location_name.replace(" ", "-") + ".html"


def build_search_url(
    filters: SearchFilters, location_id: str, location_name: str, index: int = 0
) -> str:
    dont_show: list[str] = []
    if filters.exclude_student:
        dont_show.append("student")
    if filters.exclude_retirement:
        dont_show.append("retirement")
    if filters.exclude_house_share:
        dont_show.append("houseShare")

    params: dict[str, str] = {
        "searchLocation": location_name,
        "useLocationIdentifier": "true",
        "locationIdentifier": location_id,
        "rent": "To rent",
        "radius": str(filters.radius_miles),
        "maxBedrooms": str(filters.max_beds),
        "maxPrice": str(filters.max_price_pcm),
        "_includeLetAgreed": "on",
    }
    if filters.min_beds is not None:
        params["minBedrooms"] = str(filters.min_beds)
    if filters.min_price_pcm is not None:
        params["minPrice"] = str(filters.min_price_pcm)
    if dont_show:
        params["dontShow"] = ",".join(dont_show)
    if filters.furnished_only:
        params["furnishTypes"] = "furnished"
    params["sortType"] = "6"
    params["channel"] = "RENT"
    params["transactionType"] = "LETTING"
    params["displayLocationIdentifier"] = _display_location_id(location_name)
    params["index"] = str(index)
    return f"{_SEARCH_BASE}?{urlencode(params)}"


# ── Parsing helpers ───────────────────────────────────────────────────────────


def _get_next_data(html: str) -> dict[str, Any]:
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        raise ValueError("__NEXT_DATA__ not found in search page")
    data: dict[str, Any] = json.loads(m.group(1))
    return data


def _deref(flat: list[Any], idx: int) -> Any:
    """Dereference one flat-array index, recursively resolving dicts and lists.

    Only integers that appear *as values inside a dict or list* are treated as
    index references.  An integer returned directly from flat[idx] is a literal
    (e.g. deposit amount, bedroom count) and is returned as-is.
    """
    val = flat[idx]
    if isinstance(val, dict):
        out: dict[str, Any] = {}
        for k, v in val.items():
            if isinstance(v, int):
                out[k] = _deref(flat, v)
            elif isinstance(v, list):
                out[k] = [_deref(flat, x) if isinstance(x, int) else x for x in v]
            else:
                out[k] = v
        return out
    if isinstance(val, list):
        return [_deref(flat, x) if isinstance(x, int) else x for x in val]
    return val  # literal: str, int, float, bool, None


def _get_page_model(html: str) -> dict[str, Any]:
    m = re.search(r"window\.__PAGE_MODEL\s*=\s*(\{.*?\});\s*\n", html, re.DOTALL)
    if not m:
        raise ValueError("__PAGE_MODEL not found in listing page")
    wrapper: dict[str, Any] = json.loads(m.group(1))
    flat: list[Any] = json.loads(wrapper["data"])
    schema: dict[str, int] = flat[0]
    return {k: _deref(flat, v) for k, v in schema.items()}


def _parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _parse_uk_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        parts = s.split("/")
        return date(int(parts[2]), int(parts[1]), int(parts[0]))
    except (ValueError, IndexError):
        return None


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()


# ── Search result → basic Listing ────────────────────────────────────────────


def _price_pcm(price_block: dict[str, Any]) -> int | None:
    """price.amount is in whatever unit price.frequency specifies (often
    "weekly", not "monthly") — read the canonical pcm figure straight from
    displayPrices rather than assuming amount is already monthly."""
    for dp in price_block.get("displayPrices", []):
        text = dp.get("displayPrice", "")
        if text.endswith("pcm"):
            digits = re.sub(r"[^\d]", "", text)
            if digits:
                return int(digits)

    amount = price_block.get("amount")
    if amount is None:
        return None
    if price_block.get("frequency") == "weekly":
        return round(float(amount) * 52 / 12)
    return int(amount)


def _listing_from_search_result(raw: dict[str, Any]) -> Listing:
    prop_id = str(raw.get("id", ""))
    url = f"{_DETAIL_BASE}/{prop_id}" if prop_id else ""

    price_block: dict[str, Any] = raw.get("price", {})
    price_pcm: int | None = _price_pcm(price_block)

    features: list[dict[str, Any]] = raw.get("keyFeatures", [])
    feature_texts = tuple(f.get("description", "") for f in features if f.get("description"))

    council_tax = next((t for t in feature_texts if "council tax" in t.lower()), None)

    return Listing(
        source="rightmove",
        url=url,
        listing_id=prop_id or None,
        address=raw.get("displayAddress") or None,
        price_pcm=price_pcm,
        beds=raw.get("bedrooms"),
        available_from=_parse_iso_date(raw.get("letAvailableDate", "")),
        key_features=feature_texts,
        council_tax=council_tax,
        description=raw.get("summary") or None,
    )


# ── Pagination ────────────────────────────────────────────────────────────────


def iter_search_results(
    client: httpx.Client,
    filters: SearchFilters,
    location_id: str,
    location_name: str,
    on_page: Callable[[int, int], None] | None = None,
) -> Iterator[Listing]:
    """Yield every listing across all result pages.

    If *on_page* is given, it's called once per fetched page with
    (current_page, total_pages) so callers can drive a progress bar.
    """
    index = 0
    total_pages: int | None = None

    while True:
        url = build_search_url(filters, location_id, location_name, index)
        resp = client.get(url)
        resp.raise_for_status()

        data = _get_next_data(resp.text)
        sr: dict[str, Any] = data["props"]["pageProps"]["searchResults"]

        if total_pages is None:
            # pagination.total is a *page* count, not a listing count — it
            # matches len(pagination.options), one entry per page, with
            # options[-1].value == the index of the final page. Confirmed
            # live: a search reporting total=42 had real listings all the
            # way out to index=984 (42 pages * 24/page).
            total_pages = int(sr["pagination"]["total"])

        if on_page is not None:
            on_page(index // _PAGE_SIZE + 1, total_pages)

        properties: list[dict[str, Any]] = sr.get("properties", [])
        for raw in properties:
            yield _listing_from_search_result(raw)

        index += _PAGE_SIZE
        if not properties or (index // _PAGE_SIZE) >= total_pages:
            break


# ── Listing detail ────────────────────────────────────────────────────────────


def fetch_listing_detail(client: httpx.Client, listing: Listing) -> Listing:
    """Fetch deposit, furnish type, full description from the property page."""
    resp = client.get(listing.url)
    resp.raise_for_status()

    model = _get_page_model(resp.text)
    pd: dict[str, Any] = model.get("propertyData", {})

    lettings_raw = pd.get("lettings", {})
    lettings: dict[str, Any] = lettings_raw if isinstance(lettings_raw, dict) else {}
    deposit_raw = lettings.get("deposit")
    furnish_type = lettings.get("furnishType") or None
    available_from = (
        _parse_uk_date(str(lettings.get("letAvailableDate", ""))) or listing.available_from
    )

    text_raw = pd.get("text", {})
    text: dict[str, Any] = text_raw if isinstance(text_raw, dict) else {}
    description = _strip_html(str(text.get("description", ""))) or listing.description

    features_raw: list[Any] = pd.get("keyFeatures", [])
    feature_texts = (
        tuple(
            str(f.get("description", "")) if isinstance(f, dict) else str(f)
            for f in features_raw
            if f
        )
        or listing.key_features
    )
    council_tax = next(
        (t for t in feature_texts if "council tax" in t.lower()),
        listing.council_tax,
    )

    return Listing(
        source=listing.source,
        url=listing.url,
        listing_id=listing.listing_id,
        address=listing.address,
        price_pcm=listing.price_pcm,
        beds=listing.beds,
        available_from=available_from,
        deposit=int(deposit_raw) if isinstance(deposit_raw, (int, float)) else None,
        furnish_type=furnish_type,
        council_tax=council_tax,
        key_features=feature_texts,
        description=description,
    )
