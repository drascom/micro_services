"""Orchestrate a site run: fetch -> parse -> validate -> drift? -> heal ->
(if healed+applied) re-parse with the new config. Metrics are persisted per site.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urljoin

from . import config as scfg
from . import drift, fetch, heal, parse, schema, state

log = logging.getLogger("scraper.runner")


@dataclass
class RunResult:
    site_id: str
    items: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    drift: dict = field(default_factory=dict)
    heal_result: Optional[dict] = None
    config_version: Optional[int] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "site_id": self.site_id,
            "items": self.items,
            "metrics": self.metrics,
            "drift": self.drift,
            "heal_result": self.heal_result,
            "config_version": self.config_version,
            "error": self.error,
        }


def _enrich_first(cfg: scfg.SiteConfig, items: list[dict]) -> None:
    """Fetch ONE detail page and merge its fields into the first item in place.

    Keeps courtesy load low; the smoke test only needs to prove detail parsing.
    """
    if not items or not cfg.detail_fields:
        return
    url = items[0].get("detail_url")
    if not url:
        return
    try:
        html = fetch.page(cfg, urljoin(cfg.base_url, url))
    except fetch.FetchError as exc:
        log.warning("detail fetch failed: %s", exc)
        return
    detail = parse.parse_detail(html, cfg.detail_fields)
    for k, v in detail.items():
        if v not in (None, "", []):
            items[0][k] = v
    items[0]["_detail_html"] = html  # transient, for heal sandbox; stripped later


def run_site(site_id: str, *, persist: bool = True) -> RunResult:
    log.info("run_site: %s", site_id)
    result = RunResult(site_id=site_id)
    try:
        cfg = scfg.load_site(site_id)
        result.config_version = cfg.version

        list_url = urljoin(cfg.base_url, cfg.list_url)
        html = fetch.page(cfg, list_url, wait_for=cfg.row_selector)
        raw = parse.parse_list(html, cfg.row_selector, cfg.list_fields)
        log.info("parsed %d raw rows", len(raw))

        valid, metrics = schema.validate_items(cfg.schema, raw)
        verdict = drift.detect(metrics, cfg.baseline())
        result.metrics, result.drift, result.items = metrics, verdict, valid

        if verdict["drift"]:
            log.warning("DRIFT on %s: %s", site_id, verdict["reasons"])
            hr = heal.heal(cfg, page="list", html=html, reasons=verdict["reasons"], trigger="drift")
            result.heal_result = hr
            if hr.get("status") == "healed" and hr.get("applied"):
                cfg = scfg.load_site(site_id)  # reload new active version
                result.config_version = cfg.version
                raw = parse.parse_list(html, cfg.row_selector, cfg.list_fields)
                valid, metrics = schema.validate_items(cfg.schema, raw)
                verdict = drift.detect(metrics, cfg.baseline())
                result.metrics, result.drift, result.items = metrics, verdict, valid
                log.info("re-parsed after heal: %d items, drift=%s", len(valid), verdict["drift"])
                if not verdict["drift"]:
                    try:  # healed config is proven on live html -> new known-good baseline
                        scfg.update_baseline(site_id, {
                            "valid_count": metrics.get("valid_count", 0),
                            "fill_ratio": metrics.get("fill_ratio", 0.0),
                            "field_fill": metrics.get("field_fill", {}),
                            "field_fill_all": heal.field_fill(raw, cfg.list_fields),
                            "config_version": cfg.version,
                        })
                    except Exception:
                        log.exception("baseline update failed for %s", site_id)
        else:
            _enrich_first(cfg, result.items)
            for it in result.items:
                it.pop("_detail_html", None)
    except Exception as exc:
        result.error = str(exc)
        log.exception("run_site failed: %s", site_id)

    if persist:
        try:
            state.record_run(site_id, result.to_dict())
        except Exception:
            log.exception("failed to persist state for %s", site_id)
    return result


def run_all(*, persist: bool = True) -> list[RunResult]:
    """Run every registered site (used by the scheduler / admin refresh)."""
    return [run_site(sid, persist=persist) for sid in scfg.list_sites()]
