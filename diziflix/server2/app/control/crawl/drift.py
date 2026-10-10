"""Structure change rule: every crawled page is also a structure check (its page type's ``meta.<type>.fingerprint``).

``STREAK`` pages of one page type in a row that miss their fingerprint (or give zero rows) stop the crawl of THAT page
type (its frontier rows wait, the other types go on) and publish ``structure_changed(site_id, page_type, evidence)``
once (T14 opens a problem; until then an ``admin_notes`` warning). One odd page never stops anything. A stopped type
goes on when its stage passes again (``resume``)."""
from __future__ import annotations

from typing import Optional

from ...core import events
from ...core.extract import fingerprint
from .. import notes
from . import state

STREAK = 5
#: frontier page type -> (map page type, stage id)
PAGE = {"home": ("home", "A1"), "listing": ("listing", "A2"), "title": ("title", "A3"), "season": ("episodes", "A4"),
        "episode": ("player", "A5")}
LABEL = {"home": "ana sayfa", "listing": "liste", "title": "başlık", "season": "sezon", "episode": "bölüm"}


def rule_of(site_map: dict, page_type: str) -> Optional[dict]:
    """The extraction rule the fingerprint of ``page_type`` was made with (A1: first section, A2: rows, A3: fields)."""
    pages = site_map.get("pages") or {}
    if page_type == "home":
        first = next(iter((pages.get("home") or {}).get("sections") or []), None)
        return {"row_selector": first.get("row_selector"), "fields": first.get("fields") or {}} if isinstance(first, dict) else None
    if page_type == "listing":
        rule = pages.get("listing") or {}
        return {"row_selector": rule.get("row_selector"), "fields": rule.get("fields") or {}}
    if page_type == "title":
        return {"fields": (pages.get("title") or {}).get("fields") or {}}
    return None


LISTING_MIN_SHARE = 0.5   # a listing page that links a next page must show at least half the reference rows


def check(site_map: dict, page_type: str, html: str, *, last_page: bool = True) -> tuple[Optional[bool], list[str]]:
    """``(fingerprint_ok, reasons)``; ``None`` when the page type has no reference fingerprint. ``last_page`` (listings):
    the page links no next page, so it may be short; a page with a next link keeps the reference (at least
    ``LISTING_MIN_SHARE`` of its rows)."""
    map_type = PAGE.get(page_type, (page_type, ""))[0]
    ref = ((site_map.get("meta") or {}).get(map_type) or {}).get("fingerprint")
    rule = rule_of(site_map, page_type)
    if not isinstance(ref, dict) or not rule:
        return None, []
    cur = fingerprint.make(html, rule)
    extra: list[str] = []
    if page_type == "listing" and cur.get("rows"):
        ref_rows, cur_rows = int(ref.get("rows") or 0), int(cur["rows"])
        ref = {**ref, "rows": min(ref_rows, cur_rows)}   # the row floor is checked here, the field fill by compare
        if not last_page and cur_rows < ref_rows * LISTING_MIN_SHARE:
            extra.append(f"rows {cur_rows} < {ref_rows * LISTING_MIN_SHARE:g} (reference {ref_rows}, not the last page)")
    out = fingerprint.compare(ref, cur)
    # A skeleton change alone is not a miss: one site serves films and series from two templates (the rule still fills
    # every field). A miss = rows / required field fill fell, i.e. the rule no longer reads the page.
    hard = extra + [r for r in out["reasons"] if not r.startswith("skeleton changed")]
    return not hard, hard


def stopped(site_id: str) -> dict:
    return dict(state.get(site_id).get("stopped") or {})


def record(site_id: str, page_type: str, url: str, *, bad: bool, reasons: list[str]) -> bool:
    """One page's verdict into the streak of its type. True when this page stopped the type."""
    fired: dict = {}

    def fn(data: dict) -> None:
        drift = data.setdefault("drift", {})
        entry = drift.setdefault(page_type, {"streak": 0, "pages": []})
        if not bad:
            entry.update(streak=0, pages=[])
            return
        entry["streak"] = int(entry.get("streak") or 0) + 1
        entry["pages"] = (list(entry.get("pages") or []) + [{"url": url, "reasons": reasons[:3]}])[-STREAK:]
        stops = data.setdefault("stopped", {})
        if entry["streak"] >= STREAK and page_type not in stops:
            stops[page_type] = {"at": int(state.now()), "evidence": {"pages": entry["pages"]}}
            fired.update(stops[page_type])
    state.update(site_id, fn)
    if not fired:
        return False
    evidence = fired["evidence"]
    events.emit("structure_changed", site_id=site_id, page_type=page_type, evidence=evidence)
    stage = PAGE.get(page_type, ("", "?"))[1]
    notes.add("warn", f"{site_id}: {LABEL.get(page_type, page_type)} sayfalarının yapısı değişmiş görünüyor "
              f"({STREAK} sayfa üst üste tutmadı); bu tür için tarama durdu, {stage} aşaması yeniden geçince sürer",
              site_id=site_id, ref_kind="crawl", ref_id=page_type)
    return True


def resume(site_id: str, page_type: str) -> bool:
    """The stage of ``page_type`` passed again: the type is crawled again (streak reset)."""
    was = {}

    def fn(data: dict) -> None:
        if page_type in (data.get("stopped") or {}):
            was["yes"] = data["stopped"].pop(page_type)
        (data.get("drift") or {}).pop(page_type, None)
    state.update(site_id, fn)
    return bool(was)
