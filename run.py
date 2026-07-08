"""Cron entry point: scrape Rightmove, email any new listings, save snapshot."""

from __future__ import annotations

import sys
import time
from datetime import datetime

import httpx
from tqdm import tqdm

import config
import notifier
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
_CRAWL_DELAY = 0.35


def _filters_summary() -> str:
    f = config.FILTERS
    beds = f"{f.min_beds}–{f.max_beds}" if f.min_beds is not None else f"≤{f.max_beds}"
    price = (
        f"£{f.min_price_pcm:,}–£{f.max_price_pcm:,}"
        if f.min_price_pcm is not None
        else f"max £{f.max_price_pcm:,}"
    )
    return (
        f"{beds} bed · {price} pcm · "
        f"{config.RIGHTMOVE_LOCATION_NAME} ±{f.radius_miles} mi · "
        f"available {f.available_from.earliest} – {f.available_from.latest}"
    )


def main() -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] rent-scraper run starting")

    # ── Load secret ───────────────────────────────────────────────────────────
    secret_path = config.SECRET_FILE
    if not secret_path.exists():
        print(f"ERROR: secret file not found: {secret_path}", file=sys.stderr)
        sys.exit(1)
    password = secret_path.read_text(encoding="utf-8").strip()

    # ── Load snapshot ─────────────────────────────────────────────────────────
    seen = notifier.load_snapshot(config.SNAPSHOT_PATH)
    print(f"Snapshot: {len(seen)} known listings")

    # ── Scrape Rightmove ──────────────────────────────────────────────────────
    filters = config.FILTERS
    f = filters
    all_candidates: list[Listing] = []

    with httpx.Client(headers=_HEADERS, follow_redirects=True, timeout=20.0) as client:
        print(f"Scanning Rightmove for '{config.RIGHTMOVE_LOCATION_NAME}' …")
        # disable=None → bar shows in a terminal, silently off in cron/launchd logs.
        scan_bar = tqdm(desc="  Pages", unit="pg", disable=None, leave=False)

        def _on_page(page: int, total: int) -> None:
            scan_bar.total = total
            scan_bar.n = page
            scan_bar.refresh()

        try:
            for listing in rightmove.iter_search_results(
                client,
                filters,
                config.RIGHTMOVE_LOCATION_ID,
                config.RIGHTMOVE_LOCATION_NAME,
                on_page=_on_page,
            ):
                all_candidates.append(listing)
                scan_bar.set_postfix_str(f"{len(all_candidates)} listings", refresh=False)
                time.sleep(_CRAWL_DELAY)
        except Exception as exc:
            scan_bar.close()
            print(
                f"Rightmove scan failed after retries: {exc} — skipping this run.",
                file=sys.stderr,
            )
            return
        scan_bar.close()

    date_matches = [c for c in all_candidates if f.available_from.contains(c.available_from)]
    print(f"  {len(all_candidates)} scanned · {len(date_matches)} in date window")

    # ── Fetch details for date-matching listings ───────────────────────────────
    detailed = []
    with httpx.Client(headers=_HEADERS, follow_redirects=True, timeout=20.0) as client:
        detail_bar = tqdm(date_matches, desc="  Details", unit="listing", disable=None, leave=False)
        for i, lst in enumerate(detail_bar, 1):
            try:
                full = rightmove.fetch_listing_detail(client, lst)
                detailed.append(full)
            except Exception as exc:
                detailed.append(lst)
                tqdm.write(f"  detail [{i}/{len(date_matches)}] failed: {exc}")
            time.sleep(_CRAWL_DELAY)

    # ── Filter by furnished status (only knowable after detail fetch) ─────────
    if f.furnished_only:
        before = len(detailed)
        detailed = [
            lst
            for lst in detailed
            if lst.furnish_type is None or "unfurnished" not in lst.furnish_type.lower()
        ]
        print(f"  {before - len(detailed)} unfurnished filtered out")

    # ── Dedup against snapshot ────────────────────────────────────────────────
    new_listings = notifier.filter_new(detailed, seen)
    print(f"  {len(new_listings)} new (not previously emailed)")

    # ── Save snapshot ─────────────────────────────────────────────────────────
    notifier.save_snapshot(seen, config.SNAPSHOT_PATH)
    print(f"Snapshot saved → {config.SNAPSHOT_PATH} ({len(seen)} total)")

    # ── Send email ────────────────────────────────────────────────────────────
    if not new_listings:
        print("Nothing new — no email sent.")
        return

    print(f"Sending email to {config.EMAIL_RECIPIENTS} ({len(new_listings)} listings) …")
    try:
        notifier.send_email(
            listings=new_listings,
            sender=config.EMAIL_SENDER,
            password=password,
            recipients=config.EMAIL_RECIPIENTS,
            filters_summary=_filters_summary(),
            smtp_host=config.SMTP_HOST,
            smtp_port=config.SMTP_PORT,
            sender_name=config.EMAIL_SENDER_NAME,
        )
        print("Email sent ✓")
    except Exception as exc:
        print(f"ERROR sending email: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
