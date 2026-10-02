"""Manually ingest one or more sources into the canonical library.

Usage:
    python -m tools.ingest              # ingest all scraper sites
    python -m tools.ingest sinemalar    # ingest a specific site
"""
from __future__ import annotations

import json
import sys

from app import db
from app.library import ingest_source
from app.scraper import list_sites


def main(argv: list[str]) -> int:
    db.init()
    sites = argv[1:] or list_sites()
    out = []
    for site in sites:
        out.append(ingest_source(site))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if any(r.get("error") or r.get("partial") for r in out) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
