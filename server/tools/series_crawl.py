"""Read the full season/episode inventory of the series in the library (the same pass every ingest runs).

Usage (from ``server/``):
    python -m tools.series_crawl yabancidizi --dry-run     # list what is due, no network, no writes
    python -m tools.series_crawl yabancidizi               # one budgeted pass (SERIES_CRAWL_BUDGET series)
    python -m tools.series_crawl yabancidizi --limit 40    # a bigger pass (mind the delay: ~10-20 s per series)
    python -m tools.series_crawl yabancidizi --key dizi/star-trek-strange-new-worlds-izle-3 --force

One request at a time, SERIES_CRAWL_DELAY between series; what a pass does not reach stays due for the next one.
Takes the ingest lock (an ingest / TMDB backfill running in the server is waited for), and TMDB season/episode
artwork is filled afterwards for the crawled series when the admin's TMDB automation is on.
"""
from __future__ import annotations

import argparse
import json
import sys

from app import db
from app.library import enrich, series_crawl, seasons
from app.scraper import config as scfg


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("site", nargs="?", default="yabancidizi")
    parser.add_argument("--limit", type=int, default=None, help="series to crawl (default SERIES_CRAWL_BUDGET)")
    parser.add_argument("--force", action="store_true", help="crawl even series that are not due")
    parser.add_argument("--key", default=None, help="only this source_key (e.g. dizi/lanterns)")
    parser.add_argument("--dry-run", action="store_true", help="only list what is due")
    args = parser.parse_args(argv[1:])
    db.init()
    cfg = scfg.load_site(args.site)
    with enrich.ingest_lock():
        stats = series_crawl.run_stage(cfg, args.site, None, force=args.force, limit=args.limit,
                                       dry_run=args.dry_run, only=args.key)
        cids = stats.pop("cids", [])
        if cids and not args.dry_run:
            stats["tmdb_seasons"] = seasons.auto_enrich(cids, locked=True)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 1 if stats.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
