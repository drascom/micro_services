"""Read-only scraper probe: live HTTP or an already saved homepage HTML file.

python -m tools.homepage_probe sinemalar [--html /path/homepage.html]
Does not ingest, persist scraper state, invoke healing, or fetch detail pages.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urljoin

from app.library.normalize import normalize
from app.scraper import config, drift, fetch, parse, schema


def probe(site: str, html: str) -> dict:
    cfg = config.load_site(site)
    raw = parse.parse_list(html, cfg.row_selector, cfg.list_fields)
    items, metrics = schema.validate_items(cfg.schema, raw)
    normalized = {}
    for item in items:
        norm = normalize(site, item)
        if norm:
            key = norm["source_key"]
            if key not in normalized:
                normalized[key] = norm
            else:
                for field, value in norm.items():
                    if not normalized[key].get(field) and value not in (None, "", []):
                        normalized[key][field] = value
    return {"site_id": site, "metrics": metrics,
            "drift": drift.detect(metrics, cfg.baseline()),
            "unique_titles": len(normalized), "items": items,
            "normalized": list(normalized.values())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", choices=config.list_sites())
    parser.add_argument("--html", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cfg = config.load_site(args.site)
    try:
        html = args.html.read_text(encoding="utf-8") if args.html else fetch.page(
            cfg, urljoin(cfg.base_url, cfg.list_url), wait_for=cfg.row_selector)
        report = probe(args.site, html)
        report["input"] = "saved_html" if args.html else "live_http"
    except (OSError, fetch.FetchError) as exc:
        print(json.dumps({"site_id": args.site, "error": str(exc)}, ensure_ascii=False))
        return 1
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    return 1 if report["drift"]["drift"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
