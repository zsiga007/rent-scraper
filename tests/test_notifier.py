from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import notifier
from models import Listing


def _listing(listing_id: str, price: int = 1800, source: str = "rightmove") -> Listing:
    return Listing(
        source=source,
        url=f"https://{source}.co.uk/{listing_id}",
        listing_id=listing_id,
        address=f"Flat {listing_id}, London",
        price_pcm=price,
        beds=1,
        available_from=date(2026, 8, 25),
    )


# ── listing_hash ──────────────────────────────────────────────────────────────


def test_hash_is_12_chars() -> None:
    assert len(notifier.listing_hash(_listing("1"))) == 12


def test_hash_is_stable() -> None:
    lst = _listing("123")
    assert notifier.listing_hash(lst) == notifier.listing_hash(lst)


def test_hash_ignores_price_change() -> None:
    # Same source+id, different price → same hash
    h1 = notifier.listing_hash(_listing("123", 1800))
    h2 = notifier.listing_hash(_listing("123", 2000))
    assert h1 == h2


def test_hash_differs_by_id() -> None:
    assert notifier.listing_hash(_listing("123")) != notifier.listing_hash(_listing("456"))


def test_hash_differs_by_source() -> None:
    assert notifier.listing_hash(_listing("1", source="rightmove")) != notifier.listing_hash(
        _listing("1", source="zoopla")
    )


def test_hash_fallback_to_url_when_no_id() -> None:
    lst = Listing(source="rightmove", url="https://rm.co.uk/abc", listing_id=None)
    expected = hashlib.sha256(b"rightmove:https://rm.co.uk/abc").hexdigest()[:12]
    assert notifier.listing_hash(lst) == expected


# ── filter_new ────────────────────────────────────────────────────────────────


def test_filter_new_all_new() -> None:
    listings = [_listing("1"), _listing("2")]
    seen: dict[str, str] = {}
    new = notifier.filter_new(listings, seen)
    assert len(new) == 2
    assert len(seen) == 2


def test_filter_new_none_new() -> None:
    listings = [_listing("1"), _listing("2")]
    seen: dict[str, str] = {}
    notifier.filter_new(listings, seen)
    new = notifier.filter_new(listings, seen)
    assert new == []


def test_filter_new_mixed() -> None:
    seen: dict[str, str] = {}
    notifier.filter_new([_listing("1")], seen)
    new = notifier.filter_new([_listing("1"), _listing("2")], seen)
    assert len(new) == 1
    assert new[0].listing_id == "2"


def test_filter_new_mutates_seen() -> None:
    seen: dict[str, str] = {}
    notifier.filter_new([_listing("1")], seen)
    assert len(seen) == 1
    h = notifier.listing_hash(_listing("1"))
    assert h in seen


# ── load_snapshot / save_snapshot ─────────────────────────────────────────────


def test_load_snapshot_missing_file(tmp_path: Path) -> None:
    assert notifier.load_snapshot(tmp_path / "nonexistent.json") == {}


def test_load_snapshot_corrupt_file(tmp_path: Path) -> None:
    p = tmp_path / "seen.json"
    p.write_text("not json", encoding="utf-8")
    assert notifier.load_snapshot(p) == {}


def test_snapshot_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "seen.json"
    data = {"abc123def456": "2026-06-29", "fff000aaa111": "2026-06-28"}
    notifier.save_snapshot(data, p)
    assert notifier.load_snapshot(p) == data


def test_snapshot_sorted_keys(tmp_path: Path) -> None:
    p = tmp_path / "seen.json"
    notifier.save_snapshot({"zzz": "2026-01-01", "aaa": "2026-01-02"}, p)
    raw = p.read_text(encoding="utf-8")
    assert raw.index('"aaa"') < raw.index('"zzz"')


def test_snapshot_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "sub" / "deep" / "seen.json"
    notifier.save_snapshot({"h": "2026-01-01"}, p)
    assert p.exists()
    assert json.loads(p.read_text()) == {"h": "2026-01-01"}


# ── card rendering ────────────────────────────────────────────────────────────


def test_html_card_contains_key_fields() -> None:
    lst = Listing(
        source="rightmove",
        url="https://rightmove.co.uk/123",
        listing_id="123",
        address="Flat 1, London W1",
        price_pcm=1800,
        beds=1,
        available_from=date(2026, 8, 22),
        deposit=1800,
        furnish_type="Furnished",
    )
    html = notifier._html_card(1, lst)
    assert "Flat 1, London W1" in html
    assert "£1,800 pcm" in html
    assert "1 bed" in html
    assert "Furnished" in html
    assert "https://rightmove.co.uk/123" in html


def test_text_card_contains_key_fields() -> None:
    lst = Listing(
        source="zoopla",
        url="https://zoopla.co.uk/456",
        listing_id="456",
        address="Studio, E1",
        price_pcm=1500,
        beds=1,
        available_from=date(2026, 9, 1),
    )
    text = notifier._text_card(2, lst)
    assert "Studio, E1" in text
    assert "£1,500 pcm" in text
    assert "https://zoopla.co.uk/456" in text
