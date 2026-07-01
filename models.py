from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Listing:
    source: str
    url: str
    listing_id: str | None = None
    address: str | None = None
    price_pcm: int | None = None
    beds: int | None = None
    available_from: date | None = None
    deposit: int | None = None  # GBP
    furnish_type: str | None = None
    council_tax: str | None = None
    key_features: tuple[str, ...] = ()
    description: str | None = None
