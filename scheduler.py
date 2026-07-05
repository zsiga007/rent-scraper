"""Run the scrape on a fixed daily schedule, in the foreground.

Designed to run inside a ``screen`` (or tmux) session so it keeps working after
you detach, for as long as you stay logged in::

    screen -S rent-scraper -dm bash -lc 'uv run python scheduler.py >> data/scheduler.log 2>&1'
    screen -r rent-scraper      # reattach   ·   Ctrl-A then D to detach

Unlike a morning cron/launchd job, this keeps your logged-in session — network,
credentials, an awake machine — so the run actually goes through.

Runs at 07:00 and 15:00 UK time (Europe/London, DST-aware) by default. Override
with a comma-separated 24-hour list::

    RENT_SCRAPER_AT=08:30,18:00 uv run python scheduler.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).parent
_TZ = ZoneInfo("Europe/London")
_TIMES_SPEC = os.environ.get("RENT_SCRAPER_AT", "07:00,15:00")


def _parse_times(spec: str) -> list[tuple[int, int]]:
    times: list[tuple[int, int]] = []
    for part in spec.split(","):
        hh, mm = part.strip().split(":")
        times.append((int(hh), int(mm)))
    return sorted(set(times))


def _next_run(now: datetime, times: list[tuple[int, int]]) -> datetime:
    candidates: list[datetime] = []
    for hh, mm in times:
        t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if t <= now:
            t += timedelta(days=1)
        candidates.append(t)
    return min(candidates)


def main() -> None:
    times = _parse_times(_TIMES_SPEC)
    pretty = ", ".join(f"{hh:02d}:{mm:02d}" for hh, mm in times)
    print(f"[scheduler] running daily at {pretty} Europe/London — Ctrl-C to stop", flush=True)

    while True:
        target = _next_run(datetime.now(_TZ), times)
        print(f"[scheduler] next run: {target:%Y-%m-%d %H:%M %Z}", flush=True)

        # Sleep in short chunks so laptop sleep/wake drift self-corrects.
        while (remaining := (target - datetime.now(_TZ)).total_seconds()) > 0:
            time.sleep(min(300.0, remaining))

        stamp = datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
        print(f"[scheduler] {stamp} — starting scrape", flush=True)
        try:
            subprocess.run([sys.executable, str(_ROOT / "run.py")], cwd=_ROOT, check=False)
        except Exception as exc:  # a failed run must never kill the loop
            print(f"[scheduler] run error: {exc}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
