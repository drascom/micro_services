"""Admin "Site ekle" (site onboarding) API: a pi agent session writes a draft site yaml, the admin reviews and saves it.

`POST /api/ops/onboard`              ``{url, hint?, mode?, site_id?}`` -> 202 ``{draft}`` (409 another run is active (``already_running``, the body
                                     carries ``running`` = {draft_id, url, status}: the draft that holds the slot), 400 URL refused, 403 off).
                                     ``mode: "edit"`` + ``site_id`` (a registered site; ``url`` is ignored, the site's own base_url is used,
                                     404 ``no_such_site``, 409 ``busy`` while it is scanned / healed): an agent run that CHANGES that site
                                     according to ``hint`` (the admin's "what should change?"); the draft has ``mode: "edit"`` / ``edit_site_id``
`GET  /api/ops/onboard`              ``{drafts: [summary]}`` (newest first; each row + ``overall`` = {state, headline, problem_step})
`GET  /api/ops/onboard/{id}`         ``{draft, events, events_total}``; ``?events_after=<n>`` = only the events after the
                                     first n ones (poll with the previous ``events_total``; positions never shift).
                                     ``draft.pipeline`` = the step view ({overall, steps[6], app, quality?}, ``scraper/onboard_pipeline.py``),
                                     built from the final report and, while the agent works, its latest test results
`POST /api/ops/onboard/{id}/message` ``{text}`` feedback: the same pi session runs again (409 while running)
`POST /api/ops/onboard/{id}/cancel`  stop a running draft
`POST /api/ops/onboard/{id}/save`    ``{site_id, display_name?, enable?, force?, scan_now?}`` write the site (yaml v1 + baseline; an EDIT draft:
                                     a new version of its site, ``site_id`` must be that site) -> ``{site_id, version, scan_started, mode, ...}``;
                                     ``scan_now`` (default true) scans the saved site in the background so its titles reach the catalogue
`DELETE /api/ops/onboard/{id}`       delete a draft that is not running
`GET  /api/ops/onboard/health`       pi / skill / extension / model / LLM account state; ``running`` = {draft_id, url, status} | null

A draft the agent asks a question about (``ask_user``) is ``needs_input`` and carries ``question`` (plain text, as always) plus
``question_data`` = ``{kind: missing_info|decision|engine_gap, field, text, tried[], proposal?, options[{id, label, answer?,
answer_prefix?, input?}]}``; the admin's answer is an ordinary ``POST .../message`` whose text is the option's ``answer`` ("Sitede yok,
atla: <field>" / "Var: <hint>" / "Önerini uygula" / "Seçim: <label>"; the first one is also remembered in ``skipped_fields``).
A draft that fails its criteria is sent back to the agent automatically first (``onboard.auto_rounds()``, env ``ONBOARD_AUTO_ROUNDS``):
while that runs the draft is ``running`` with ``auto_round`` / ``auto_rounds`` and a ``status`` event ``auto_fix``. ``draft.pipeline.steps[]``
carry ``actions`` (one-click ``message`` texts for the same message endpoint).

Each event carries ``kind`` (tool, tool_result, say, ask, user, error, retry, status, submit; an event written by the sandbox
only has ``type``, it is copied to ``kind`` here) and a position ``n``. The draft of the answer has no ``events`` list
(they come separately) but ``event_count``, and a ``pipeline`` (every answer that carries a draft); the raw ``live`` tool results
stay in the draft file.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..errors import ApiError
from ..scraper import onboard, onboard_pipeline
from ..scraper import onboard_store as store

router = APIRouter(prefix="/api/ops/onboard")


class StartBody(BaseModel):
    url: str = Field("", max_length=2048, description="site URL (new site; ignored for mode=edit)")
    hint: str = Field("", max_length=2000, description="free note for the agent (e.g. 'only films'); for mode=edit: what should change")
    mode: str = Field("new", max_length=8, description="'new' (default) or 'edit' (change the registered site `site_id`)")
    site_id: str = Field("", max_length=64, description="mode=edit: the registered site to change")


class MessageBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


class SaveBody(BaseModel):
    site_id: str = Field(..., max_length=64)
    display_name: Optional[str] = Field(None, max_length=80)
    enable: bool = Field(False, description="true: the site's automatic scan starts ON")
    force: bool = Field(False, description="save although the acceptance criteria are not met")
    scan_now: bool = Field(True, description="scan the site in the background right after the save (its titles reach the catalogue)")


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except onboard.OnboardError as exc:
        error = ApiError(exc.status, exc.code, exc.message)
        error.detail.update(exc.extra)   # e.g. already_running: ``running`` (the exception handler of main.py passes extra fields on)
        raise error


def _public(draft: dict[str, Any]) -> dict[str, Any]:
    """A draft without its event list and raw live results (+ ``event_count`` and the step view ``pipeline``)."""
    out = {k: v for k, v in draft.items() if k not in ("events", "events_dropped", "live")}
    out["event_count"] = int(draft.get("events_dropped") or 0) + len(draft.get("events") or [])
    out["pipeline"] = onboard_pipeline.safe(draft)
    return out


@router.post("", status_code=202)
@router.post("/", status_code=202, include_in_schema=False)
def start(body: StartBody) -> dict[str, Any]:
    return {"draft": _public(_call(onboard.start, body.url, body.hint, mode=body.mode, site_id=body.site_id))}


@router.get("")
@router.get("/", include_in_schema=False)
def list_drafts() -> dict[str, Any]:
    rows = []
    for draft in store.list_drafts():
        row = onboard.summary(draft)
        row["overall"] = onboard_pipeline.safe(draft)["overall"]
        rows.append(row)
    return {"drafts": rows}


@router.get("/health")
def health() -> dict[str, Any]:
    return onboard.health()


@router.get("/{draft_id}")
def get_draft(draft_id: str, events_after: int = Query(0, ge=0)) -> dict[str, Any]:
    draft = _call(onboard.get, draft_id)
    dropped = int(draft.get("events_dropped") or 0)
    events = draft.get("events") or []
    out = []
    for position, event in enumerate(events, start=dropped):
        if position >= events_after:
            item = dict(event)
            item.setdefault("kind", item.get("type"))
            item["n"] = position
            out.append(item)
    return {"draft": _public(draft), "events": out, "events_total": dropped + len(events)}


@router.post("/{draft_id}/message")
def post_message(draft_id: str, body: MessageBody) -> dict[str, Any]:
    return {"draft": _public(_call(onboard.message, draft_id, body.text))}


@router.post("/{draft_id}/cancel")
def cancel(draft_id: str) -> dict[str, Any]:
    return {"draft": _public(_call(onboard.cancel, draft_id))}


@router.post("/{draft_id}/save")
def save(draft_id: str, body: SaveBody) -> dict[str, Any]:
    return _call(onboard.save, draft_id, body.site_id, body.display_name, body.enable, body.force, body.scan_now)


@router.delete("/{draft_id}")
def delete(draft_id: str) -> dict[str, Any]:
    _call(onboard.delete_draft, draft_id)
    return {"deleted": draft_id}
