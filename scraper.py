from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path

import httpx

from filters import SearchFilters
from models import Listing
from portals import rightmove

_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}
_CRAWL_DELAY = 0.35  # seconds between requests


def _card(i: int, lst: Listing) -> str:
    price = f"£{lst.price_pcm:,} pcm" if lst.price_pcm else "Price TBC"
    deposit = f"£{lst.deposit:,}" if lst.deposit else "?"
    furnish = lst.furnish_type or "?"
    avail = str(lst.available_from) if lst.available_from else "?"
    ctax = lst.council_tax or "?"
    beds = f"{lst.beds} bed" if lst.beds is not None else "?"

    lines = [
        f"{'─' * 64}",
        f"  {i}.  {lst.address or 'Unknown address'}",
        f"       {price}  ·  {beds}  ·  {furnish}",
        f"       Available: {avail}  ·  Deposit: {deposit}  ·  Council tax: {ctax}",
        f"       {lst.url}",
    ]
    if lst.key_features:
        lines.append("       " + "  ·  ".join(lst.key_features[:6]))
    if lst.description:
        excerpt = lst.description[:240].replace("\n", " ")
        lines.append(f"\n       {excerpt}…")
    return "\n".join(lines)


def run(
    filters: SearchFilters,
    location_id: str,
    location_name: str,
    output_path: Path = Path("results.txt"),
) -> None:
    with httpx.Client(headers=_HEADERS, follow_redirects=True, timeout=20.0) as client:
        # ── Pass 1: collect all pre-filtered results ──────────────────────────
        print(f"Scanning Rightmove results for '{location_name}' …")
        candidates: list[Listing] = []
        page = 0
        for listing in rightmove.iter_search_results(client, filters, location_id, location_name):
            candidates.append(listing)
            if len(candidates) % 24 == 0:
                page += 1
                in_window = sum(
                    1 for c in candidates if filters.available_from.contains(c.available_from)
                )
                print(f"  page {page}: {len(candidates)} total, {in_window} in date window so far")
            time.sleep(_CRAWL_DELAY)

        date_matches = [c for c in candidates if filters.available_from.contains(c.available_from)]
        print(
            f"\n{len(candidates)} pre-filtered listings scanned, "
            f"{len(date_matches)} available {filters.available_from.earliest} – "
            f"{filters.available_from.latest}"
        )

        # ── Pass 2: fetch details only for date-matching listings ─────────────
        print("\nFetching details for matching listings …")
        detailed: list[Listing] = []
        for i, lst in enumerate(date_matches, 1):
            try:
                full = rightmove.fetch_listing_detail(client, lst)
                detailed.append(full)
                print(
                    f"  [{i}/{len(date_matches)}] ✓  {full.address}  "
                    f"·  {full.furnish_type or '?'}  ·  deposit £{full.deposit or '?'}"
                )
            except Exception as exc:
                detailed.append(lst)  # keep with partial data
                print(f"  [{i}/{len(date_matches)}] !  {lst.address}  ({exc})")
            time.sleep(_CRAWL_DELAY)

        if filters.furnished_only:
            before = len(detailed)
            detailed = [
                lst
                for lst in detailed
                if lst.furnish_type is None or "unfurnished" not in lst.furnish_type.lower()
            ]
            print(f"\n{before - len(detailed)} unfurnished listings filtered out")

    detailed.sort(key=lambda x: (x.available_from or date.max, x.price_pcm or 0))

    # ── Write output ──────────────────────────────────────────────────────────
    beds = (
        f"{filters.min_beds}-{filters.max_beds}"
        if filters.min_beds is not None
        else f"Up to {filters.max_beds}"
    )
    price = (
        f"£{filters.min_price_pcm:,}–£{filters.max_price_pcm:,}"
        if filters.min_price_pcm is not None
        else f"max £{filters.max_price_pcm:,}"
    )
    header = (
        f"Rightmove — {location_name}  ±{filters.radius_miles} mi\n"
        f"{beds} bed  ·  {price} pcm  ·  "
        f"available {filters.available_from.earliest} – {filters.available_from.latest}\n"
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"{'═' * 64}\n\n"
        f"{len(detailed)} listings\n"
    )
    body = "\n\n".join(_card(i, lst) for i, lst in enumerate(detailed, 1))
    output_path.write_text(header + body + "\n", encoding="utf-8")
    print(f"\nSaved {len(detailed)} listings → {output_path}")
