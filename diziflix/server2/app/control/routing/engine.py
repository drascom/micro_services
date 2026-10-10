"""Routing engine (T12): rules x ``source_origins`` (+ the title's fields) -> ``library_lists``. LLM-free and fetch-free:
a rule change takes effect with the next ``apply`` (job kind ``routing``, key ``routing:<site>``), no re-crawl.

Units of a record: each origin row ``(kind, value, position)``, each ``genres`` entry not already an origin, the title's
type and its source address. Per unit the matching rules with the highest priority win (an ``ignore`` among them = that
unit goes nowhere; several non-ignore winners = several groups). A record may land in many groups through many units.
List ids: ``<role>_<site>`` / ``category_<slug>_<site>``; order = smallest origin position (field units go last).
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

from ... import db
from ...core.catalog import cache, lists
from ...core.catalog.textnorm import fold
from . import rules as rule_store

FIELD_POS = 1_000_000          # field-derived units (genres field, type, url) sort after every origin position
MATCH_UNIT = {"section": "section", "listing": "listing", "genre": "genre", "type": "type", "url_pattern": "url"}


# --------------------------------------------------------------------------- records

def _json_list(raw: Any) -> list:
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw else raw
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def records(site_id: str, source_key: Optional[str] = None) -> list[dict]:
    """The site's catalogue records with their origins: ``{key, canonical_id, title, type, genres, url, origins[]}``."""
    sql = ("SELECT s.key, s.canonical_id, s.source_url, s.normalized, l.title, l.type, l.genres FROM source_items s "
           "LEFT JOIN library_items l ON l.id = s.canonical_id WHERE s.site_id = ?")
    params: list[Any] = [site_id]
    if source_key is not None:
        sql += " AND s.key = ?"
        params.append(source_key)
    out: dict[str, dict] = {}
    for r in db.query(sql, params):
        norm = {}
        try:
            norm = json.loads(r["normalized"] or "{}") or {}
        except ValueError:
            pass
        genres = _json_list(r["genres"]) or (norm.get("genres") if isinstance(norm.get("genres"), list) else [])
        out[r["key"]] = {"key": r["key"], "canonical_id": r["canonical_id"], "title": r["title"] or norm.get("title") or r["key"],
                         "type": r["type"] or norm.get("type") or "", "genres": [str(g) for g in genres if g],
                         "url": r["source_url"] or "", "origins": []}
    osql = "SELECT source_key, kind, value, position FROM source_origins WHERE site_id = ?"
    oparams: list[Any] = [site_id]
    if source_key is not None:
        osql += " AND source_key = ?"
        oparams.append(source_key)
    for r in db.query(osql + " ORDER BY position, kind, value", oparams):
        rec = out.get(r["source_key"])
        if rec is not None:
            rec["origins"].append((r["kind"], r["value"], int(r["position"] or 0)))
    return list(out.values())


def units(rec: dict) -> list[tuple[str, str, int]]:
    """``(unit kind, value, position)`` of a record: origins, then field genres / type / url."""
    out = [(k, v, p) for k, v, p in rec["origins"]]
    have = {(k, fold(v)) for k, v, _ in out}
    for g in rec.get("genres") or []:
        if ("genre", fold(g)) not in have:
            have.add(("genre", fold(g)))
            out.append(("genre", g, FIELD_POS))
    if rec.get("type") and ("type", fold(rec["type"])) not in have:
        out.append(("type", rec["type"], FIELD_POS))
    if rec.get("url"):
        out.append(("url", rec["url"], FIELD_POS))
    return out


# --------------------------------------------------------------------------- matching

class _Compiled:
    def __init__(self, rule: dict) -> None:
        self.rule = rule
        self.unit = MATCH_UNIT.get(rule["match_kind"], "")
        self.key = fold(rule["match_value"])
        self.regex = None
        if rule["match_kind"] == "url_pattern":
            if rule_store.regex_problem(rule["match_value"]) is None:   # an unsafe stored pattern never runs
                self.regex = re.compile(rule["match_value"])
            else:
                self.unit = ""
        self.rank = (int(rule.get("priority") or 0), 1 if rule.get("site_id") else 0)

    def matches(self, kind: str, value: str) -> bool:
        if kind != self.unit:
            return False
        if self.regex is not None:
            return bool(self.regex.search(str(value or "")))
        return bool(self.key) and fold(value) == self.key


def compile_rules(rule_list: Iterable[dict]) -> list[_Compiled]:
    return [_Compiled(r) for r in rule_list]


def _targets(rec: dict, compiled: list[_Compiled]) -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    for kind, value, pos in units(rec):
        hits = [c for c in compiled if c.matches(kind, value)]
        if not hits:
            continue
        top = max(c.rank[0] for c in hits)
        best = [c for c in hits if c.rank[0] == top]
        if any(c.rule["target_kind"] == "ignore" for c in best):
            continue
        for c in best:
            t = (c.rule["target_kind"], c.rule["target_value"], pos)
            if t not in out:
                out.append(t)
    return out


def targets_for(site_id: str, source_key: str) -> list[tuple[str, str, int]]:
    """``[(target_kind, target_value, position)]`` of one record under the site's enabled rules."""
    recs = records(site_id, source_key)
    if not recs:
        return []
    return _targets(recs[0], compile_rules(rule_store.for_site(site_id, enabled_only=True)))


def _list_id(site_id: str, target_kind: str, target_value: str) -> Optional[str]:
    try:
        if target_kind == "role":
            return lists.list_id(target_value, site_id)
        if target_kind == "category":
            return lists.list_id("category", site_id, target_value)
    except ValueError:
        return None
    return None


# --------------------------------------------------------------------------- apply / preview / groups

def compute(site_id: str) -> dict[str, list[str]]:
    """``{list_id: [canonical ids in order]}`` the enabled rules produce for ``site_id`` (no write)."""
    compiled = compile_rules(rule_store.for_site(site_id, enabled_only=True))
    found: dict[str, dict[str, tuple]] = {}
    if not compiled:
        return {}
    for rec in records(site_id):
        cid = rec["canonical_id"]
        if not cid:
            continue
        for tk, tv, pos in _targets(rec, compiled):
            lid = _list_id(site_id, tk, tv)
            if lid is None:
                continue
            members = found.setdefault(lid, {})
            sort_key = (pos, fold(rec["title"]), cid)
            if cid not in members or sort_key < members[cid]:
                members[cid] = sort_key
    return {lid: [cid for cid, _ in sorted(m.items(), key=lambda kv: kv[1])] for lid, m in found.items()}


def apply(site_id: str) -> dict[str, int]:
    """Rewrite the lists the rules manage for ``site_id``: the ones they produce now, plus the ones routing wrote
    before (emptied when no rule feeds them any more). Every other list is left alone."""
    wanted = compute(site_id)
    before = {r["list_id"] for r in db.query("SELECT list_id FROM routing_lists WHERE site_id = ?", (site_id,))}
    items = 0
    for lid, cids in wanted.items():
        items += lists.set_list(lid, cids)
    cleared = 0
    for lid in sorted(before - set(wanted)):
        lists.set_list(lid, [])
        cleared += 1
    with db.transaction() as conn:
        conn.execute("DELETE FROM routing_lists WHERE site_id = ?", (site_id,))
        conn.executemany("INSERT INTO routing_lists (site_id, list_id) VALUES (?, ?)", [(site_id, lid) for lid in wanted])
    cache.refresh_soon()
    return {"lists_written": len(wanted), "items": items, "cleared": cleared}


def _sites_with_records() -> list[str]:
    return [r["site_id"] for r in db.query("SELECT DISTINCT site_id FROM source_items ORDER BY site_id")]


def preview(rule: Any, site_id: Optional[str] = None) -> dict[str, Any]:
    """How many titles the (unsaved) rule matches, alone, plus 5 examples. Writes nothing."""
    clean = rule_store.clean(rule, allow_new_category=True)
    scope = rule.get("site_id") if isinstance(rule, dict) else None
    sites = [site_id] if site_id else ([scope] if scope else _sites_with_records())
    comp = _Compiled({**clean, "site_id": site_id or scope})
    hits: list[tuple] = []
    seen: set = set()
    for sid in sites:
        for rec in records(sid):
            matched = [(pos, value) for kind, value, pos in units(rec) if comp.matches(kind, value)]
            ident = rec["canonical_id"] or f"{sid}:{rec['key']}"
            if matched and ident not in seen:
                seen.add(ident)
                pos, value = min(matched)
                hits.append((pos, fold(rec["title"]), {"title": rec["title"], "canonical_id": rec["canonical_id"],
                                                       "site_id": sid, "matched": value}))
    hits.sort(key=lambda h: h[:2])
    return {"count": len(hits), "examples": [h[2] for h in hits[:5]]}


def groups(site_id: str) -> list[dict[str, Any]]:
    """Source groups of the site (origin kind + value, field genres too): title count, 3 examples, where they go now."""
    compiled = compile_rules(rule_store.for_site(site_id, enabled_only=True))
    out: dict[tuple, dict] = {}
    for rec in records(site_id):
        for kind, value, pos in units(rec):
            if kind == "url":
                continue
            g = out.setdefault((kind, fold(value)), {"kind": kind, "value": value, "count": 0, "examples": [],
                                                     "from_field": pos >= FIELD_POS, "targets": []})
            g["count"] += 1
            if len(g["examples"]) < 3:
                g["examples"].append(rec["title"])
    for (kind, _), g in out.items():
        hits = [c for c in compiled if c.matches(kind, g["value"])]
        if hits:
            top = max(c.rank[0] for c in hits)
            best = [c for c in hits if c.rank[0] == top]
            g["targets"] = ([{"target_kind": "ignore", "target_value": "", "rule_id": c.rule["id"]} for c in best
                             if c.rule["target_kind"] == "ignore"][:1]
                            or [{"target_kind": c.rule["target_kind"], "target_value": c.rule["target_value"],
                                 "rule_id": c.rule["id"]} for c in best])
    order = {"section": 0, "listing": 1, "genre": 2, "type": 3}
    return sorted(out.values(), key=lambda g: (order.get(g["kind"], 9), g["from_field"], -g["count"], fold(g["value"])))
