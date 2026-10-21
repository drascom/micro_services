"""Problems the system finds itself (T14 item 3): event subscribers + the daily ``problem_scan`` job.

- ``structure_changed(site_id, page_type)`` (T11 crawl drift) -> a site problem ``structure_changed`` on the map page type; its
  action is the repair of that stage (no measurement-based diagnosis needed). When it passes, the stopped crawl type goes on
  (the stage engine calls ``crawl.plan.stage_passed``).
- Missing data: more than :data:`MISSING_SHARE` of a site's visible titles without a poster (TMDB did not fill it either) ->
  ONE site problem ``missing_field`` (``page_type=title``); single titles never get a problem.
- Broken links: a ``gone`` (404/410) title / episode address in the frontier -> the ``video_sources`` rows of that page
  ``disabled`` (a title with no source left is not visible, K5); re-crawled fine -> back to ``unknown``; more than
  :data:`GONE_SHARE` gone -> site problem ``links_gone`` (an admin note, no LLM).
- A visible title whose every source is ``broken`` -> a title / episode problem (the T13 actions).
- Coverage (T33, the old ``playheal.coverage_signal``): more than :data:`COVERAGE_SHARE` of a site's series (not blocked,
  not unlisted) without any episode row -> ``missing_field`` (``page_type=episodes``, field ``episodes``); with episode rows
  but no usable source -> ``missing_field`` (``page_type=player``, field ``sources``). The TMDB episode gap (aired TMDB
  episodes of the seasons TMDB answered, missing from the site's rows) above :data:`GAP_SHARE` -> the ``episodes`` problem too.
- ``image_broken`` is counted per title (``problem_signals``); over :data:`IMAGE_SHARE` of a site's visible titles -> ONE site
  problem ``image_broken``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from ... import db, settings
from ...core.catalog import cache
from .. import jobs
from . import store

log = logging.getLogger("diziflix.problems")

MISSING_SHARE = 0.2
IMAGE_SHARE = 0.2
GONE_SHARE = 0.3
MIN_GONE_ROWS = 5          # a share of gone rows is judged only above this many title / episode rows
MAX_TITLE_PROBLEMS = 20    # sourceless titles opened per scan
COVERAGE_SHARE = 0.5      # old playheal: half the series without episodes / sources
MIN_COVERAGE_TITLES = 5   # coverage is judged only above this many series
GAP_SHARE = 0.2           # missing share of the aired TMDB episodes
MIN_GAP_EPISODES = 10     # the gap is judged only above this many expected episodes
SCAN_EVERY_S = 86400
_LAYER = {"structure_changed": "map", "missing_field": "map", "links_gone": "content", "image_broken": "image"}


def _deadline() -> int:
    return store.now() + int(settings.get("problem_deadline_s") or 900)


def open_site_problem(site_id: str, page_type: str, klass: str, evidence: dict) -> Optional[dict]:
    """The site problem of ``(site, page_type, class)``: created / reopened when finished, its job queued while it works."""
    from . import run
    if not site_id:
        return None
    problem, created = store.open_or_get(store.site_fp(site_id, page_type, klass), scope="site", site_id=site_id,
                                         page_type=page_type, layer=_LAYER.get(klass), deadline_at=_deadline(),
                                         evidence=evidence, **{"class": klass})
    if not created:
        if problem["state"] in store.FINISHED:
            problem = store.transition(problem["id"], "open", actor="system", kind="reopened", note=klass,
                                       attempts=int(problem["attempts"] or 0) + 1, deadline_at=_deadline(), next_at=0,
                                       evidence={**(problem.get("evidence") or {}), **evidence})
        else:
            store.event(problem["id"], "system", "signal", klass, evidence)
    if problem["state"] in store.WORKING:
        run.enqueue(problem)
    return problem


# --------------------------------------------------------------------------- events

def on_structure_changed(site_id: str = "", page_type: str = "", evidence: Any = None, **_: Any) -> None:
    from ..crawl import drift
    map_type = drift.PAGE.get(page_type, (page_type, ""))[0]
    ev = dict(evidence or {}) if isinstance(evidence, dict) else {}
    open_site_problem(site_id, map_type, "structure_changed", {"crawl_page_type": page_type, "pages": ev.get("pages") or []})


def on_image_broken(canonical_id: str = "", kind: str = "", url: str = "", reason: str = "", **_: Any) -> None:
    """Counted per title (no problem of its own); :func:`scan` judges the site-wide share."""
    if not canonical_id:
        return
    row = db.query_one("SELECT site_id FROM source_items WHERE canonical_id=? ORDER BY fetched_at DESC LIMIT 1", (canonical_id,))
    db.execute("INSERT INTO problem_signals(kind,ref,site_id,at,data) VALUES ('image_broken',?,?,?,?) "
               "ON CONFLICT(kind,ref) DO UPDATE SET at=excluded.at, data=excluded.data",
               (canonical_id, row["site_id"] if row else None, store.now(), f"{kind}:{reason}"[:200]))


# --------------------------------------------------------------------------- scan

def visible_titles(site_id: str) -> list:
    """Titles of the site a client can see (K5: a ``unknown|healthy`` source of this site; not unlisted)."""
    unlisted = set(cache._unlisted()) if hasattr(cache, "_unlisted") else set()
    rows = db.query("SELECT DISTINCT li.id, li.poster_url, li.tmdb_poster_url, si.source_url FROM source_items si "
                    "JOIN library_items li ON li.id = si.canonical_id "
                    "JOIN video_sources v ON v.canonical_id = si.canonical_id AND v.source = si.site_id "
                    "WHERE si.site_id=? AND v.kind!='trailer' AND v.status IN ('unknown','healthy')", (site_id,))
    return [r for r in rows if r["id"] not in unlisted]


def scan_missing(site_id: str, titles: list) -> Optional[dict]:
    missing = [t for t in titles if not (t["poster_url"] or t["tmdb_poster_url"])]
    if not titles or len(missing) / len(titles) <= MISSING_SHARE:
        return None
    return open_site_problem(site_id, "title", "missing_field", {
        "field": "poster", "counts": {"visible": len(titles), "missing": len(missing)},
        "urls": [t["source_url"] for t in missing[:3] if t["source_url"]]})


def scan_images(site_id: str, titles: list) -> Optional[dict]:
    ids = {t["id"] for t in titles}
    broken = [r["ref"] for r in db.query("SELECT ref FROM problem_signals WHERE kind='image_broken'") if r["ref"] in ids]
    if not titles or len(broken) / len(titles) <= IMAGE_SHARE:
        return None
    return open_site_problem(site_id, "title", "image_broken", {"counts": {"visible": len(titles), "broken": len(broken)},
                                                                 "examples": broken[:3]})


def scan_coverage(site_id: str) -> dict:
    """Series coverage of one site (episodes / sources / TMDB episode gap) -> at most one ``episodes`` and one ``player``
    ``missing_field`` problem. Generic: only ``video_sources`` + TMDB tables, no site knowledge."""
    from ...core.catalog import seasons
    unlisted = set(cache._unlisted()) if hasattr(cache, "_unlisted") else set()
    rows = db.query("SELECT DISTINCT li.id, si.source_url FROM source_items si JOIN library_items li ON li.id = si.canonical_id "
                    "WHERE si.site_id=? AND li.type='series'", (site_id,))
    stats = {r["canonical_id"]: r for r in db.query(
        "SELECT canonical_id, SUM(status='blocked') AS blocked, SUM(status!='blocked') AS eps, "
        "SUM(status IN ('unknown','healthy','suspect')) AS usable FROM video_sources "
        "WHERE source=? AND kind='episode' GROUP BY canonical_id", (site_id,))}
    series = [r for r in rows if r["id"] not in unlisted and not (stats.get(r["id"]) and stats[r["id"]]["blocked"])]
    no_eps = [r for r in series if not (stats.get(r["id"]) and stats[r["id"]]["eps"])]
    no_eps_ids = {r["id"] for r in no_eps}
    no_src = [r for r in series if stats.get(r["id"]) and stats[r["id"]]["eps"] and not stats[r["id"]]["usable"]]
    gap = {"expected": 0, "missing": 0}
    gap_examples = []
    with db.read_transaction() as conn:
        for r in series:
            if r["id"] in no_eps_ids:
                continue
            g = seasons.episode_gap(conn, r["id"], site_id)
            gap["expected"] += g["expected"]
            gap["missing"] += g["missing"]
            if g["missing"] and len(gap_examples) < 3:
                gap_examples.append(r["source_url"] or r["id"])
    n = len(series)
    out: dict = {"series": n, "no_episodes": len(no_eps), "no_sources": len(no_src), **{f"gap_{k}": v for k, v in gap.items()}}
    eps_low = n >= MIN_COVERAGE_TITLES and len(no_eps) / n > COVERAGE_SHARE
    gap_high = gap["expected"] >= MIN_GAP_EPISODES and gap["missing"] / gap["expected"] > GAP_SHARE
    if eps_low or gap_high:
        out["episodes_problem"] = open_site_problem(site_id, "episodes", "missing_field", {
            "field": "episodes", "counts": {"series": n, "no_episodes": len(no_eps), "tmdb_expected": gap["expected"],
                                            "tmdb_missing": gap["missing"]},
            "urls": ([r["source_url"] for r in no_eps if r["source_url"]] + gap_examples)[:3]})
    if n >= MIN_COVERAGE_TITLES and len(no_src) / n > COVERAGE_SHARE:
        out["player_problem"] = open_site_problem(site_id, "player", "missing_field", {
            "field": "sources", "counts": {"series": n, "no_sources": len(no_src)},
            "urls": [r["source_url"] for r in no_src[:3] if r["source_url"]]})
    return out


GONE_CODES = ("http_404", "http_410")   # only a definite disappearance counts as gone (a timeout / 5xx / block does not)


def scan_gone(site_id: str) -> dict:
    """``gone`` frontier addresses whose last error is a definite disappearance (404/410) -> the sources whose OWN page that
    address is ``disabled`` (a gone title page does not take its episodes along); an address that crawled fine again gives
    its ``link_gone`` sources back (``unknown``). Share above :data:`GONE_SHARE` -> ``links_gone``."""
    rows = db.query("SELECT url, page_type, state, error FROM crawl_frontier WHERE site_id=? AND page_type IN ('title','episode')",
                    (site_id,))
    gone = [r for r in rows if r["state"] == "gone" and (r["error"] or "") in GONE_CODES]
    disabled = restored = 0
    for r in gone:
        disabled += db.execute("UPDATE video_sources SET status='disabled', last_error='link_gone' WHERE source=? AND locator=? "
                               "AND status NOT IN ('disabled','blocked')", (site_id, r["url"])).rowcount
    for r in rows:
        if r["state"] == "done":
            restored += db.execute("UPDATE video_sources SET status='unknown', last_error=NULL WHERE source=? AND locator=? "
                                   "AND status='disabled' AND last_error='link_gone'", (site_id, r["url"])).rowcount
    if disabled or restored:
        cache.refresh_soon()
    out: dict = {"gone": len(gone), "rows": len(rows), "disabled": disabled, "restored": restored}
    if len(rows) >= MIN_GONE_ROWS and len(gone) / len(rows) > GONE_SHARE:
        out["problem"] = open_site_problem(site_id, "title", "links_gone", {
            "counts": {"rows": len(rows), "gone": len(gone)}, "urls": [r["url"] for r in gone[:3]]})
    return out


def scan_sourceless(site_id: str) -> int:
    """Titles of the site with sources that are ALL broken -> one problem each (T13 diagnosis + actions)."""
    from . import intake
    rows = db.query("SELECT canonical_id, MAX(CASE WHEN status='broken' THEN episode_id END) AS ep FROM video_sources "
                    "WHERE source=? AND kind!='trailer' AND status NOT IN ('disabled','blocked') GROUP BY canonical_id "
                    "HAVING SUM(status IN ('unknown','healthy','suspect'))=0 AND SUM(status='broken')>0 LIMIT ?",
                    (site_id, MAX_TITLE_PROBLEMS))
    n = 0
    for r in rows:
        fp = store.episode_fp(r["canonical_id"], r["ep"] or "")
        known = store.by_fingerprint(fp)
        if known is not None and known["state"] not in store.FINISHED:
            continue
        intake.report(r["canonical_id"], r["ep"] or "", "", origin="system_scan", data={"site": site_id})
        n += 1
    return n


def scan(site_id: Optional[str] = None) -> dict:
    sites = [site_id] if site_id else [r["site_id"] for r in db.query("SELECT site_id FROM sites WHERE status='active'")]
    out = {}
    for sid in sites:
        try:
            titles = visible_titles(sid)
            gone = scan_gone(sid)
            out[sid] = {"visible": len(titles), "missing": bool(scan_missing(sid, titles)), "images": bool(scan_images(sid, titles)),
                        "gone": gone["gone"], "disabled": gone["disabled"], "restored": gone["restored"],
                        "links_gone": bool(gone.get("problem")),
                        "sourceless": scan_sourceless(sid)}
            cov = scan_coverage(sid)
            out[sid].update(coverage={k: v for k, v in cov.items() if not k.endswith("_problem")},
                            episodes_gap=bool(cov.get("episodes_problem")), sources_low=bool(cov.get("player_problem")))
        except Exception:   # one site never stops the scan
            log.exception("problem scan of %s failed", sid)
    return out


def scan_job(job, ctx):
    return jobs.Done(scan())
