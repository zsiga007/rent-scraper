from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DateWindow:
    earliest: date
    latest: date

    def contains(self, d: date | None) -> bool:
        return d is not None and self.earliest <= d <= self.latest


@dataclass(frozen=True)
class SearchFilters:
    max_beds: int
    max_price_pcm: int
    radius_miles: float
    available_from: DateWindow
    min_beds: int | None = None
    min_price_pcm: int | None = None
    exclude_student: bool = True
    exclude_retirement: bool = True
    exclude_house_share: bool = True
    furnished_only: bool = True
