from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlencode

from playwright.sync_api import Browser, BrowserContext

from filters import SearchFilters
from models import Listing

_BASE = "https://www.zoopla.co.uk/to-rent/property"
_DETAIL_BASE = "https://www.zoopla.co.uk"

_MONTHS: dict[str, int] = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}

_FURNISH_LABELS: dict[str, str] = {
    "furnished": "Furnished",
    "part_furnished": "Part furnished",
    "part furnished": "Part furnished",
    "unfurnished": "Unfurnished",
}


# ── URL builder ───────────────────────────────────────────────────────────────


def _bedroom_segment(max_beds: int) -> str:
    if max_beds == 0:
        return "studio"
    if max_beds == 1:
        return "1-bedroom"
    return f"{max_beds}-bedrooms"


def _station_slug(location_name: str) -> str:
    name = location_name.split(",")[0].strip()
    if name.lower().endswith(" station"):
        name = name[: -len(" station")]
    return name.strip().lower().replace(" ", "-")


def build_search_url(filters: SearchFilters, location_name: str, page: int = 1) -> str:
    beds = _bedroom_segment(filters.max_beds)
    slug = _station_slug(location_name)
    path = f"{_BASE}/{beds}/station/tube/{slug}/"

    params: dict[str, str] = {"duration": "1800"}
    if filters.furnished_only:
        params["furnished_state"] = "furnished"
    if filters.exclude_retirement:
        params["is_retirement_home"] = "false"
    if filters.exclude_house_share:
        params["is_shared_accommodation"] = "false"
    if filters.exclude_student:
        params["is_student_accommodation"] = "false"
    params["price_frequency"] = "per_month"
    params["price_max"] = str(filters.max_price_pcm)
    if filters.min_price_pcm is not None:
        params["price_min"] = str(filters.min_price_pcm)
    params["q"] = location_name
    params["search_source"] = "to-rent"
    params["transport_type"] = "public_transport"
    if page > 1:
        params["pn"] = str(page)

    return f"{path}?{urlencode(params)}"


# ── Parsing helpers ───────────────────────────────────────────────────────────


def _parse_zoopla_date(s: str | None) -> date | None:
    """Parse '22nd Jul 2026' or '22 July 2026' → date."""
    if not s:
        return None
    m = re.match(r"(\d+)(?:st|nd|rd|th)?\s+(\w+)\s+(\d{4})", s.strip())
    if not m:
        return None
    day, month_str, year = int(m.group(1)), m.group(2), int(m.group(3))
    month = _MONTHS.get(month_str) or _MONTHS.get(month_str[:3].capitalize())
    if not month:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    return re.sub(r"<[^>]+>", "", text).strip()


# ── RSC payload extraction ────────────────────────────────────────────────────


def _rsc_chunks(html: str) -> list[str]:
    """Return all unescaped RSC chunk strings from the page."""
    raw_list = re.findall(r'self\.__next_f\.push\(\[\d+,(".*?")\]\);?', html, re.DOTALL)
    result: list[str] = []
    for raw_str in raw_list:
        try:
            result.append(json.loads(raw_str))
        except json.JSONDecodeError:
            pass
    return result


def _extract_search_data(html: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract (listings, pagination) from a search result page."""
    for inner in _rsc_chunks(html):
        if "regularListingsFormatted" not in inner:
            continue
        pm = re.search(r'"pagination":\{([^}]+)\}', inner)
        pagination: dict[str, Any] = json.loads("{" + pm.group(1) + "}") if pm else {}

        idx = inner.find('"regularListingsFormatted":')
        start = inner.find("[", idx)
        depth = 0
        end = start
        for i, ch in enumerate(inner[start:], start):
            if ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
            if depth == 0:
                end = i
                break

        listings: list[dict[str, Any]] = json.loads(inner[start : end + 1])
        return listings, pagination
    return [], {}


# Allow short text (e.g. "/ Bond") between "Deposit" and ":", but stop at "(" to
# avoid matching government boilerplate "Deposit (per tenancy. Rent under £50,000…"
_DEPOSIT_PAT = re.compile(r"[Dd]eposit\b(?:[^(£<\n]|<[^>]*>){0,25}:\s*£([\d,]+(?:\.\d{2})?)")
_CTAX_PAT = re.compile(r"[Cc]ouncil [Tt]ax\s+[Bb]and[:\s]+([A-H][^,\n.<]*)")


def _extract_detail_data(html: str) -> tuple[str | None, int | None, str | None, str | None]:
    """Returns (furnish_type, deposit_gbp, description_text, council_tax) from a detail page."""
    furnish_type: str | None = None
    deposit: int | None = None
    description: str | None = None
    council_tax: str | None = None

    # Description from schema.org JSON-LD — there may be multiple ld+json blocks
    for ld_raw in re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL
    ):
        try:
            ld: dict[str, Any] = json.loads(ld_raw)
            if ld.get("@type") == "RealEstateListing":
                description = _strip_html(str(ld.get("description", ""))) or None
                break
        except (json.JSONDecodeError, ValueError):
            pass

    # Deposit, council tax, furnish type from RSC chunks
    # Deposit appears in description HTML blobs: "Deposit / Bond: £X" or "Deposit: £X"
    for inner in _rsc_chunks(html):
        if deposit is None and "eposit" in inner:
            dm = _DEPOSIT_PAT.search(inner)
            if dm:
                try:
                    deposit = int(float(dm.group(1).replace(",", "")))
                except ValueError:
                    pass
        if council_tax is None and "ouncil" in inner.lower():
            cm = _CTAX_PAT.search(_strip_html(inner))
            if cm:
                council_tax = cm.group(0).strip()
        if furnish_type is None:
            fm = re.search(r'"furnishedState":"([^"]+)"', inner)
            if fm:
                raw_fs = fm.group(1)
                furnish_type = _FURNISH_LABELS.get(raw_fs, raw_fs.replace("_", " ").capitalize())
        if deposit is not None and council_tax is not None and furnish_type is not None:
            break

    # Also check plain description text for deposit / council tax
    if description:
        if deposit is None:
            dm = _DEPOSIT_PAT.search(description)
            if dm:
                try:
                    deposit = int(float(dm.group(1).replace(",", "")))
                except ValueError:
                    pass
        if council_tax is None:
            cm = _CTAX_PAT.search(description)
            if cm:
                council_tax = cm.group(0).strip()

    return furnish_type, deposit, description, council_tax


# ── Listing model ─────────────────────────────────────────────────────────────


def _listing_from_rsc(raw: dict[str, Any]) -> Listing:
    listing_id = str(raw.get("listingId", ""))
    detail_path = (raw.get("listingUris") or {}).get("detail", "")
    url = f"{_DETAIL_BASE}{detail_path}" if detail_path else ""

    beds: int | None = None
    for feat in raw.get("features", []):
        if isinstance(feat, dict) and feat.get("iconId") == "bed":
            beds = int(feat["content"])
            break

    avail_str: str | None = raw.get("availableFrom")
    avail_date: date | None = None
    if avail_str:
        if avail_str.lower().strip() in ("immediately", "now", "available now"):
            avail_date = date.today() + timedelta(days=1)
        else:
            avail_date = _parse_zoopla_date(avail_str)

    return Listing(
        source="zoopla",
        url=url,
        listing_id=listing_id or None,
        address=raw.get("address") or None,
        price_pcm=int(raw["priceUnformatted"]) if raw.get("priceUnformatted") else None,
        beds=beds,
        available_from=avail_date,
        description=raw.get("summaryDescription") or None,
    )


# ── Pagination iterator ───────────────────────────────────────────────────────


def iter_search_results(
    ctx: BrowserContext,
    filters: SearchFilters,
    location_name: str,
) -> Iterator[Listing]:
    p = ctx.new_page()
    try:
        page_num = 1
        max_pages: int | None = None

        while True:
            url = build_search_url(filters, location_name, page_num)
            p.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                p.wait_for_selector("article", timeout=12000)
            except Exception:
                pass

            listings, pagination = _extract_search_data(p.content())
            if max_pages is None:
                max_pages = int(pagination.get("pageNumberMax", 1))

            for raw in listings:
                yield _listing_from_rsc(raw)

            if page_num >= (max_pages or 1) or not listings:
                break
            page_num += 1
    finally:
        p.close()


# ── Detail fetcher ────────────────────────────────────────────────────────────

_CTX_OPTS: dict[str, object] = {
    "user_agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "locale": "en-GB",
    "viewport": {"width": 1280, "height": 800},
}


def fetch_listing_detail(browser: Browser, listing: Listing) -> Listing:
    """Fetch full details for a single listing.

    Creates a fresh browser context each call: Cloudflare limits detail page
    access to ~2 requests per session, so context isolation is required.
    """
    ctx = browser.new_context(**_CTX_OPTS)  # type: ignore[arg-type]
    try:
        p = ctx.new_page()
        try:
            p.goto(listing.url, wait_until="domcontentloaded", timeout=30000)
            try:
                # Detail pages reach ~390KB once all RSC chunks have streamed in.
                p.wait_for_function(
                    "() => document.documentElement.innerHTML.length > 300000",
                    timeout=20000,
                )
            except Exception:
                pass
            furnish_type, deposit, description, council_tax = _extract_detail_data(p.content())
        finally:
            p.close()
    finally:
        ctx.close()

    return Listing(
        source=listing.source,
        url=listing.url,
        listing_id=listing.listing_id,
        address=listing.address,
        price_pcm=listing.price_pcm,
        beds=listing.beds,
        available_from=listing.available_from,
        deposit=deposit if deposit is not None else listing.deposit,
        furnish_type=furnish_type or listing.furnish_type,
        council_tax=council_tax or listing.council_tax,
        key_features=listing.key_features,
        description=description or listing.description,
    )
