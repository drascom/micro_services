"""Admin Yönlendirme (T12): source groups, routing rules, LLM drafts (suggestions) and previews of one site.

    GET    /api/control/sites/{id}/routing                       groups + rules + pending suggestions + roles/categories
    POST   /api/control/sites/{id}/routing/rules                 {match_kind, match_value, target_kind, target_value, priority?,
                                                                   enabled?, note?, all_sites?}   (created_by=admin)
    PUT    /api/control/sites/{id}/routing/rules/{rid}           partial fields
    DELETE /api/control/sites/{id}/routing/rules/{rid}
    POST   /api/control/sites/{id}/routing/preview               {rule fields}  -> {count, examples}
    POST   /api/control/sites/{id}/routing/suggest               one LLM call -> drafts (admin-started, not budget-limited)
    POST   /api/control/sites/{id}/routing/from-text {text}      one LLM call -> one draft + preview
    POST   /api/control/sites/{id}/routing/suggestions/{n}/accept | reject
    POST   /api/control/sites/{id}/routing/suggestions/accept-all
    POST   /api/control/sites/{id}/routing/categories {title}    one-line "kategori ekle"
    POST   /api/control/sites/{id}/routing/apply                 apply now (normally a ``routing`` job does it)
Every change queues the ``routing`` job (no re-crawl needed).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from .. import db
from ..control import routing
from ..control.routing import engine, rules, suggest
from ..core.catalog import categories, lists
from ..errors import ApiError

router = APIRouter()
P = "/api/control/sites/{site_id}/routing"


def _site(site_id: str) -> None:
    if db.query_one("SELECT 1 FROM sites WHERE site_id = ?", (site_id,)) is None:
        raise ApiError(404, "not_found", "Site bulunamadı")


def _guard(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except rules.RoutingError as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None
    except categories.CategoryError as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None


def _rule_of_site(site_id: str, rid: int) -> dict:
    r = rules.get(rid)
    if r is None or (r["site_id"] and r["site_id"] != site_id):
        raise ApiError(404, "not_found", "Kural bulunamadı")
    return r


@router.get(P)
def overview(site_id: str) -> dict[str, Any]:
    _site(site_id)
    cats = [{"slug": r["slug"], "title": r["title"]} for r in db.query(
        "SELECT slug, title FROM home_categories WHERE kind = 'category' ORDER BY position, slug")]
    return {"groups": engine.groups(site_id), "rules": rules.for_site(site_id), "suggestions": suggest.pending(site_id),
            "roles": [{"id": r, "text": lists.ROLES.get(r, "")} for r in rules.ROLES], "categories": cats,
            "match_kinds": list(rules.MATCH_KINDS), "target_kinds": list(rules.TARGET_KINDS)}


@router.post(P + "/rules", status_code=201)
def add_rule(site_id: str, body: dict = Body(default={})) -> dict:
    _site(site_id)
    scope = None if body.get("all_sites") else site_id
    rule = _guard(rules.add, scope, body, created_by="admin")
    routing.schedule(scope)
    return {"rule": rule}


@router.put(P + "/rules/{rid}")
def update_rule(site_id: str, rid: int, body: dict = Body(default={})) -> dict:
    _site(site_id)
    old = _rule_of_site(site_id, rid)
    rule = _guard(rules.update, rid, body)
    routing.schedule(old["site_id"] or None)
    return {"rule": rule}


@router.delete(P + "/rules/{rid}")
def delete_rule(site_id: str, rid: int) -> dict:
    _site(site_id)
    old = _rule_of_site(site_id, rid)
    _guard(rules.delete, rid)
    routing.schedule(old["site_id"] or None)
    return {"ok": True}


@router.post(P + "/preview")
def preview(site_id: str, body: dict = Body(default={})) -> dict:
    _site(site_id)
    return _guard(engine.preview, body, None if body.get("all_sites") else site_id)


@router.post(P + "/suggest")
def run_suggest(site_id: str) -> dict:
    _site(site_id)
    out = suggest.suggest(site_id, background=False)
    if out["error"]:
        raise ApiError(502, "llm_failed", f"LLM yanıtı alınamadı: {out['error']}")
    return out


@router.post(P + "/from-text")
def from_text(site_id: str, body: dict = Body(default={})) -> dict:
    _site(site_id)
    return {"suggestion": _guard(suggest.from_text, site_id, body.get("text") or "")}


@router.post(P + "/suggestions/accept-all")
def accept_all(site_id: str) -> dict:
    _site(site_id)
    saved, failed = [], []
    for s in suggest.pending(site_id):
        try:
            saved.append(suggest.accept(site_id, s["id"]))
        except (rules.RoutingError, categories.CategoryError) as exc:
            failed.append({"id": s["id"], "error": str(exc)})
    routing.schedule(site_id)
    return {"accepted": len(saved), "failed": failed}


@router.post(P + "/suggestions/{sid}/accept")
def accept(site_id: str, sid: int) -> dict:
    _site(site_id)
    rule = _guard(suggest.accept, site_id, sid)
    routing.schedule(site_id)
    return {"rule": rule}


@router.post(P + "/suggestions/{sid}/reject")
def reject(site_id: str, sid: int) -> dict:
    _site(site_id)
    _guard(suggest.reject, site_id, sid)
    return {"ok": True}


@router.post(P + "/categories", status_code=201)
def add_category(site_id: str, body: dict = Body(default={})) -> dict:
    _site(site_id)
    return {"category": _guard(categories.create, body.get("title") or "", body.get("slug") or None)}


@router.post(P + "/apply")
def apply_now(site_id: str) -> dict:
    _site(site_id)
    return engine.apply(site_id)
