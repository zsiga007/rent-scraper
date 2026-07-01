"""Example config — copy this to config.py and fill in your own values.

    cp config.example.py config.py

config.py is gitignored so your personal filters, emails, and location
never end up in git history.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from filters import DateWindow, SearchFilters

# ── Search ────────────────────────────────────────────────────────────────────

# Find these by doing a manual search on rightmove.co.uk and copying
# `locationIdentifier` out of the URL (e.g. "STATION^9272" or "REGION^87526").
RIGHTMOVE_LOCATION_ID = "STATION^9272"
RIGHTMOVE_LOCATION_NAME = "King's Cross Station"
ZOOPLA_LOCATION = "King's Cross Station, London"

FILTERS = SearchFilters(
    max_beds=1,
    min_beds=None,
    max_price_pcm=2000,
    min_price_pcm=None,
    radius_miles=3.0,
    available_from=DateWindow(
        # The killer feature: a *two-sided* window. Most portals only let you
        # filter "available from" a date onwards — there's no way to exclude
        # listings whose move-in date is too far out. If your move has to
        # land in a specific window (lease ends, notice period, etc.), you'd
        # otherwise have to open every single listing to check by hand.
        earliest=date(2026, 9, 1),
        latest=date(2026, 9, 30),
    ),
    exclude_student=True,
    exclude_retirement=True,
    exclude_house_share=True,
    furnished_only=True,
)

# ── Email ─────────────────────────────────────────────────────────────────────

EMAIL_SENDER = "you@gmail.com"
EMAIL_RECIPIENTS: list[str] = [
    "you@gmail.com",
]
# File containing the Gmail app-password (one line). Never commit this file.
SECRET_FILE = Path(__file__).parent / "secret.txt"

# ── Snapshot ──────────────────────────────────────────────────────────────────


def _snapshot_path() -> Path:
    """Unique snapshot per (recipients, filters) pair — changing either starts fresh."""
    f = FILTERS
    payload = json.dumps(
        {
            "recipients": sorted(EMAIL_RECIPIENTS),
            "filters": {
                "max_beds": f.max_beds,
                "min_beds": f.min_beds,
                "max_price_pcm": f.max_price_pcm,
                "min_price_pcm": f.min_price_pcm,
                "radius_miles": f.radius_miles,
                "earliest": f.available_from.earliest.isoformat(),
                "latest": f.available_from.latest.isoformat(),
                "exclude_student": f.exclude_student,
                "exclude_retirement": f.exclude_retirement,
                "exclude_house_share": f.exclude_house_share,
                "furnished_only": f.furnished_only,
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    h = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return Path(__file__).parent / "data" / f"seen_{h}.json"


SNAPSHOT_PATH = _snapshot_path()
