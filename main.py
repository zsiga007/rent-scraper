from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

import config
import scraper
import zoopla_scraper
from portals import rightmove, zoopla


def show_urls() -> None:
    rm_url = rightmove.build_search_url(
        config.FILTERS, config.RIGHTMOVE_LOCATION_ID, config.RIGHTMOVE_LOCATION_NAME
    )
    z_url = zoopla.build_search_url(config.FILTERS, config.ZOOPLA_LOCATION)
    print(f"Rightmove:\n  {rm_url}")
    print(f"\nZoopla:\n  {z_url}")
    if input("\nOpen both in browser? [y/N] ").strip().lower() == "y":
        webbrowser.open(rm_url)
        webbrowser.open(z_url)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "urls"
    if mode == "scrape":
        output = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("results.txt")
        scraper.run(
            config.FILTERS,
            config.RIGHTMOVE_LOCATION_ID,
            config.RIGHTMOVE_LOCATION_NAME,
            output,
        )
    elif mode == "scrape-zoopla":
        output = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("zoopla_results.txt")
        zoopla_scraper.run(config.FILTERS, config.ZOOPLA_LOCATION, output)
    elif mode == "scrape-all":
        scraper.run(
            config.FILTERS,
            config.RIGHTMOVE_LOCATION_ID,
            config.RIGHTMOVE_LOCATION_NAME,
            Path("results.txt"),
        )
        zoopla_scraper.run(config.FILTERS, config.ZOOPLA_LOCATION, Path("zoopla_results.txt"))
    else:
        show_urls()


if __name__ == "__main__":
    main()
