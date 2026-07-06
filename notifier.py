from __future__ import annotations

import hashlib
import json
import smtplib
import textwrap
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

from models import Listing

# ── Hashing ───────────────────────────────────────────────────────────────────


def listing_hash(lst: Listing) -> str:
    """48-bit stable identifier.

    Keyed on source + listing_id (or url as fallback) so the same property at
    a different price or available date still maps to the same hash.
    """
    key = f"{lst.source}:{lst.listing_id or lst.url}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


# ── Snapshot ──────────────────────────────────────────────────────────────────


def load_snapshot(path: Path) -> dict[str, str]:
    """Return {hash: first_seen_date_iso} from the snapshot file."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
        return {}


def save_snapshot(seen: dict[str, str], path: Path) -> None:
    """Atomically write the snapshot (sorted compact JSON)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(seen, sort_keys=True, separators=(",", ":"))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)  # atomic rename on POSIX / macOS


def filter_new(
    listings: list[Listing],
    seen: dict[str, str],
) -> list[Listing]:
    """Return listings whose hash is not yet in *seen*, and add them to it."""
    today = date.today().isoformat()
    new: list[Listing] = []
    for lst in listings:
        h = listing_hash(lst)
        if h not in seen:
            seen[h] = today
            new.append(lst)
    return new


# ── Email ─────────────────────────────────────────────────────────────────────


def _text_card(i: int, lst: Listing) -> str:
    price = f"£{lst.price_pcm:,} pcm" if lst.price_pcm else "Price TBC"
    avail = str(lst.available_from) if lst.available_from else "?"
    deposit = f"£{lst.deposit:,}" if lst.deposit else "?"
    furnish = lst.furnish_type or "?"
    ctax = lst.council_tax or "?"
    beds = f"{lst.beds} bed" if lst.beds is not None else "?"
    lines = [
        f"{i}. {lst.address or 'Unknown'}",
        f"   {price}  ·  {beds}  ·  {furnish}",
        f"   Available: {avail}  ·  Deposit: {deposit}  ·  Council tax: {ctax}",
        f"   {lst.url}",
    ]
    if lst.description:
        excerpt = lst.description[:200].replace("\n", " ")
        lines.append(f"   {excerpt}…")
    return "\n".join(lines)


def _html_card(i: int, lst: Listing) -> str:
    price = f"£{lst.price_pcm:,} pcm" if lst.price_pcm else "Price TBC"
    avail = str(lst.available_from) if lst.available_from else "?"
    deposit = f"£{lst.deposit:,}" if lst.deposit else "?"
    furnish = lst.furnish_type or "?"
    ctax = lst.council_tax or "?"
    beds = f"{lst.beds} bed" if lst.beds is not None else "?"
    desc_html = ""
    if lst.description:
        excerpt = lst.description[:300].replace("\n", " ")
        desc_html = f'<p style="color:#555;font-size:13px;margin:6px 0 0">{excerpt}…</p>'
    tag_style = (
        "display:inline-block;background:#f0f4ff;color:#3355cc;"
        "border-radius:4px;padding:2px 8px;font-size:12px;margin:2px 2px 2px 0"
    )
    source_badge = (
        f'<span style="{tag_style};background:#fff3cd;color:#856404">'
        f"{lst.source.capitalize()}</span>"
    )
    return textwrap.dedent(f"""
        <div style="border:1px solid #e0e0e0;border-radius:8px;padding:16px 20px;
                    margin:0 0 16px;font-family:sans-serif;background:#fff">
          <div style="margin-bottom:6px">
            {source_badge}
            <span style="font-size:18px;font-weight:700;color:#111">
              {lst.address or "Unknown address"}
            </span>
          </div>
          <div style="font-size:15px;color:#222;margin-bottom:4px">
            <strong>{price}</strong> &nbsp;·&nbsp; {beds} &nbsp;·&nbsp; {furnish}
          </div>
          <div style="font-size:13px;color:#444;margin-bottom:8px">
            📅 Available: <strong>{avail}</strong>
            &nbsp;&nbsp;💰 Deposit: <strong>{deposit}</strong>
            &nbsp;&nbsp;🏛 Council tax: {ctax}
          </div>
          {desc_html}
          <div style="margin-top:10px">
            <a href="{lst.url}" style="color:#1a56db;font-size:13px">View listing →</a>
          </div>
        </div>
    """).strip()


def _build_html(listings: list[Listing], filters_summary: str) -> str:
    cards = "\n".join(_html_card(i, lst) for i, lst in enumerate(listings, 1))
    return textwrap.dedent(f"""
        <!DOCTYPE html>
        <html lang="en">
        <head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Rent listings</title></head>
        <body style="background:#f5f5f5;padding:24px;margin:0">
          <div style="max-width:640px;margin:0 auto">
            <h2 style="font-family:sans-serif;color:#111;margin:0 0 4px">
              🏠 {len(listings)} new rental listing{"s" if len(listings) != 1 else ""}
            </h2>
            <p style="font-family:sans-serif;color:#666;font-size:13px;margin:0 0 20px">
              {filters_summary}
            </p>
            {cards}
            <p style="font-family:sans-serif;color:#999;font-size:12px;margin-top:24px">
              Sent by rent-scraper · Rightmove
            </p>
          </div>
        </body>
        </html>
    """).strip()


def send_email(
    listings: list[Listing],
    sender: str,
    password: str,
    recipients: list[str],
    filters_summary: str = "",
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 465,
    sender_name: str | None = None,
) -> None:
    today = date.today().isoformat()
    subject = f"[Rent] {len(listings)} new listing{'s' if len(listings) != 1 else ''} – {today}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((sender_name, sender)) if sender_name else sender
    msg["To"] = ", ".join(recipients)

    text_body = "\n\n".join(_text_card(i, lst) for i, lst in enumerate(listings, 1))
    html_body = _build_html(listings, filters_summary)

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL(smtp_host, smtp_port) as smtp:
        smtp.login(sender, password)
        smtp.sendmail(sender, recipients, msg.as_string())
