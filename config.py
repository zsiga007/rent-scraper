"""Load the rent-scraper config from YAML into typed objects.

The values live in a gitignored ``config.yaml`` (copy ``config.example.yaml``).
Point the ``RENT_SCRAPER_CONFIG`` env var at another file to override, e.g.::

    RENT_SCRAPER_CONFIG=work.yaml uv run python run.py
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from filters import DateWindow, SearchFilters

_ROOT = Path(__file__).parent
CONFIG_PATH = Path(os.environ.get("RENT_SCRAPER_CONFIG", str(_ROOT / "config.yaml")))


def _load() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise SystemExit(
            f"Config file not found: {CONFIG_PATH}\n"
            "Copy the template and edit it:\n"
            "    cp config.example.yaml config.yaml"
        )
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise SystemExit(f"Config file {CONFIG_PATH} is empty or malformed.")
    return data


def _as_date(v: Any) -> date:
    """Accept a YAML date, a datetime, or an ISO string."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


_cfg = _load()
_rm = _cfg["rightmove"]
_f = _cfg["filters"]
_win = _f["available_from"]
_email = _cfg["email"]

# ── Search ────────────────────────────────────────────────────────────────────

RIGHTMOVE_LOCATION_ID: str = _rm["location_id"]
RIGHTMOVE_LOCATION_NAME: str = _rm["location_name"]
ZOOPLA_LOCATION: str = _cfg["zoopla"]["location"]

FILTERS = SearchFilters(
    max_beds=_f["max_beds"],
    min_beds=_f.get("min_beds"),
    max_price_pcm=_f["max_price_pcm"],
    min_price_pcm=_f.get("min_price_pcm"),
    radius_miles=float(_f["radius_miles"]),
    available_from=DateWindow(
        earliest=_as_date(_win["earliest"]),
        latest=_as_date(_win["latest"]),
    ),
    exclude_student=_f.get("exclude_student", True),
    exclude_retirement=_f.get("exclude_retirement", True),
    exclude_house_share=_f.get("exclude_house_share", True),
    furnished_only=_f.get("furnished_only", True),
)

# ── Email ─────────────────────────────────────────────────────────────────────

EMAIL_SENDER: str = _email["sender"]
EMAIL_SENDER_NAME: str | None = _email.get("sender_name")
EMAIL_RECIPIENTS: list[str] = list(_email["recipients"])
# SMTP server used to send the notification email (defaults to Gmail).
SMTP_HOST: str = _email.get("smtp_host", "smtp.gmail.com")
SMTP_PORT: int = int(_email.get("smtp_port", 465))
# File containing the Gmail app-password (one line). Never commit this file.
SECRET_FILE: Path = _ROOT / _email.get("secret_file", "secret.txt")

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
    return _ROOT / "data" / f"seen_{h}.json"


SNAPSHOT_PATH = _snapshot_path()
