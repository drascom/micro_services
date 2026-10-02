"""Site-onboarding job manager: runs the pi agent in the background, keeps its progress in a draft, saves the result.

``start(url, hint)`` takes the single onboarding slot (``state.activity_start("_onboard", "onboard")``), creates a draft
(``onboard_store``), issues a sandbox token and runs ``pi -p --mode json`` in a thread. The agent only has the eleven
tools of ``server/pi/extensions/diziflix-onboard.ts`` (ten sandbox tools + ``ask_user``) and the skill
``server/pi/skills/diziflix-site-onboarding``; its JSON event stream is boiled down to ``events`` of the draft (``tool``,
``tool_result``, ``say``, ``ask``, ``user``, ``error``, ``retry``). When the run ends the draft is ``ready`` (the agent called
``submit_draft``), ``needs_input`` (it did not; its last message is the question, ``draft["question"]``; when it called
``ask_user`` the question is also ``draft["question_data"]``, see ``question_data()``), ``failed`` (``reason``: ``pi_failed`` =
non-zero exit / crash, ``provider_error`` = the model provider failed while pi still exited 0, ``timeout``, ``server_restart``)
or ``cancelled``.

SELF-CORRECTION: a ``ready`` draft whose report says ``passed: false`` with a non-empty ``failing`` list (and no question
pending) is not handed to the admin yet: the same pi session gets an automatic message (``auto_fix_message``: the failing
criteria, their hints and the diagnostics) and runs again, at most ``auto_rounds()`` (``ONBOARD_AUTO_ROUNDS``, default 2) times
in a row inside the one run thread (slot and token are kept; the draft is ``running`` and carries ``auto_round`` /
``auto_rounds``). The admin's answers to a question are plain messages (``ANSWER_*``); "Sitede yok, atla: <field>" is also
remembered (``skipped_fields``) so a skipped field is never auto-corrected.
``message`` re-runs the same pi session with the user's feedback, ``save`` writes the site (yaml v1 + baseline
thresholds, auto-scan OFF unless asked) together with the draft's provider recipes (``configs/providers/<name>.yaml`` v1:
the reusable provider library, all validated before anything is written), ``cancel`` / ``delete_draft`` / ``health`` / ``recover_stale`` complete it.

``start(..., mode="edit", site_id=...)`` changes a REGISTERED site instead: the draft carries ``mode: "edit"`` / ``edit_site_id`` / the
current yaml / the admin's request (``hint``), the agent runs with ``DIZIFLIX_MODE=edit`` (the onboarding tools + ``load_site_config``)
and ``save`` writes a NEW VERSION of that site (old one archived, baseline thresholds refreshed, ``last_good`` kept). ``save(...,
scan_now=True)`` then scans the site in the background so the result reaches the catalogue. ``health()['running']`` and the
``already_running`` error say WHICH draft holds the single slot (``running_info``); a holder that is already finished (status ``ready``
...: pi is only winding down) or lost its thread never blocks a new run (``_take_slot`` reclaims it).

Nothing here parses a site: the sandbox (``routers/onboard_sandbox.py``) does that. The pi runner itself (command line
``build_command``, event field names ``_ev_*``, process supervision ``run``) lives in ``scraper/pi_agent.py`` and is shared
with the repair agent of the heal (``scraper/heal_agent.py``); the names below are thin aliases / wrappers of it. It was
checked against a real pi 0.99.1 in the Faz 2b spike (``docs/plans/site-onboarding/pi-spike-notes.md``); what the spike
could not measure stays marked ``UNVERIFIED``.
"""
from __future__ import annotations

import logging
import math
import os
import re
import shutil
import subprocess   # noqa: F401  (pi_agent starts the process through the same module object; tests patch ``onboard.subprocess.Popen``)
import threading
import time
from typing import Any, Optional
from urllib.parse import urlsplit

import yaml

from .. import config, llm_health, netguard, settings
from . import config as scfg
from . import onboard_store as store
from . import onboard_pipeline, pi_agent, state

log = logging.getLogger("scraper.onboard")

SLOT_SITE, SLOT_KIND = "_onboard", "onboard"
SITE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
SERVER_DIR = pi_agent.SERVER_DIR
SKILL_DIR = pi_agent.SKILL_DIR
EXTENSION_PATH = pi_agent.EXTENSION_PATH
TOOLS = pi_agent.ONBOARD_TOOLS
SKILL_NAME = pi_agent.SKILL_NAME
READ_TOOL = pi_agent.READ_TOOL   # pi's own file reader, allowed for the skill references only (extension guard, DIZIFLIX_SKILL_DIR)

MAX_EVENTS = 500
SAY_CLIP = pi_agent.SAY_CLIP
SUMMARY_CLIP = pi_agent.SUMMARY_CLIP
ARGS_CLIP = pi_agent.ARGS_CLIP
STDERR_CLIP = pi_agent.STDERR_CLIP
HINT_CLIP = 2000
KILL_GRACE = pi_agent.KILL_GRACE

_ACTIVE = ("running",)
_RESUMABLE = ("ready", "needs_input", "failed")

_lock = threading.RLock()
_procs: dict[str, Any] = {}              # draft id -> Popen (restart loses it)
_threads: dict[str, threading.Thread] = {}
_cancelled: set[str] = set()
_reaped: set[str] = set()   # drafts whose pi was killed to free the slot for a new run (its non-zero exit is no error then)
_save_lock = threading.Lock()
MODES = ("new", "edit")
SLOT_RECLAIM_AFTER = 3.0   # seconds a slot must be held before a holder without a live thread counts as lost (start-up window)


class OnboardError(Exception):
    """A request that cannot be served (the router maps ``status``/``code``/``message`` to an ``ApiError``)."""

    def __init__(self, status: int, code: str, message: str, extra: Optional[dict] = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.extra = dict(extra or {})   # more fields of the error body (``already_running``: ``running``)


# --- small helpers ----------------------------------------------------------------------------------------------

def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# The pi runner (command line, child environment, JSON event stream, process supervision) lives in ``pi_agent`` and is
# shared with the repair agent of the heal; what follows are the names onboarding (and its tests) have always used.
_clip = pi_agent.clip
model = pi_agent.model
sandbox_url = pi_agent.sandbox_url
sessions_dir = pi_agent.sessions_dir
work_dir = pi_agent.work_dir
scrub = pi_agent.scrub
EventParser = pi_agent.EventParser


def child_env(token: str, draft_id: str, skill_dir: Optional[str] = None) -> dict[str, str]:
    """The environment of an onboarding pi run (``pi_agent.child_env``; the skill directory defaults to ``SKILL_DIR``)."""
    return pi_agent.child_env(token, draft_id, skill_dir or SKILL_DIR)


def build_command(draft_id: str, *, pi_bin: Optional[str] = None, model_name: Optional[str] = None,
                  extension: Optional[str] = None, skill: Optional[str] = None,
                  session_dir: Optional[str] = None) -> list[str]:
    """The pi argv of one onboarding run (``pi_agent.build_command`` with the eleven onboarding tools)."""
    return pi_agent.build_command(draft_id, tools=TOOLS, pi_bin=pi_bin, model_name=model_name or model(),
                                  extension=extension or EXTENSION_PATH, skill=skill or SKILL_DIR, session_dir=session_dir)


CATEGORIES_MAX = 40


def categories_line() -> str:
    """``Mevcut kategoriler: kore-dizileri (Kore Dizileri), anime (Anime)`` (``library/categories``, enabled or not; ``yok`` when
    there is none): the slugs a ``role: category`` collection may name. Never raises (a missing registry reads as none)."""
    try:
        from . import collections as site_collections
        cats = [c for c in site_collections.known_categories() if isinstance(c, dict) and c.get("slug")]
    except Exception:
        cats = []
    shown = ", ".join(f"{c['slug']} ({str(c.get('title') or c['slug']).strip()})" for c in cats[:CATEGORIES_MAX])
    return f"Mevcut kategoriler: {shown or 'yok'}"


def first_message(url: str, hint: str = "", categories: str = "") -> str:
    """Opening message: pi's ``/skill:<name> <text>`` command (verified in print/json mode from stdin, multi-line text
    included: pi embeds the SKILL.md body in the first user message and appends ``<text>``). ``categories`` (``categories_line()``)
    follows, then the user's note."""
    lines = [f"/skill:{SKILL_NAME} {url}"]
    if categories.strip():
        lines.append(categories.strip())
    if hint.strip():
        lines.append(f"Kullanıcı notu: {hint.strip()}")
    return "\n".join(lines)


def edit_first_message(site_id: str, hint: str = "") -> str:
    """Opening message of an EDIT run (the skill's EDIT mode, ``references/edit.md``): the registered site and the admin's request."""
    request = hint.strip() or "(none given: load the config, test it and fix only what fails)"
    return (f"/skill:{SKILL_NAME} EDIT mode for site {site_id}. User request: {request}\n"
            f"This is NOT an onboarding of a new site. Read references/edit.md of the skill and follow it. Start with "
            f"load_site_config(\"{site_id}\"), make the narrowest change that satisfies the request, verify with "
            f"test_config(playable: true, collections: true), finish with submit_draft (site_id_suggestion = \"{site_id}\").")


# --- questions of the agent (ask_user) --------------------------------------------------------------------------

ASK_KINDS = ("missing_info", "decision", "engine_gap")
QUESTION_CLIP = 600
TRIED_MAX, TRIED_CLIP, PROPOSAL_CLIP, OPTIONS_MAX, OPTION_LABEL_CLIP = 8, 200, 400, 4, 80

#: the admin's answers are plain messages; the skill (``Missing information protocol``) tells the agent how to read each one
ANSWER_ABSENT = "Sitede yok, atla: {field}"      # not (always) on the site: an information field is KEPT (optional), go on
ANSWER_PRESENT = "Var: "                          # + the admin's free-text hint where it is: search again with it
ANSWER_APPLY = "Önerini uygula"                   # do what the agent proposed
ANSWER_CHOICE = "Seçim: {label}"                  # a custom option of a "decision" question
_SKIP_RE = re.compile(r"^\s*Sitede yok, atla\s*:\s*(\S.*?)\s*$", re.I | re.S)
_OPTION_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")

#: what a field is about decides whether "it is not on the site" is an answer at all. Playability and the list itself cannot be
#: skipped (a site without them is no site): for them the question is "try this / save with force", never "skip".
_UNSKIPPABLE_TOKENS = frozenset({"play", "playable", "playback", "stream", "streams", "player", "resolver", "resolvers", "provider",
                                 "providers", "video", "videos", "inventory", "normalize", "selector", "list", "row", "key"})
_UNSKIPPABLE_NAMES = frozenset({"title", "detail_url", "valid_count", "series_page", "episodes", "episode_list", "base_url", "fetch",
                                "fetch_mode", "availability_gate"})   # the gate keeps copyright / unplayable titles out: never "not on the site"
#: the hardening criteria's own "it is not on the site" answers (``onboard_sandbox`` ``SKIP_*``): a series section on the home page,
#: and a series page that lists the episodes ("inventory" is otherwise an unskippable token)
_SKIPPABLE_NAMES = frozenset({"home_series_section", "series_inventory"})


def skippable(field: Any) -> bool:
    """May the admin answer "it is not on the site" for ``field``? False for playability / list / identity fields."""
    name = re.sub(r"[^a-z0-9_]+", "_", str(field or "").strip().lower()).strip("_")
    if name in _SKIPPABLE_NAMES:
        return True
    if name in _UNSKIPPABLE_NAMES:
        return False
    return not (set(name.split("_")) & _UNSKIPPABLE_TOKENS)


def question_data(args: Any) -> Optional[dict]:
    """The structured question of an ``ask_user`` call (``args``: the tool arguments), or None when it carries no question text.

        {"kind": "missing_info" | "decision" | "engine_gap", "field", "text", "tried": [str], "proposal"?: str,
         "options": [{"id", "label", "answer"?: <message sent when clicked>, "answer_prefix"?: str, "input"?: True}]}

    ``missing_info`` offers ``absent`` ("Sitede yok, atla"), ``present`` ("Var, ben göstereyim": the admin types a hint, the message
    is ``answer_prefix`` + hint) and, with a proposal, ``apply`` ("Önerilen: ..."). A ``missing_info`` question about a field that
    cannot be skipped (``skippable``) is turned into a ``decision`` (no ``absent``). ``decision``: the agent's own ``options``
    (``answer`` = "Seçim: <label>"), else ``apply`` (with a proposal) + ``present`` ("Ben yönlendireyim"). ``engine_gap``: no
    options (a missing engine feature: nothing the admin can click away; the panel shows a copyable "Sistemde eksik özellik" card).
    Fields the agent sent are clipped, never trusted."""
    given = pi_agent._args_dict(args)
    text = _clip(given.get("question") or given.get("text"), QUESTION_CLIP)
    if not text:
        return None
    field = _clip(given.get("field"), 60) or "?"
    kind = given.get("kind") if given.get("kind") in ASK_KINDS else "missing_info"
    if kind == "missing_info" and not skippable(field):
        kind = "decision"
    raw_tried = given.get("tried")
    tried = [t for t in (_clip(x, TRIED_CLIP) for x in (raw_tried if isinstance(raw_tried, list) else [])[:TRIED_MAX]) if t]
    proposal = _clip(given.get("proposal"), PROPOSAL_CLIP)
    options: list[dict] = []
    if kind == "missing_info":
        options.append({"id": "absent", "label": "Varsa al, yoksa atla" if onboard_pipeline._optional_field(field) else "Sitede yok, atla",
                        "answer": ANSWER_ABSENT.format(field=field)})
        options.append({"id": "present", "label": "Var, ben göstereyim", "input": True, "answer_prefix": ANSWER_PRESENT})
        if proposal:
            options.append({"id": "apply", "label": _clip("Önerilen: " + proposal, OPTION_LABEL_CLIP + 12), "answer": ANSWER_APPLY})
    elif kind == "decision":
        own = given.get("options")
        for item in (own if isinstance(own, list) else [])[:OPTIONS_MAX]:
            if not isinstance(item, dict):
                continue
            label = _clip(item.get("label"), OPTION_LABEL_CLIP)
            oid = str(item.get("id") or "").strip().lower()
            if label and _OPTION_ID_RE.match(oid) and oid not in {o["id"] for o in options}:
                options.append({"id": oid, "label": label, "answer": ANSWER_CHOICE.format(label=label)})
        if not options:
            if proposal:
                options.append({"id": "apply", "label": _clip("Önerilen: " + proposal, OPTION_LABEL_CLIP + 12), "answer": ANSWER_APPLY})
            options.append({"id": "present", "label": "Ben yönlendireyim", "input": True, "answer_prefix": ANSWER_PRESENT})
    out: dict[str, Any] = {"kind": kind, "field": field, "text": text, "tried": tried, "options": options}
    if proposal:
        out["proposal"] = proposal
    return out


def skipped_fields(draft: Optional[dict]) -> list[str]:
    """The fields the admin answered "Sitede yok, atla" for (lower case)."""
    raw = (draft or {}).get("skipped_fields")
    return [str(f).strip().lower() for f in (raw if isinstance(raw, list) else []) if str(f).strip()]


def _note_answer(text: str, draft: dict) -> dict:
    """Draft fields a message of the admin adds: ``skipped_fields`` grows when it is "Sitede yok, atla: a, b"."""
    found = _SKIP_RE.match(text or "")
    if not found:
        return {}
    names = [n.strip().lower() for n in re.split(r"[,;]", found.group(1)) if n.strip()]
    merged = list(dict.fromkeys([*skipped_fields(draft), *(_clip(n, 60) for n in names)]))[:40]
    return {"skipped_fields": merged}


# --- self-correction: the automatic rounds ------------------------------------------------------------------------

AUTO_ROUNDS_DEFAULT, AUTO_ROUNDS_MAX = 2, 5
#: failing criterion -> the keys of ``report.diagnostics`` that explain it (the sandbox's evidence: first rows, what a selector caught)
_DIAG_KEYS = {"valid_count": ("list",), "title_fill": ("list",), "detail_url_fill": ("list",), "poster_url_fill": ("list",),
              "normalize_ok_ratio": ("list",), "duplicate_key_ratio": ("list",), "detail_fill": ("detail",),
              "series_inventory_ok": ("series",), "series_have_episode_sources": ("series", "player"),
              "collections_valid_count": ("collections",), "collections_normalize_ok_ratio": ("collections",),
              "search_ok": ("search",), "playable_ratio": ("player",),
              "series_signal_collection": ("collections",), "series_full_inventory": ("series", "list"),
              "home_path_is_canonical": ("list", "collections"), "availability_gate_defined": ("player",),
              "collection_poster_fill": ("collections",), "detail_info_defined": ("detail",), "ingest_sample_ok": ("series",)}
#: an admin "Sitede yok, atla: <field>" that covers a criterion whose name does not contain the field (the hardening exemptions)
_SKIP_COVERS = {"home_series_section": ("series_signal_collection",), "series_inventory": ("series_full_inventory",)}
#: criteria no admin answer covers (the gate keeps copyright / unplayable titles out; ``availability_gate`` is not a field the site may lack;
#: ``ingest_sample_ok``: series that come from episode cards must be readable whatever the site offers)
_NEVER_SKIPPED = frozenset({"availability_gate_defined", "ingest_sample_ok"})
DIAG_CLIP, MESSAGE_CLIP, FAIL_HINT_CLIP = 500, 5200, 700   # (the hardening hints are several sentences: a message of 5+ failing items still fits)


def auto_rounds() -> int:
    """How many automatic correction rounds one run may add (``ONBOARD_AUTO_ROUNDS``, default 2, 0 = off, at most 5); read at
    call time (the env value, not an import-time constant)."""
    try:
        return max(0, min(AUTO_ROUNDS_MAX, int(float(os.environ.get("ONBOARD_AUTO_ROUNDS", AUTO_ROUNDS_DEFAULT)))))
    except (TypeError, ValueError):
        return AUTO_ROUNDS_DEFAULT


def _field_matches(criterion: str, field: str) -> bool:
    """Does an admin-skipped ``field`` (``poster``, ``poster_url``, ``collection:trending``) cover a failing ``criterion``
    (``poster_url_fill``)?"""
    crit, name = criterion.lower(), re.sub(r"[^a-z0-9_]+", "_", field.lower()).strip("_")
    return bool(name) and (crit == name or crit.startswith(name + "_") or name in crit.split("_") or crit in _SKIP_COVERS.get(name, ()))


def open_failing(report: Any, skipped: Optional[list] = None) -> list[dict]:
    """``report.failing`` (``[{criterion, value, bound, hint}]``, written by the sandbox) without what the admin skipped; a report
    without that key gives ``[]`` (nothing to correct automatically)."""
    items = (report or {}).get("failing") if isinstance(report, dict) else None
    out: list[dict] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        criterion = str(item.get("criterion") or item.get("name") or "").strip()
        if not criterion or (criterion not in _NEVER_SKIPPED and any(_field_matches(criterion, f) for f in (skipped or []))):
            continue
        out.append({"criterion": criterion, "value": item.get("value"), "bound": item.get("bound"), "hint": item.get("hint")})
    return out


def auto_fix_message(report: dict, round_no: int, rounds: int, failing: list) -> str:
    """The message of an automatic correction round: what failed (``failing`` with ``hint``), the diagnostics that belong to it and
    what to do about it. English like the skill; the admin sees only the one-line event ``Otomatik düzeltme turu n/N``."""
    lines = [f"AUTOMATIC CORRECTION ROUND {round_no}/{rounds}. The draft you handed in does not meet the acceptance criteria "
             "(it is NOT ready for the admin). Fix it yourself:", ""]
    for item in failing[:8]:
        line = f"- {item['criterion']}: value {item.get('value')!r}, bound {item.get('bound')!r}"
        if item.get("hint"):
            line += f". Hint: {_clip(item['hint'], FAIL_HINT_CLIP)}"
        lines.append(line)
    wanted: list[str] = []
    for item in failing:
        for key in _DIAG_KEYS.get(item["criterion"], ()):
            if key not in wanted:
                wanted.append(key)
    shown = [(k, onboard_pipeline.diag_text(onboard_pipeline.diagnostics_for(report, k), DIAG_CLIP)) for k in wanted]
    shown = [(k, t) for k, t in shown if t]
    if shown:
        lines += ["", "Diagnostics of the failing pages (what your selectors caught, first rows):"] + [f"- {k}: {t}" for k, t in shown]
    warns = [_clip(w, 200) for w in ((report or {}).get("warnings") or [])[:5]] if isinstance(report, dict) else []
    if warns:
        lines += ["", "Warnings of the same run (solve the solvable ones too):"] + [f"- {w}" for w in warns]
    blocked = (report or {}).get("blocked") if isinstance(report, dict) else None
    if isinstance(blocked, dict) and blocked.get("count"):
        lines += ["", f"{blocked.get('count')} item(s) are blocked (copyright / access) and are not counted: do not fight them "
                      "(references/blocked.md)."]
    lines += ["", "For EACH problem: find the CAUSE with evidence (query_html / grep_page on the failing page: what did the selector catch, "
              "why is the field empty), change the yaml, run test_config(playable: true, collections: true) again and call submit_draft "
              "again. At most 4 attempts per problem. If you cannot fix it, or the information is not on the site, do NOT submit it as it "
              "is: call ask_user (what you tried + your proposal)."]
    return _clip_block("\n".join(lines), MESSAGE_CLIP)


def _clip_block(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _auto_event_text(round_no: int, rounds: int, failing: list) -> str:
    names = []
    for item in failing[:4]:
        label = onboard_pipeline.criterion_label(item["criterion"])
        names.append(label)
    more = len(failing) - len(names)
    return f"Otomatik düzeltme turu {round_no}/{rounds}: " + ", ".join(names) + (f" (+{more})" if more > 0 else "")


# --- draft events -----------------------------------------------------------------------------------------------

def add_event(draft_id: str, event: dict) -> None:
    """Append one event; only the newest ``MAX_EVENTS`` stay (``events_dropped`` counts the cut ones, so the absolute
    position of an event never changes: that is what the router's ``events_after`` counts)."""
    with store._lock:
        draft = store.append_event(draft_id, event)
        if draft is None:
            return
        extra = len(draft.get("events") or []) - MAX_EVENTS
        if extra > 0:
            store.update_draft(draft_id, events=draft["events"][extra:],
                               events_dropped=int(draft.get("events_dropped") or 0) + extra)


def _record(draft: dict, status: str, started: float, turns: int, notes: str = "") -> None:
    report = draft.get("report") or {}
    state.record_ops_onboard({
        "draft_id": draft.get("id"), "url": draft.get("url"), "site_id": draft.get("saved_site_id") or draft.get("site_id_suggestion") or None,
        "site": "onboard", "status": status, "at": _now(), "seconds": round(time.monotonic() - started, 1),
        "turns": turns, "passed": report.get("passed") if report else None,
        "notes": _clip(notes or report.get("notes") or draft.get("error") or "", 500)})


# --- running ----------------------------------------------------------------------------------------------------

_terminate = pi_agent.terminate


def _release(token: str) -> None:
    """The run is over for good: the single slot is freed and the sandbox token revoked. Never raises."""
    from ..routers import onboard_sandbox as sandbox
    state.activity_end(SLOT_SITE, SLOT_KIND)
    try:
        sandbox.revoke_token(token)
    except Exception:
        pass


def _run(draft_id: str, message: str, token: str) -> None:
    """The pi run(s) of ``draft_id`` (a thread). ``message`` goes to stdin; ``token`` is revoked at the end, the slot freed.

    Usually ONE pi run. When it hands in a draft that fails its criteria, ``_finish`` asks for an automatic correction round: the
    same thread then runs pi again in the same session with ``auto_fix_message`` (slot and token are kept, the draft stays
    ``running``), at most ``auto_rounds()`` times; cancel / timeout / a question of the agent end the loop like any run."""
    started = time.monotonic()
    round_no = 0     # automatic correction rounds done so far
    turns = 0        # tool turns of the earlier rounds (the ops record counts all of them)
    holding = True   # this thread still owns the slot and the token
    try:
        while True:
            def on_start(proc: Any) -> bool:
                with _lock:
                    _procs[draft_id] = proc
                    return draft_id in _cancelled   # cancel() came while pi was starting

            if round_no:
                with _lock:
                    gone = draft_id in _cancelled   # cancel() came between two rounds: no new pi
                    _cancelled.discard(draft_id)
                if gone:
                    holding = False
                    _finish(draft_id, started, pi_agent.EventParser(), None, "", False, True, "", token, turns_before=turns)
                    return
            draft = store.get_draft(draft_id) or {}
            edit = draft.get("mode") == "edit"   # an edit draft stays an edit run for its follow-up messages
            job = (pi_agent.Job(draft_id, message, "edit", pi_agent.EDIT_TOOLS, {"DIZIFLIX_SITE_ID": draft.get("edit_site_id") or ""}) if edit
                   else pi_agent.Job(draft_id, message, "onboard", TOOLS))
            result = pi_agent.run(job, token=token, timeout=float(config.ONBOARD_TIMEOUT),
                                  on_event=lambda event: add_event(draft_id, event),
                                  on_label=lambda label, phase: state.activity_update(SLOT_SITE, SLOT_KIND, label=label, phase=phase),
                                  on_start=on_start, model_name=model(), extension=EXTENSION_PATH, skill=SKILL_DIR)
            with _lock:
                _procs.pop(draft_id, None)
                cancelled = draft_id in _cancelled
                _cancelled.discard(draft_id)
                reaped = draft_id in _reaped
                _reaped.discard(draft_id)
            if reaped:   # killed on purpose after the draft was handed in / ended: its exit code says nothing
                result.exit_code, result.crash = (0 if result.exit_code is not None else None), ""
            holding = False   # _finish frees the slot itself, unless it asks for another round
            follow = _finish(draft_id, started, result.parser, result.exit_code, result.crash, result.timed_out, cancelled, result.stderr,
                             token, reaped=reaped, auto_round=round_no, turns_before=turns)
            if follow is None:
                return
            holding = True
            round_no += 1
            turns += result.parser.turns
            message = follow
            state.activity_update(SLOT_SITE, SLOT_KIND, label=f"otomatik düzeltme {round_no}/{auto_rounds()}", phase="start", draft_id=draft_id)
    except BaseException:
        if holding:
            _release(token)
        raise


def _snapshot_pipeline(draft_id: str) -> None:
    """The run is over: store the step view (``pipeline``) in the draft; a draft that has its final ``report`` drops the
    ``live`` tool results (the report replaces them: the next feedback round starts from it). Never raises."""
    try:
        draft = store.get_draft(draft_id)
        if draft is None:
            return
        fields: dict[str, Any] = {"pipeline": onboard_pipeline.for_draft(draft)}
        if draft.get("report"):
            fields["live"] = None
        store.update_draft(draft_id, **fields)
    except Exception:
        log.exception("onboarding pipeline snapshot of %s failed", draft_id)


def _begin_auto_round(draft_id: str, round_no: int) -> Optional[str]:
    """Decide an automatic correction round for the ``ready`` draft that was just handed in: the message of the round (and the draft
    is ``running`` again, ``auto_round`` / ``auto_rounds`` set, one ``Otomatik düzeltme turu`` event), or None when the draft goes
    to the admin as it is: the rounds are used up (or switched off), the report passed / has no ``failing`` list / only failures the
    admin skipped, the draft is an EDIT of a registered site, or it is no longer ``ready`` (cancelled / saved / deleted meanwhile). The check and the flip to
    ``running`` happen under the save lock, so a save of the same draft either wins (no round) or is refused (``bad_state``)."""
    rounds = auto_rounds()
    if round_no >= rounds:
        return None
    with _save_lock:
        draft = store.get_draft(draft_id)
        if not draft or draft.get("status") != "ready" or draft.get("question_data") or draft.get("mode") == "edit":
            return None   # (an edit of a registered site is judged on what the change touches: some criteria stay unmet by design)
        report = draft.get("report") or {}
        if report.get("passed") is not False:
            return None
        failing = open_failing(report, skipped_fields(draft))
        if not failing:
            return None
        text = auto_fix_message(report, round_no + 1, rounds, failing)
        updated = store.update_draft(draft_id, status="running", error=None, question=None, question_data=None, reason=None,
                                     auto_round=round_no + 1, auto_rounds=rounds)
        if updated is None:
            return None
    add_event(draft_id, {"t": _now(), "kind": "status", "status": "auto_fix", "round": round_no + 1, "rounds": rounds,
                         "text": _auto_event_text(round_no + 1, rounds, failing)})
    return text


def _is_question(text: str) -> bool:
    """Does the agent's last message end as a question (``?``, trailing quotes / brackets / spaces ignored)?"""
    return (text or "").rstrip().rstrip("\"'”’)]*_` ").endswith("?")


def _finish(draft_id: str, started: float, parser: EventParser, exit_code: Optional[int], crash: str,
            timed_out: bool, cancelled: bool, stderr: str, token: str, *, reaped: bool = False, auto_round: int = 0,
            turns_before: int = 0) -> Optional[str]:
    """Final status of a run, the ops record, the slot and the token. Never raises. Returns None, or (only for a clean run that
    handed in a failing draft, ``_begin_auto_round``) the message of the next AUTOMATIC correction round: the draft is then
    ``running`` again and the slot and token are NOT released (``_run`` runs pi again)."""
    follow: Optional[str] = None
    try:
        draft = store.get_draft(draft_id)
        if draft is None:
            return None
        asked = question_data(parser.ask) if parser.asking else None   # the agent's last word is ask_user
        if cancelled or draft.get("status") == "cancelled":
            status, notes = "cancelled", ""
            store.update_draft(draft_id, status="cancelled", error=None)
        elif asked is not None:
            status, notes = "needs_input", asked["text"]
            store.update_draft(draft_id, status="needs_input", error=None, question=asked["text"], question_data=asked)
        elif draft.get("status") == "ready":   # the agent called submit_draft during this run
            status, notes = "ready", ""
            if exit_code not in (0, None) or crash:
                add_event(draft_id, {"t": _now(), "kind": "error",
                                     "text": _clip(crash or f"pi çıkış kodu {exit_code} (taslak teslim edilmişti)", 300)})
            store.update_draft(draft_id, question=None, question_data=None)
            if not reaped and not timed_out and not crash and exit_code in (0, None):
                follow = _begin_auto_round(draft_id, auto_round)
        elif timed_out:
            status, notes = "failed", f"zaman aşımı: pi {config.ONBOARD_TIMEOUT:g} sn içinde bitmedi"
            store.update_draft(draft_id, status="failed", error=notes, reason="timeout")
        elif crash or exit_code not in (0, None):
            detail = crash or scrub(stderr, STDERR_CLIP, (token,)) or f"pi çıkış kodu {exit_code}"
            status, notes = "failed", detail
            store.update_draft(draft_id, status="failed", error=detail, reason="pi_failed")
        elif parser.provider_error:   # pi exited 0, but the last assistant message ended with stopReason "error"
            status, notes = "failed", scrub(parser.provider_error, SUMMARY_CLIP, (token,))
            store.update_draft(draft_id, status="failed", error=notes, reason="provider_error")
        elif draft.get("report") and draft.get("yaml_text") and not _is_question(parser.last_say):
            # a draft was handed in EARLIER and this run (a chat answer) neither changed it nor asked anything: it is ready again
            status, notes = "ready", "Taslak değişmedi, hazır"
            store.update_draft(draft_id, status="ready", error=None, reason=None, question=None, question_data=None)
            add_event(draft_id, {"t": _now(), "kind": "status", "status": "ready", "text": notes})
        else:
            status, notes = "needs_input", parser.last_say
            store.update_draft(draft_id, status="needs_input", error=None, question=parser.last_say or None, question_data=None)
        if follow is None:
            if status in ("failed", "needs_input"):
                add_event(draft_id, {"t": _now(), "kind": "status", "status": status,
                                     "text": _clip(notes or "pi bir şey teslim etmeden bitti", 300)})
            _snapshot_pipeline(draft_id)
            _record(store.get_draft(draft_id) or draft, status, started, parser.turns + turns_before, notes)
    except Exception:
        log.exception("finishing onboarding run of %s failed", draft_id)
        follow = None
    finally:
        if follow is None:
            _release(token)
    return follow


def _spawn(draft_id: str, message: str, token: str) -> None:
    thread = threading.Thread(target=_run, args=(draft_id, message, token), name=f"onboard-{draft_id}", daemon=True)
    with _lock:
        _threads[draft_id] = thread
    thread.start()


def join(draft_id: str, timeout: Optional[float] = None) -> bool:
    """Wait for the run thread of ``draft_id`` (tests / shutdown); True when it is done."""
    with _lock:
        thread = _threads.get(draft_id)
    if thread is None:
        return True
    thread.join(timeout)
    return not thread.is_alive()


def _require_enabled() -> None:
    if not config.ONBOARD_ENABLED:
        raise OnboardError(403, "disabled", "site onboarding is switched off (ONBOARD_ENABLED=0)")


def _slot_holder() -> Optional[dict]:
    return next((a for a in state.activity_list() if a["site"] == SLOT_SITE and a["kind"] == SLOT_KIND), None)


def _holder_alive(draft_id: str) -> bool:
    with _lock:
        thread = _threads.get(draft_id)
    return thread is not None and thread.is_alive()


def _reap_lost() -> bool:
    """The slot is held by a run whose thread is gone (nothing will ever free it): free it, and fail its draft when that still says
    ``running``. True when a slot was freed. A holder younger than ``SLOT_RECLAIM_AFTER`` s may still be starting its thread."""
    holder = _slot_holder()
    draft_id = (holder or {}).get("draft_id")
    if not holder or not draft_id or float(holder.get("elapsed") or 0) < SLOT_RECLAIM_AFTER or _holder_alive(draft_id):
        return False
    state.activity_end(SLOT_SITE, SLOT_KIND)
    draft = store.get_draft(draft_id)
    if draft and draft.get("status") in _ACTIVE:
        store.update_draft(draft_id, status="failed", reason="pi_failed", error="iş sahipsiz kaldı (süreç yok)")
        add_event(draft_id, {"t": _now(), "kind": "error", "text": "iş sahipsiz kaldı; yer açıldı"})
    return True


def _reap_finished() -> bool:
    """The slot is held by a draft that is NOT running any more (``ready`` after ``submit_draft``, cancelled, ...) while its pi still
    winds down: pi is stopped and waited for, so the slot is free. True when the holder is gone afterwards."""
    holder = _slot_holder()
    draft_id = (holder or {}).get("draft_id")
    if not holder or not draft_id:
        return False
    draft = store.get_draft(draft_id)
    if draft is not None and draft.get("status") in _ACTIVE:
        return False
    with _lock:
        proc = _procs.get(draft_id)
        _reaped.add(draft_id)
    if proc is not None:
        _terminate(proc)
    done = join(draft_id, KILL_GRACE + 2.0)
    with _lock:
        _reaped.discard(draft_id)
    return done


def running_info() -> Optional[dict]:
    """``{draft_id, url, status}`` of the draft that holds the onboarding slot and is really running, else None (no holder, or a
    holder whose draft already ended: ``ready`` / ``failed`` / ``cancelled`` ... pi is only winding down, nothing to wait for)."""
    _reap_lost()
    holder = _slot_holder()
    draft = store.get_draft((holder or {}).get("draft_id") or "") if holder else None
    if draft is None or draft.get("status") not in _ACTIVE:
        return None
    return {"draft_id": draft["id"], "url": draft.get("url"), "status": draft.get("status")}


def _take_slot(trigger: str) -> None:
    if state.activity_start(SLOT_SITE, SLOT_KIND, trigger):
        return
    if (_reap_lost() or _reap_finished()) and state.activity_start(SLOT_SITE, SLOT_KIND, trigger):
        return
    raise OnboardError(409, "already_running", "another onboarding run is in progress", {"running": running_info()})


def _edit_target(site_id: str) -> "scfg.SiteConfig":
    """The registered site an edit run changes (404 ``no_such_site``; 409 ``busy`` while it is scanned / healed)."""
    site_id = (site_id or "").strip()
    if not SITE_ID_RE.match(site_id) or site_id not in scfg.list_sites():
        raise OnboardError(404, "no_such_site", f"site {site_id!r} is not a registered site")
    for job in state.activity_list():
        if job["site"] == site_id and job["kind"] in ("scan", "heal"):
            raise OnboardError(409, "busy", f"site {site_id!r} is busy ({job['kind']} is running); try again when it is done")
    try:
        return scfg.load_site(site_id)
    except Exception as exc:
        raise OnboardError(409, "no_such_site", f"the config of site {site_id!r} cannot be read ({type(exc).__name__})")


def start(url: str = "", hint: str = "", trigger: str = "admin", mode: str = "new", site_id: str = "") -> dict:
    """Begin an onboarding run for ``url``; returns the new draft (``status=running``). ``mode="edit"`` (``site_id`` of a registered
    site, ``url`` ignored: the site's ``base_url``) starts an EDIT run: the draft is ``mode: "edit"`` / ``edit_site_id`` /
    ``site_id_suggestion = site_id`` / ``yaml_text`` = the current yaml / ``hint`` = what the admin wants changed."""
    from ..routers import onboard_sandbox as sandbox
    _require_enabled()
    if mode not in MODES:
        raise OnboardError(422, "invalid_mode", "mode must be 'new' or 'edit'")
    edit_cfg = _edit_target(site_id) if mode == "edit" else None
    if edit_cfg is not None:
        url = edit_cfg.base_url
    elif not (url or "").strip():
        raise OnboardError(422, "url_required", "url is required for a new site")
    try:
        url = netguard.check_url((url or "").strip())
    except ValueError as exc:
        raise OnboardError(400, "url_rejected", str(exc))
    hint = (hint or "")[:HINT_CLIP]
    _take_slot(trigger)
    token = ""
    try:
        draft = store.create_draft(url, site_id_suggestion=edit_cfg.site_id if edit_cfg is not None else "")
        if edit_cfg is None and hint:   # the first request of a new site goes into its handoff note
            draft = store.update_draft(draft["id"], hint=hint) or draft
        if edit_cfg is not None:
            try:
                with open(edit_cfg.path, "r", encoding="utf-8") as fh:
                    current = fh.read()
            except OSError:
                current = ""
            draft = store.update_draft(draft["id"], mode="edit", edit_site_id=edit_cfg.site_id, edit_from_version=edit_cfg.version,
                                       yaml_text=current, hint=hint) or draft
        token = sandbox.issue_token(draft["id"])
        state.activity_update(SLOT_SITE, SLOT_KIND, label=urlsplit(url).hostname or url, phase="start", draft_id=draft["id"])
        _spawn(draft["id"], edit_first_message(edit_cfg.site_id, hint) if edit_cfg is not None else first_message(url, hint, categories_line()), token)
    except BaseException:
        if token:
            sandbox.revoke_token(token)
        state.activity_end(SLOT_SITE, SLOT_KIND)
        raise
    return draft


def message(draft_id: str, text: str, trigger: str = "admin") -> dict:
    """Feedback of the user: the same pi session runs again with ``text`` (draft ``ready | needs_input | failed``). The answers to a
    question of the agent (``question_data`` options) are plain messages of this kind (``ANSWER_*``)."""
    from ..routers import onboard_sandbox as sandbox
    _require_enabled()
    text = (text or "").strip()
    if not text:
        raise OnboardError(422, "empty_message", "the message is empty")
    draft = store.get_draft(draft_id)
    if draft is None:
        raise OnboardError(404, "not_found", f"draft {draft_id!r} not found")
    if draft.get("status") not in _RESUMABLE:
        raise OnboardError(409, "bad_state", f"draft is {draft.get('status')}; a message needs ready, needs_input or failed")
    _take_slot(trigger)
    token = ""
    try:
        token = sandbox.issue_token(draft_id)
        add_event(draft_id, {"t": _now(), "kind": "user", "text": text[:SAY_CLIP]})
        # a message answers the open question (question_data) and gives the automatic rounds a fresh start; "Sitede yok, atla: x" is remembered
        draft = store.update_draft(draft_id, status="running", error=None, question=None, question_data=None, reason=None,
                                   auto_round=0, auto_rounds=None, **_note_answer(text, draft))
        state.activity_update(SLOT_SITE, SLOT_KIND, label="geri bildirim", phase="start", draft_id=draft_id)
        _spawn(draft_id, text, token)
    except BaseException:
        if token:
            sandbox.revoke_token(token)
        state.activity_end(SLOT_SITE, SLOT_KIND)
        raise
    return draft


def cancel(draft_id: str) -> dict:
    """Stop a running draft: SIGTERM, SIGKILL after 5 s; ``status=cancelled``."""
    draft = store.get_draft(draft_id)
    if draft is None:
        raise OnboardError(404, "not_found", f"draft {draft_id!r} not found")
    if draft.get("status") not in _ACTIVE:
        raise OnboardError(409, "not_running", f"draft is {draft.get('status')}, nothing to cancel")
    with _lock:
        proc = _procs.get(draft_id)
        _cancelled.add(draft_id)
    draft = store.update_draft(draft_id, status="cancelled", error=None, question=None)
    add_event(draft_id, {"t": _now(), "kind": "status", "status": "cancelled", "text": "iptal edildi"})
    if proc is not None:
        _terminate(proc)
    else:
        with _lock:
            thread = _threads.get(draft_id)
            if thread is None or not thread.is_alive():   # no run to notice it (lost on a restart): nothing cleans up
                _cancelled.discard(draft_id)
    return draft


def delete_draft(draft_id: str) -> None:
    draft = store.get_draft(draft_id)
    if draft is None:
        raise OnboardError(404, "not_found", f"draft {draft_id!r} not found")
    if draft.get("status") in _ACTIVE:
        raise OnboardError(409, "running", "the draft is running; cancel it first")
    with store._lock:
        try:
            os.unlink(os.path.join(store.root(), "drafts", draft_id + ".json"))
        except OSError:
            pass
    try:   # pi's session files, verified naming "<ISO time>_<draft id>.jsonl" (Faz 2b): best effort
        folder = sessions_dir()
        for name in os.listdir(folder):
            if draft_id in name:
                path = os.path.join(folder, name)
                shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else os.unlink(path)
    except OSError:
        pass


def recover_stale() -> int:
    """Startup: drafts still ``running`` lost their process with the old server -> ``failed`` (``reason=server_restart``)."""
    count = 0
    with _lock:
        live = set(_procs)
    for draft in store.list_drafts("running"):
        if draft["id"] in live:
            continue
        store.update_draft(draft["id"], status="failed", reason="server_restart",
                           error="sunucu yeniden başladı; iş yarıda kaldı (server_restart)")
        add_event(draft["id"], {"t": _now(), "kind": "error", "text": "sunucu yeniden başladı; iş yarıda kaldı"})
        count += 1
    return count


# --- reading ----------------------------------------------------------------------------------------------------

def get(draft_id: str) -> dict:
    draft = store.get_draft(draft_id)
    if draft is None:
        raise OnboardError(404, "not_found", f"draft {draft_id!r} not found")
    return draft


def summary(draft: dict) -> dict:
    """List row of a draft (no yaml, report or events)."""
    report = draft.get("report") or {}
    return {"id": draft.get("id"), "url": draft.get("url"), "status": draft.get("status"),
            "created_at": draft.get("created_at"), "updated_at": draft.get("updated_at"),
            "site_id_suggestion": draft.get("site_id_suggestion") or "", "saved_site_id": draft.get("saved_site_id"),
            "mode": draft.get("mode") or "new", "edit_site_id": draft.get("edit_site_id"),
            "error": draft.get("error"), "question": draft.get("question"), "has_yaml": bool(draft.get("yaml_text")),
            "passed": report.get("passed") if report else None,
            "event_count": int(draft.get("events_dropped") or 0) + len(draft.get("events") or []),
            "turns": sum(1 for e in draft.get("events") or [] if e.get("kind") == "tool")}


def list_drafts() -> list[dict]:
    return [summary(d) for d in store.list_drafts()]


def health() -> dict:
    """What the admin start button needs: pi, skill + extension files, model, LLM account (cached ``pi auth check``) and ``running``
    = ``{draft_id, url, status}`` of the run that holds the single slot, None when nobody does (a draft that already ended is never
    "running", even while its pi winds down: ``running_info``)."""
    pi_bin = config.ONBOARD_PI_BIN
    found = shutil.which(pi_bin)
    name = model()
    skill_file = os.path.join(SKILL_DIR, "SKILL.md")
    out = {"enabled": bool(config.ONBOARD_ENABLED),
           "pi": {"bin": pi_bin, "found": bool(found)},
           "skill": {"path": SKILL_DIR, "exists": os.path.isfile(skill_file)},
           "extension": {"path": EXTENSION_PATH, "exists": os.path.isfile(EXTENSION_PATH)},
           "model": name, "model_set": bool(name),
           "timeout": config.ONBOARD_TIMEOUT,
           "running": running_info()}
    try:
        out["llm"] = llm_health.check()
    except Exception as exc:  # pragma: no cover - llm_health is defensive itself
        out["llm"] = {"status": "error", "message": type(exc).__name__}
    out["ready"] = bool(out["enabled"] and out["pi"]["found"] and out["skill"]["exists"] and out["extension"]["exists"]
                        and out["model_set"] and out["llm"].get("status") == llm_health.VALID)
    return out


# --- save -------------------------------------------------------------------------------------------------------

def thresholds_from(report: dict) -> dict:
    """Drift thresholds of the new site from the measured list parse: ``min_items`` = max(5, 60% of the valid items),
    ``critical_field_fill`` (title / detail_url / poster_url, only the ones the list fills) = measured - 0.15 but at
    least 0.5 (never above what was measured: the site must not start in drift), ``min_fill_ratio`` = the aggregate over
    the schema's key fields - 0.15 (clamped 0.1..0.9). A report whose ``series`` block read series pages (the yaml has a
    ``series_page``) adds ``series_page: {min_items}`` = max(1, 50% of the measured average episode count): the floor the
    ``series_crawl`` drift check reads from the same baseline."""
    from . import schema
    block = (report or {}).get("list") or {}
    fill = block.get("field_fill") or {}
    out: dict[str, Any] = {"min_items": max(5, math.floor(int(block.get("valid_count") or 0) * 0.6))}
    critical = {}
    for name in ("title", "detail_url", "poster_url"):
        measured = fill.get(name)
        if isinstance(measured, (int, float)) and measured > 0:
            critical[name] = round(min(float(measured), max(0.5, float(measured) - 0.15)), 2)
    out["critical_field_fill"] = critical
    keys = schema._KEY_FIELDS.get((report or {}).get("schema") or "MovieItem") or schema._KEY_FIELDS["MovieItem"]
    aggregate = sum(float(fill.get(k) or 0.0) for k in keys) / len(keys)
    out["min_fill_ratio"] = round(max(0.1, min(0.9, aggregate - 0.15)), 2)
    counts = [int(s["episodes"]) for s in ((report or {}).get("series") or {}).get("samples") or []
              if isinstance(s, dict) and isinstance(s.get("episodes"), int) and not isinstance(s["episodes"], bool) and s["episodes"] > 0]
    if counts:
        out["series_page"] = {"min_items": max(1, math.floor(sum(counts) / len(counts) * 0.5))}
    return out


def _read_active_yaml(site_id: str) -> str:
    try:
        with open(scfg._active_path(site_id), "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _site_taken(site_id: str) -> bool:
    return (site_id in scfg.list_sites() or bool(scfg.archived_versions(site_id))
            or os.path.exists(os.path.join(scfg.CONFIG_DIR, f"{site_id}.baseline.json")))


def _rekey_collections(data: dict, site_id: str, old_sites: list[str]) -> list[tuple[str, str]]:
    """Rewrite, in place, the ``collections:`` ids of ``data`` to ``collections.list_id(role, site_id)`` (the id the
    ingest writes its list under, the home rows read by role, and the sandbox's collection check demands). Touched:
    home roles (``collections.HOME_ROLES``), role ``category`` (``category_<slug>_<old site>``) and any other role whose id is ``<role>_<old site>`` (``old_sites``: the
    yaml's own ``site_id`` / the agent's suggestion; with none known, any ``<role>_<valid site id>``); every other id
    is left alone. Returns [(old id, new id)] of the ones changed."""
    from . import collections as site_collections
    raw = data.get("collections")
    if not isinstance(raw, list):
        return []
    changed: list[tuple[str, str]] = []
    for spec in raw:
        if not isinstance(spec, dict):
            continue
        role, cid = spec.get("role"), spec.get("id")
        if not isinstance(role, str) or not role or not isinstance(cid, str):
            continue
        if role == site_collections.CATEGORY_ROLE:   # category_<slug>_<site>: the slug stays, the site part follows
            slug = site_collections.category_of(spec)
            if site_collections.check_category_slug(slug):
                continue
            if old_sites:
                if cid not in {site_collections.list_id(role, old, slug) for old in old_sites}:
                    continue
            elif not (cid.startswith(f"category_{slug}_") and SITE_ID_RE.match(cid[len(f"category_{slug}_"):])):
                continue
            want = site_collections.list_id(role, site_id, slug)
            if cid != want:
                spec["id"] = want
                changed.append((cid, want))
            continue
        if role in site_collections.HOME_ROLES:
            pass
        elif old_sites:
            if cid not in {site_collections.list_id(role, old) for old in old_sites}:
                continue
        elif not (cid.startswith(role + "_") and SITE_ID_RE.match(cid[len(role) + 1:])):
            continue
        want = site_collections.list_id(role, site_id)
        if cid != want:
            spec["id"] = want
            changed.append((cid, want))
    return changed


def _recipe_conflicts(items: list[dict]) -> Optional[str]:
    """The first provider-recipe name of the draft that is taken (a code provider, a recipe of the library, an archive left
    behind) or used twice; None when all are free."""
    from .providers import recipes
    seen: set = set()
    for item in items:
        name = str(item.get("name") or "").strip()
        if name in seen:
            return f"provider recipe {name!r} is listed twice"
        seen.add(name)
        if name in recipes.CODE_NAMES:
            return f"provider recipe {name!r} is the name of a code provider"
        if item.get("mode") == "update":   # a new version of a library recipe (host added): validated by ``_prepare_recipes``
            if name not in scfg.recipe_names():
                return f"provider recipe {name!r} is marked mode update but there is no such recipe in the library"
            continue
        if name in scfg.recipe_names() or scfg.recipe_archived_versions(name):
            return (f"provider recipe {name!r} already exists in the library (reference it in providers:, update it with mode update to add a "
                    "host, or choose another name)")
    return None


_SECRET_KEY = r"(?:cookie|authorization|proxy-authorization|x-api-key|api[_-]?key|token|secret|password)"
_MASKED_LINE = re.compile(r"^(\s*(%s)\s*:\s*)\*\*\*\s*$" % _SECRET_KEY, re.I | re.M)
_REAL_LINE = re.compile(r"^\s*(%s)\s*:\s*((?!\*\*\*\s*$)\S.*)$" % _SECRET_KEY, re.I | re.M)


def _restore_masked(new_text: str, old_text: str) -> str:
    """``load_site_config`` shows an agent the active yaml with secret-looking values masked (``cookie: ***``); a yaml it submits
    with the mask still in must not overwrite the real value on an edit save: the n-th masked line of a key takes the value of the
    n-th real line of that key in the active yaml (a masked line without a counterpart stays as it is)."""
    real: dict[str, list[str]] = {}
    for m in _REAL_LINE.finditer(old_text):
        real.setdefault(m.group(1).lower(), []).append(m.group(2))
    seen: dict[str, int] = {}

    def fill(m: "re.Match") -> str:
        key = m.group(2).lower()
        index, seen[key] = seen.get(key, 0), seen.get(key, 0) + 1
        values = real.get(key) or []
        return m.group(1) + values[index] if index < len(values) else m.group(0)
    return _MASKED_LINE.sub(fill, new_text)


def _write_handoff(draft: dict, site_id: str, data: dict, report: dict, version: Any, edit_site: str, force: bool) -> bool:
    """The site handoff note of a save (``scraper/site_handoff``): a NEW site gets its first note (the agent's ``handoff`` text, else its
    ``notes``, + the deterministic parts the server builds from the draft), an EDIT adds one change entry. Never raises: a note that
    cannot be written does not stop the save."""
    try:
        from . import site_handoff
        facts = site_handoff.facts_from_draft(draft, data, version, force=force, report=report)
        agent = str(draft.get("handoff") or "").strip()
        if edit_site:
            text = agent
            if not text:   # no handoff from the agent: what the server knows
                changed = ", ".join(str(k) for k in ((report.get("edit") or {}).get("changed_keys") or [])[:12])
                text = "\n".join(x for x in (f"İstek: {draft.get('hint')}" if draft.get("hint") else "",
                                              f"Değişen anahtarlar: {changed}" if changed else "", str((report or {}).get("notes") or "")[:300]) if x)
            return site_handoff.append_change(site_id, text or "(açıklama yok)", facts, title="Düzenleme v%s" % version)
        return site_handoff.write_initial(site_id, agent or str((report or {}).get("notes") or ""), facts)
    except Exception:
        log.exception("onboarding: handoff note of %s not written", site_id)
        return False


def _start_scan(site_id: str) -> bool:
    """Scan ``site_id`` in the background (what ``POST /api/ops/sites/{site}/scan`` runs: ingest + cache refresh), so the result of
    a save reaches the catalogue. False when a scan of the site is already running or the thread cannot start; never raises."""
    if not state.activity_start(site_id, "scan", "onboard"):
        return False

    def work() -> None:
        try:
            from ..routers import ops
            ops._bg_scan(site_id)   # frees the activity itself
        except BaseException:
            log.exception("onboarding: scan of %s failed", site_id)
        finally:
            state.activity_end(site_id, "scan")

    try:
        threading.Thread(target=work, name=f"onboard-scan-{site_id}", daemon=True).start()
    except Exception:
        state.activity_end(site_id, "scan")
        log.exception("onboarding: scan of %s not started", site_id)
        return False
    return True


def save(draft_id: str, site_id: str, display_name: Optional[str] = None, enable: bool = False,
         force: bool = False, scan_now: bool = False) -> dict:
    """Write the draft as a new site: ``configs/<site_id>.yaml`` v1 + baseline thresholds; the site's auto-scan is saved
    as OFF (``enable=True`` opens it). The collection ids are first rewritten to ``<role>_<site_id>`` of the chosen id
    (``_rekey_collections``); that yaml is tested once more (the sandbox's own ``_analyze`` with ``collections=True``, ``playable=True`` and ``search_stage=True``: one live
    search query when the yaml has ``search:``, criterion ``search_ok``); ``passed=false`` (collection, playability and search
    criteria included) is refused unless ``force``.

    An EDIT draft (``mode: "edit"``) saves a NEW VERSION of its registered site instead: ``site_id`` must be the edited site (422
    ``site_id_locked``), it must still exist (409 ``no_such_site``) and not be healed right now (409 ``busy``), the draft must hold
    the agent's submission (409 ``no_yaml``); the active yaml is archived as ``<site>.vN.yaml`` (``save_new_version``), the baseline
    thresholds are refreshed (``last_good`` and the other keys stay), the auto-scan setting the site has is left alone (only
    ``enable=True`` opens it) and a mask the agent's yaml still carries is replaced by the real value (``_restore_masked``).

    ``scan_now`` (the admin API defaults it to true) starts a background scan of the site after the save (``_start_scan``); the
    answer says ``scan_started`` (False: a scan was already running) and ``mode``.

    The provider recipes the draft carries (``provider_recipes``) are written with it, each as ``configs/providers/<name>.yaml``
    v1. Everything is checked first (a name that is a code provider's or already in the library is a 409, an invalid recipe a
    422; ``force`` only skips the ``passed`` criteria, never these); then the recipes are written, then the site; when any
    write fails the ones already written are removed again, so the library never gets a half-saved draft. A recipe entry with ``mode:
    "update"`` is a NEW VERSION of a library recipe (``<name>.vN.yaml`` archive like a site's): ``recipes.update_problems`` accepts only
    the host widening ``match_providers`` proposes; on a failure it goes back to its previous version (``config.restore_recipe``)."""
    from ..routers import onboard_sandbox as sandbox
    site_id = (site_id or "").strip()
    if not SITE_ID_RE.match(site_id):
        raise OnboardError(422, "invalid_site_id", "site_id must match ^[a-z][a-z0-9_]{1,31}$")
    with _save_lock:
        draft = get(draft_id)
        if draft.get("status") in _ACTIVE or draft.get("status") in ("cancelled", "saved"):
            raise OnboardError(409, "bad_state", f"draft is {draft.get('status')}; it cannot be saved")
        yaml_text = draft.get("yaml_text") or ""
        edit_site = (draft.get("edit_site_id") or "") if draft.get("mode") == "edit" else ""
        if not yaml_text.strip() or (edit_site and not draft.get("report")):   # an edit draft starts with the current yaml, that is no submission
            raise OnboardError(409, "no_yaml", "the draft has no yaml yet (the agent has not submitted one)")
        if edit_site:
            if site_id != edit_site:
                raise OnboardError(422, "site_id_locked", f"this draft edits site {edit_site!r}; the site id cannot change")
            if site_id not in scfg.list_sites():
                raise OnboardError(409, "no_such_site", f"site {site_id!r} no longer exists (it was deleted); start a new site instead")
            if any(a["site"] == site_id and a["kind"] == "heal" for a in state.activity_list()):
                raise OnboardError(409, "busy", f"site {site_id!r} is being healed; save when that is done")
            yaml_text = _restore_masked(yaml_text, _read_active_yaml(site_id))
        elif _site_taken(site_id):
            raise OnboardError(409, "site_exists", f"site {site_id!r} already exists (or has archived versions)")
        recipe_items = [r for r in (draft.get("provider_recipes") or []) if isinstance(r, dict) and r.get("name")]
        taken = _recipe_conflicts(recipe_items)
        if taken:
            raise OnboardError(409, "recipe_exists", taken)
        prepared = sandbox._prepare_recipes(recipe_items)
        if prepared.errors:
            raise OnboardError(422, "invalid_recipe", _clip("; ".join(prepared.errors), 400))
        data, problem = sandbox._load_yaml(yaml_text)
        if problem:
            raise OnboardError(422, "invalid_yaml", problem)
        data = dict(data)
        data.pop("version", None)
        old_sites = [s for s in dict.fromkeys([data.get("site_id"), draft.get("site_id_suggestion")])
                     if isinstance(s, str) and s != site_id and SITE_ID_RE.match(s)]
        _rekey_collections(data, site_id, old_sites)   # the ids follow the site id the admin chose
        data["site_id"] = site_id
        data["display_name"] = (display_name or "").strip() or str(data.get("display_name") or "").strip() or site_id
        try:   # what is tested is what is written (the rewritten ids included)
            tested_yaml = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        except yaml.YAMLError as exc:
            raise OnboardError(422, "invalid_yaml", f"yaml: {type(exc).__name__}")
        edit_info = sandbox._edit_summary(site_id, data) if edit_site else None   # against the ACTIVE yaml, before it is replaced
        with sandbox.module_site(edit_site):   # an edit: the site's own code modules are found by its real id
            report = sandbox._analyze(tested_yaml, None, None, time.monotonic() + config.ONBOARD_TOOL_TIMEOUT,
                                      collections=True, site_hint=site_id, playable=True, draft_recipes=prepared, search_stage=True,
                                      harden=not edit_site, skipped=skipped_fields(draft))   # a NEW site meets the hardening criteria too
        if not report.get("passed") and not force:
            failed = [k for k, c in (report.get("criteria") or {}).items() if not c.get("ok")]
            raise OnboardError(409, "not_passed", "the draft does not meet the criteria (" + ", ".join(failed)
                               + "); " + _clip("; ".join(report.get("errors") or []), 300) + " (force=true saves it anyway)")
        warnings: list[str] = []
        written: list[dict] = []
        snapshots: dict[str, dict] = {}
        updates = {r["name"] for r in recipe_items if r.get("mode") == "update"}
        try:   # recipes first (they are what the site yaml's providers: names); any failure undoes the recipes written so far
            for provider in prepared.providers:
                snapshots[provider.name] = scfg.recipe_snapshot(provider.name)
                version_written = scfg.save_recipe(provider.name, provider.data)
                written.append({"name": provider.name, "version": version_written, **({"mode": "update"} if provider.name in updates else {})})
            version = scfg.save_new_version(site_id, data)
            limits = thresholds_from(report)
            scfg.write_baseline_thresholds(site_id, limits)
        except Exception:
            for entry in written:   # a new recipe is removed again, an updated one goes back to its previous version
                try:
                    scfg.restore_recipe(entry["name"], snapshots[entry["name"]])
                except Exception:
                    log.exception("onboarding: could not undo provider recipe %s", entry["name"])
            raise
        clear = getattr(scfg, "clear_tombstone", None)   # config.py (site deletion): a site saved again is no longer a tombstone (deploy keeps it away otherwise)
        if callable(clear):
            try:
                clear(site_id)
            except Exception:
                log.exception("onboarding: tombstone of %s not cleared", site_id)
        try:   # a NEW site's auto-scan starts OFF (an explicit record keeps INGEST_SITES, the .env default, from opening it); an edit leaves the setting alone
            if enable or not edit_site:
                settings.update({"sites": {site_id: {"enabled": bool(enable)}}})
        except Exception as exc:
            log.exception("onboarding: settings for %s not saved", site_id)
            warnings.append(f"settings: {type(exc).__name__}")
        try:   # the new yaml's image_hosts must reach the artwork proxy at once
            from .. import images
            images._site_hosts_cache = (None, 0.0, [])
        except Exception:
            pass
        report["notes"] = (draft.get("report") or {}).get("notes", "")   # the agent's notes survive the re-test
        if edit_info is not None:
            report["edit"] = {**edit_info, "to_version": version}
        draft = store.update_draft(draft_id, status="saved", saved_site_id=site_id, error=None, question=None,
                                   report=report)
        if not _write_handoff(draft, site_id, data, report, version, edit_site, force):
            warnings.append("handoff: devir notu yazılamadı")
        extra = (" + provider " + ", ".join(f"{w['name']} v{w['version']}" + (" (host eklendi)" if w.get("mode") == "update" else "") for w in written)) if written else ""
        add_event(draft_id, {"t": _now(), "kind": "status", "status": "saved", "text": f"{site_id} v{version} kaydedildi{extra}"})
        _record(draft, "saved", time.monotonic(), 0, f"{site_id} v{version}{extra}" + (" (force)" if force and not report.get("passed") else ""))
    scan_started = _start_scan(site_id) if scan_now else False
    if scan_now:
        add_event(draft_id, {"t": _now(), "kind": "status", "status": "saved",
                             "text": "tarama başladı" if scan_started else "tarama başlatılamadı (zaten çalışıyor)"})
    return {"site_id": site_id, "version": version, "enabled": bool(enable), "passed": bool(report.get("passed")),
            "baseline": limits, "warnings": warnings, "recipes": written, "scan_started": scan_started,
            "mode": "edit" if edit_site else "new"}
