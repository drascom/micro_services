"""LLM drafts of routing rules (T12, K6): ``suggest`` (one call: the site's source groups -> a yaml rule list) and
``from_text`` (admin's free text -> one rule). Drafts are validated and wait in ``routing_suggestions``; nothing is applied
until the admin accepts. An unknown target category is kept as "yeni kategori öner: <ad>" (no category is opened by itself).
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from ... import db
from ...core.catalog import categories, lists
from ...core.llm import client as llm, parse as llm_parse, prompts
from ..sitemap import site_notes
from . import engine, rules as rule_store

MAX_GROUPS = 60
MAX_DRAFTS = 30
MAX_TEXT = 500

_RULES = [
    "Answer with ONE fenced yaml block, nothing else.",
    "match_kind is one of: section, listing, genre, type, url_pattern. match_value is copied from the evidence "
    "(type: movie|series; url_pattern: a regex on the title address).",
    "target_kind is one of: role, category, ignore. role targets: only the roles listed below. category targets: the slug of "
    "an existing category, or the NAME of a new category you propose (it is only a proposal).",
    "Use ignore for groups that should go to no special group. priority: integer, higher wins on conflicts (default 0).",
    "Do not invent groups that are not in the evidence.",
]
_FORMAT_LIST = ("```yaml\nrules:\n  - {match_kind: section, match_value: \"<group value>\", target_kind: role, "
                "target_value: latest_episodes, priority: 0, note: \"<why, short>\"}\n```")
_FORMAT_ONE = ("```yaml\nrule: {match_kind: genre, match_value: \"<value>\", target_kind: category, target_value: \"<slug or "
               "new name>\", priority: 0, note: \"<short>\"}\n```")


def _evidence(site_id: str) -> dict[str, str]:
    groups = [g for g in engine.groups(site_id) if g["kind"] in ("section", "listing", "genre", "type")][:MAX_GROUPS]
    glines = [f"- {g['kind']}: {g['value']!r} ({g['count']} titles; e.g. {', '.join(g['examples'])})" for g in groups]
    roles = [f"- {r}: {lists.ROLES.get(r, '')}" for r in rule_store.ROLES]
    cats = [f"- {r['slug']}: {r['title']}" for r in db.query(
        "SELECT slug, title FROM home_categories WHERE kind = 'category' ORDER BY position, slug")]
    return {"source groups of the site": "\n".join(glines) or "(none)", "roles": "\n".join(roles),
            "categories (slug: title)": "\n".join(cats) or "(none)"}


def _store(site_id: str, draft: dict, origin: str) -> Optional[int]:
    rule = {k: v for k, v in draft.items() if k != "new_category"}
    key = (rule["match_kind"], rule["match_value"], rule["target_kind"], rule["target_value"])
    for r in rule_store.for_site(site_id):
        if (r["match_kind"], r["match_value"], r["target_kind"], r["target_value"]) == key:
            return None
    for r in db.query("SELECT rule FROM routing_suggestions WHERE site_id = ? AND state = 'pending'", (site_id,)):
        old = json.loads(r["rule"])
        if (old["match_kind"], old["match_value"], old["target_kind"], old["target_value"]) == key:
            return None
    cur = db.execute("INSERT INTO routing_suggestions (site_id, rule, new_category, origin, state, created_at) "
                     "VALUES (?,?,?,?, 'pending', ?)", (site_id, json.dumps(rule, ensure_ascii=False),
                                                        draft.get("new_category"), origin, int(time.time())))
    return int(cur.lastrowid)


def _call(site_id: str, task: str, evidence: dict, fmt: str, purpose: str, background: bool):
    prompt = site_notes.with_note(site_id, prompts.build(task, _RULES, evidence, fmt))   # the site note leads (cacheable)
    reply = llm.complete(prompt, purpose=purpose, site_id=site_id, background=background, timeout=90)
    if not reply.ok:
        return None, reply.error or "llm_failed"
    data, err = llm_parse.extract_yaml(reply.text)
    if data is None:
        return None, f"reply_{err}"
    return data, None


def suggest(site_id: str, *, background: bool = False) -> dict[str, Any]:
    """ONE LLM call -> validated drafts stored as pending suggestions. ``{drafts, invalid, error}``."""
    data, err = _call(site_id, "Propose routing rules: which of this site's source groups should feed which home role or "
                      "category. Only groups that clearly fit a role/category; leave the rest out.",
                      _evidence(site_id), _FORMAT_LIST, "routing_suggest", background)
    if err:
        return {"drafts": [], "invalid": 0, "error": err}
    raw = data.get("rules") if isinstance(data.get("rules"), list) else []
    drafts, invalid = [], 0
    for item in raw[:MAX_DRAFTS]:
        try:
            draft = rule_store.clean(item, allow_new_category=True)
        except rule_store.RoutingError:
            invalid += 1
            continue
        sid = _store(site_id, draft, "suggest")
        if sid is not None:
            drafts.append(get(sid))
    invalid += max(0, len(raw) - MAX_DRAFTS)
    return {"drafts": drafts, "invalid": invalid, "error": None}


def from_text(site_id: str, text: str) -> dict[str, Any]:
    """Admin's free text -> ONE draft (pending suggestion, ``origin=text``) + its preview."""
    text = " ".join(str(text or "").split())[:MAX_TEXT]
    if not text:
        raise rule_store.RoutingError(400, "empty_text", "Metin boş")
    evidence = {"admin request": text, **_evidence(site_id)}
    data, err = _call(site_id, "Turn the admin request into exactly ONE routing rule for this site.", evidence,
                      _FORMAT_ONE, "routing_text", False)
    if err:
        raise rule_store.RoutingError(502, "llm_failed", f"LLM yanıtı alınamadı: {err}")
    item = data.get("rule")
    if item is None and isinstance(data.get("rules"), list) and data["rules"]:
        item = data["rules"][0]
    if item is None and "match_kind" in data:
        item = data
    try:
        draft = rule_store.clean(item, allow_new_category=True)
    except rule_store.RoutingError as exc:
        raise rule_store.RoutingError(422, "invalid_draft", f"LLM'in kuralı geçersiz: {exc}") from None
    sid = _store(site_id, draft, "text")
    if sid is None:
        raise rule_store.RoutingError(409, "duplicate", "Bu kural zaten var ya da öneri olarak bekliyor")
    return get(sid) or {}


# --------------------------------------------------------------------------- pending suggestions

def get(sid: int, *, with_preview: bool = True) -> Optional[dict]:
    r = db.query_one("SELECT * FROM routing_suggestions WHERE id = ?", (int(sid),))
    if r is None:
        return None
    out = dict(r)
    out["rule"] = json.loads(r["rule"])
    if with_preview:
        try:
            out["preview"] = engine.preview(out["rule"], r["site_id"])
        except rule_store.RoutingError:
            out["preview"] = {"count": 0, "examples": []}
    return out


def pending(site_id: str) -> list[dict]:
    return [get(r["id"]) for r in db.query(
        "SELECT id FROM routing_suggestions WHERE site_id = ? AND state = 'pending' ORDER BY id", (site_id,))]


def _decide(site_id: str, sid: int) -> dict:
    s = get(sid, with_preview=False)
    if s is None or s["site_id"] != site_id:
        raise rule_store.RoutingError(404, "not_found", "Öneri bulunamadı")
    if s["state"] != "pending":
        raise rule_store.RoutingError(409, "already_decided", "Bu öneri zaten karara bağlandı")
    return s


def accept(site_id: str, sid: int) -> dict:
    """Admin approval: a proposed new category is opened now (explicit admin act), then the rule is saved ``created_by=llm``."""
    s = _decide(site_id, sid)
    rule = dict(s["rule"])
    if s.get("new_category") and not categories.exists(rule["target_value"]):
        try:
            categories.create(s["new_category"], rule["target_value"])
        except categories.CategoryError as exc:
            raise rule_store.RoutingError(exc.status, exc.code, str(exc)) from None
    saved = rule_store.add(site_id, rule, created_by="llm")
    db.execute("UPDATE routing_suggestions SET state = 'accepted', rule_id = ?, decided_at = ? WHERE id = ?",
               (saved["id"], int(time.time()), int(sid)))
    return saved


def reject(site_id: str, sid: int) -> None:
    _decide(site_id, sid)
    db.execute("UPDATE routing_suggestions SET state = 'rejected', decided_at = ? WHERE id = ?", (int(time.time()), int(sid)))
