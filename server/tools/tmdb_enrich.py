"""Backfill TMDB posters/backdrops (and missing metadata) for the existing library.

Usage:
    python -m tools.tmdb_enrich [--type movie|series] [--limit N] [--dry-run] [--force] [--verbose]
    python -m tools.tmdb_enrich --seasons [--limit N] [--dry-run] [--force]   # series: season posters + episode data/stills

Shares its logic with the admin "TMDB zenginleştirme" job (``app.library.enrich.backfill``).
Honours the selected types (admin Ayarlar ``tmdb_types``; default TMDB_ENRICH_TYPES, series stay
off unless selected), the retry window (TMDB_RETRY_DAYS) and never prints the API key. ``--verbose`` prints one line
per title: source (year) -> chosen candidate (title, year, tmdb id), score and the
decision (auto / review / unmatched) with the reason.
"""
from __future__ import annotations

import argparse
import json
import sys

from app import db, settings
from app.library import enrich, seasons, tmdb
from app.library.enrich import persist_match as _persist  # noqa: F401  (kept for callers/tests)
from app.library.enrich import verbose_line  # noqa: F401  (re-exported: tests + admin use the same text)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--type", choices=("movie", "series"), default="movie")
    ap.add_argument("--limit", type=int, default=0, help="max titles to look up (0 = all)")
    ap.add_argument("--dry-run", action="store_true", help="look up but write nothing")
    ap.add_argument("--force", action="store_true", help="ignore matched status / retry window")
    ap.add_argument("--verbose", action="store_true",
                    help="one line per title: chosen candidate, score, decision and reason")
    ap.add_argument("--seasons", action="store_true",
                    help="series only: season posters + episode titles/overviews/air dates/runtimes/stills "
                         "for TMDB-matched series (--limit counts series; implies --type series)")
    args = ap.parse_args(argv[1:])
    if args.seasons:
        args.type = "series"

    if not tmdb.enabled():
        print("TMDB key not configured (TMDB_ACCESS_KEY); nothing to do.")
        return 1
    if args.type not in settings.tmdb_types():
        print(f"type '{args.type}' is disabled; select it in the admin Ayarlar tab or set TMDB_ENRICH_TYPES.")
        return 1
    db.init()

    if args.seasons:
        def s_start(total, todo):
            print(f"series: {todo} with seasons to look up of {total} in library" + (" (dry-run)" if args.dry_run else ""))

        def s_progress(info):
            c = info["counters"]
            print(f"  {info['done']}/{info['total']} seasons={c['seasons']} episodes={c['episodes']} posters={c['posters']} "
                  f"stills={c['stills']} empty={c['empty']} errors={c['errors']}")

        result = seasons.backfill(dry_run=args.dry_run, limit=args.limit, force=args.force,
                                  on_start=s_start, on_progress=s_progress)
        if result["aborted"]:
            print("  TMDB unreachable; stopping.")
        print(json.dumps(result["counters"]))
        return 0

    def on_start(total, todo):
        print(f"{args.type}: {todo} to look up of {total} in library" + (" (dry-run)" if args.dry_run else ""))

    def on_progress(info):
        c = info["counters"]
        if args.verbose:
            for r in info["rows"]:
                print(verbose_line(r, info["results"].get(r["id"])))
        print(f"  {info['done']}/{info['total']} matched={c['matched']} "
              f"review={c['review']} unmatched={c['unmatched']} errors={c['errors']}")

    result = enrich.backfill(args.type, dry_run=args.dry_run, limit=args.limit, force=args.force,
                             on_start=on_start, on_progress=on_progress)
    if result["aborted"]:
        print("  TMDB unreachable; stopping.")
    print(json.dumps(result["counters"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
