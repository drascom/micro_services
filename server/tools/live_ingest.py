"""Canlı TV channel list (famelack) -> the ``channels`` table, by hand (the server runs it hourly; library/live.py).

Usage (from ``server/``):
    python -m tools.live_ingest                  # every country of scraper/configs/famelack.yaml
    python -m tools.live_ingest --country tr     # one country
    python -m tools.live_ingest --dry-run        # fetch + filter + probe, write nothing, print the counts
    python -m tools.live_ingest --force          # ignore ETags and the probe interval
    python -m tools.live_ingest --no-probe       # skip the m3u8 health probe
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from app import db
from app.library import live


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--country", default=None, help="only this country code (e.g. tr)")
    parser.add_argument("--dry-run", action="store_true", help="write nothing")
    parser.add_argument("--force", action="store_true", help="ignore ETags / probe interval")
    parser.add_argument("--no-probe", action="store_true", help="skip the m3u8 probe")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    if not args.dry_run:
        db.init()
    summary = live.run(country=args.country, dry_run=args.dry_run, force=args.force, probe=not args.no_probe,
                       trigger="cli")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
