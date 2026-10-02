#!/usr/bin/env python3
"""End-to-end smoke test for the self-healing scraper (no deploy).

Run:  server/venv/bin/python tools/scraper_smoke.py

1. Happy path  : really fetch sinemalar /filmler -> parse -> validate -> report
                 item count + field fill + one detail's synopsis & trailer. No drift.
2. Drift sim   : deliberately break a selector -> runner must DETECT drift.
3. Heal (real) : with SCRAPER_HEAL_ENABLED=true, call codex_cli on the drift and
                 honestly report healed vs heal_failed (+ reason).
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.scraper import config as scfg  # noqa: E402
from app.scraper import drift, heal, parse, schema  # noqa: E402
from app.scraper import runner  # noqa: E402

SITE = "sinemalar"
LINE = "-" * 70


def hp() -> "runner.RunResult | None":
    print(LINE + "\n[1] HAPPY PATH: sinemalar /filmler")
    res = runner.run_site(SITE, persist=True)
    if res.error:
        print("  NETWORK/ERROR:", res.error)
        return None
    m = res.metrics
    print(f"  items={m['valid_count']} (raw {m['raw_count']}, invalid {m['invalid_count']})")
    print(f"  fill_ratio={m['fill_ratio']}  drift={res.drift['drift']}")
    print("  field_fill=", json.dumps(m["field_fill"], ensure_ascii=False))
    if res.items:
        it = res.items[0]
        print(f"  sample: {it.get('title')} ({it.get('year')}) rating={it.get('rating')} genres={it.get('genres')}")
        syn = (it.get("synopsis") or "")[:80]
        print(f"  detail synopsis: {syn!r}")
        print(f"  trailer_url: {it.get('trailer_url')}")
    return res


def drift_sim() -> "tuple[dict, str] | None":
    print(LINE + "\n[2] DRIFT SIMULATION: break a selector in-memory")
    cfg = scfg.load_site(SITE)
    try:
        html = __fetch_list(cfg)
    except Exception as exc:
        print("  NETWORK/ERROR:", exc)
        return None
    broken = json.loads(json.dumps(cfg.data))
    broken["list"]["row_selector"] = "div.this-class-does-not-exist-anymore"
    raw = parse.parse_list(html, broken["list"]["row_selector"], broken["list"]["fields"])
    _v, metrics = schema.validate_items(cfg.schema, raw)
    verdict = drift.detect(metrics, cfg.baseline())
    print(f"  broken row_selector -> items={metrics['valid_count']} drift={verdict['drift']}")
    print("  reasons:", verdict["reasons"])
    return (verdict, html) if verdict["drift"] else None


def heal_test(html: str, reasons: list[str]) -> None:
    print(LINE + "\n[3] HEAL (real codex attempt)")
    if os.environ.get("SCRAPER_HEAL_ENABLED", "false").lower() not in ("1", "true", "yes"):
        print("  SKIPPED: set SCRAPER_HEAL_ENABLED=true to exercise codex heal.")
        return
    cfg = scfg.load_site(SITE)
    # feed codex a config whose row_selector is broken, plus the real HTML
    broken = json.loads(json.dumps(cfg.data))
    broken["list"]["row_selector"] = "div.this-class-does-not-exist-anymore"
    broken_cfg = scfg.SiteConfig(site_id=SITE, data=broken, path=cfg.path)
    hr = heal.heal(broken_cfg, page="list", html=html, reasons=reasons)
    print("  heal_result:", json.dumps(hr, ensure_ascii=False)[:500])
    print("  =>", hr.get("status"), hr.get("reason") or f"(new_version={hr.get('new_version')})")


def __fetch_list(cfg):
    from urllib.parse import urljoin
    from app.scraper import fetch as f
    return f.page(cfg, urljoin(cfg.base_url, cfg.list_url), wait_for=cfg.row_selector)


def main() -> int:
    print("registry:", json.dumps(scfg.registry(), ensure_ascii=False))
    hp()
    ds = drift_sim()
    if ds:
        verdict, html = ds
        heal_test(html, verdict["reasons"])
    else:
        print("[3] HEAL skipped (no drift/html available).")
    print(LINE + "\nDONE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
