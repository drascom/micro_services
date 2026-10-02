"""The pi agent runner shared by site onboarding (``scraper/onboard.py``) and the repair agent of the heal
(``scraper/heal_agent.py``).

One place knows how a ``pi`` process is started (``build_command``), what environment it gets (``child_env``: the server
environment minus everything that looks like a secret, plus the ``DIZIFLIX_*`` sandbox variables), how its JSON event
stream is read (``EventParser``, the ``_ev_*`` accessors) and how a run is supervised (``run``: stdin message, time
limit, cancel hook, stderr tail, cleanup). What a run is FOR is the caller's business: a ``Job`` carries the id (also the
pi session id and the sandbox token's owner), the first message, the ``mode`` (``onboard`` | ``repair``), the tool
allow-list and extra environment variables.

Both command line and event field names were checked against a real pi 0.99.1 in the Faz 2b spike
(``docs/plans/site-onboarding/pi-spike-notes.md``); what the spike could not measure stays marked ``UNVERIFIED``.
Nothing here parses a site or touches the library.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .. import config, llm_health
from . import onboard_store as store

log = logging.getLogger("scraper.pi_agent")

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILL_DIR = os.path.join(SERVER_DIR, "pi", "skills", "diziflix-site-onboarding")
EXTENSION_PATH = os.path.join(SERVER_DIR, "pi", "extensions", "diziflix-onboard.ts")
SKILL_NAME = "diziflix-site-onboarding"
READ_TOOL = "read"   # pi's own file reader, allowed for the skill references only (extension guard, DIZIFLIX_SKILL_DIR)

#: the tools of an onboarding run: ``discover_site`` (a draft yaml built by code from the site's pages: onboarding only), the page / test tools,
#: ``match_providers`` (the player page compared with the provider library), ``test_search`` (Faz 6, the yaml ``search:`` block; not a repair
#: tool: a repair never touches ``search:``), ``ask_user`` and ``submit_draft``. ``ask_user`` is NOT a sandbox call: the extension answers it
#: itself and the run ends ``needs_input`` (the question is read from the tool-call event, ``EventParser.ask``); a repair (the heal, no human)
#: never asks.
ONBOARD_TOOLS = ("discover_site", "fetch_page", "query_html", "grep_page", "outline_page", "test_config", "list_resolvers", "test_resolvers",
                 "test_provider", "match_providers", "test_search", "ask_user", "submit_draft")
#: repair mode: the onboarding tools that look at pages and test yaml / recipes, plus ``load_site_config`` and ``submit_repair``
#: (no ``submit_draft``: a repair never creates a site; no ``discover_site``: the site exists)
REPAIR_TOOLS = ("fetch_page", "query_html", "grep_page", "outline_page", "test_config", "test_resolvers", "test_provider", "match_providers",
                "list_resolvers", "load_site_config", "submit_repair")
#: edit mode (a registered site is changed on an admin's request, ``onboard.start(mode="edit")``): the onboarding tools minus ``discover_site``
#: + the read-only ``load_site_config`` (the extension locks ``submit_draft`` to the edited site; the sandbox only lets ``load_site_config``
#: read that site)
EDIT_TOOLS = (*(t for t in ONBOARD_TOOLS if t != "discover_site"), "load_site_config")
MODES = {"onboard": ONBOARD_TOOLS, "repair": REPAIR_TOOLS, "edit": EDIT_TOOLS}

SAY_CLIP = 1000
SUMMARY_CLIP = 300
ARGS_CLIP = 200
STDERR_CLIP = 1000
KILL_GRACE = 5.0


@dataclass
class Job:
    """One pi run: ``job_id`` (pi session id, sandbox token owner, ``DIZIFLIX_DRAFT_ID``), the ``first_message`` (stdin),
    the ``mode`` (``DIZIFLIX_MODE``: which tools the extension registers), the ``tools`` allow-list and extra env."""
    job_id: str
    first_message: str
    mode: str = "onboard"
    tools: tuple = ONBOARD_TOOLS
    env_extra: dict = field(default_factory=dict)


# --- small helpers ----------------------------------------------------------------------------------------------

def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def clip(value: Any, limit: int) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def model() -> str:
    """Model of the onboarding agent: ``ONBOARD_MODEL``, else the heal model (``SCRAPER_HEAL_MODEL`` / pi default)."""
    return config.ONBOARD_MODEL or llm_health.heal_model()


def sandbox_url() -> str:
    return f"http://127.0.0.1:{config.PORT}/api/onboard/sandbox"


def sessions_dir() -> str:
    return os.path.join(store.root(), "sessions")


def work_dir() -> str:
    """cwd of EVERY pi run. pi scopes a session to its cwd ("project = cwd"), so a follow-up (``message``) only finds the
    session of the first run when the cwd is the same: keep this one fixed (verified only for an unchanged cwd)."""
    return os.path.join(store.root(), "work")


# --- secrets ----------------------------------------------------------------------------------------------------

_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH|COOKIE|BEARER|SESSION)", re.I)
_SECRET_TEXT = [
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}(?:\.[A-Za-z0-9_-]*)?"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|passwd)\b(\s*[=:]\s*)[^\s,;\"']+"),
]


def is_secret_name(name: str) -> bool:
    return name.startswith("TMDB_") or bool(_SECRET_NAME.search(name))


def child_env(token: str, job_id: str, skill_dir: Optional[str] = None, extra: Optional[dict] = None) -> dict[str, str]:
    """A COPY of the server environment without anything that looks like a secret (TMDB keys, API keys, tokens, ...);
    pi authenticates with its own ``auth.json``. The ``DIZIFLIX_*`` variables are added last: the sandbox (url, token,
    job id) and ``DIZIFLIX_SKILL_DIR``, the only directory the extension lets pi's ``read`` tool open; ``extra`` (the
    job's ``env_extra``, e.g. ``DIZIFLIX_MODE``) comes after them."""
    env = {k: v for k, v in os.environ.items() if not is_secret_name(k)}
    env["DIZIFLIX_SANDBOX_URL"] = sandbox_url()
    env["DIZIFLIX_ONBOARD_TOKEN"] = token
    env["DIZIFLIX_DRAFT_ID"] = job_id
    env["DIZIFLIX_SKILL_DIR"] = skill_dir or SKILL_DIR
    env.update({str(k): str(v) for k, v in (extra or {}).items()})
    return env


def scrub(text: Any, limit: int = STDERR_CLIP, secrets: tuple = ()) -> str:
    """``text`` squeezed to one line, secret-looking parts masked (known values first), cut to ``limit``."""
    out = str(text or "")
    values = [v for v in (*secrets, *(v for k, v in os.environ.items() if is_secret_name(k))) if v and len(v) >= 8]
    for value in sorted(set(values), key=len, reverse=True):
        out = out.replace(value, "***")
    for pattern in _SECRET_TEXT:
        out = pattern.sub(lambda m: (m.group(1) + m.group(2) + "***") if m.lastindex and m.lastindex >= 2 else "***", out)
    return clip(out, limit)


# --- pi command line (the only place that knows it) -------------------------------------------------------------

def build_command(job_id: str, *, tools: tuple = ONBOARD_TOOLS, pi_bin: Optional[str] = None, model_name: Optional[str] = None,
                  extension: Optional[str] = None, skill: Optional[str] = None,
                  session_dir: Optional[str] = None) -> list[str]:
    """The pi argv of one run; the first message goes to stdin (like ``heal._call_pi``).

    Every flag below was run against pi 0.99.1 in the Faz 2b spike (``# verified``), except where marked UNVERIFIED.
    ``read`` is in the allow-list because without it pi does not even list a skill in the system prompt and the
    references of the skill cannot be opened; the extension's ``tool_call`` guard limits ``read`` to ``DIZIFLIX_SKILL_DIR``.
    """
    return [
        pi_bin or config.ONBOARD_PI_BIN, "-p",
        "--mode", "json",                                   # verified (Faz 2b, pi 0.99.1): strict JSONL on stdout
        "--offline",                                        # verified (Faz 2b, pi 0.99.1): no model-catalog refresh (as heal.py)
        "--no-builtin-tools",                               # verified (Faz 2b, pi 0.99.1): no bash/write/edit of pi itself
        "--tools", ",".join((*tools, READ_TOOL)),           # verified (Faz 2b, pi 0.99.1): comma list = allow-list (sandbox tools + read)
        "--no-extensions", "-e", extension or EXTENSION_PATH,   # verified (Faz 2b, pi 0.99.1): only our extension
        "--no-skills", "--skill", skill or SKILL_DIR,       # verified (Faz 2b, pi 0.99.1): only our skill (/skill:name expands)
        # UNVERIFIED (Faz 2b): the flag is accepted (cli.md) but its effect was not measured (the spike cwd had no
        # AGENTS.md / CLAUDE.md); our cwd sits under the repo whose CLAUDE.md pi would otherwise walk up to
        "--no-context-files",
        "--no-prompt-templates",                            # verified (Faz 2b, pi 0.99.1): accepted
        "--model", model_name or model(),                   # verified (Faz 2b, pi 0.99.1): provider/id; an unknown id is a provider error with exit 0
        "--session-dir", session_dir or sessions_dir(),     # verified (Faz 2b, pi 0.99.1): files <ISO>_<session-id>.jsonl straight in it
        "--session-id", job_id,                             # verified (Faz 2b, pi 0.99.1): same id + same cwd (work_dir) = same session
    ]


# --- JSON event stream (the only place that knows the field names) ----------------------------------------------
# verified (Faz 2b, pi 0.99.1): ``type`` is the discriminator (no ``event`` spelling); tool_execution_start/_end carry
# ``toolCallId`` / ``toolName`` / ``args`` and ``result.content[].text`` + top-level ``isError``; message_update carries
# ``assistantMessageEvent.{type: text_delta, delta}``; message_end/turn_end carry ``message`` (role, content blocks
# ``thinking`` / ``toolCall`` / ``text``, ``stopReason``, ``errorMessage``). The first line of the stream is the
# ``{"type":"session"}`` header, ``agent_settled`` the last: both fall into "unknown type, ignored", like everything
# the parser does not know. The extra spellings some accessors still accept were never seen and are harmless.
# UNVERIFIED (Faz 2b): ``auto_retry_start`` / ``auto_retry_end`` (documented in json.md, never observed live).
#
# A provider/model failure is NOT an ``error`` event (there is none): pi still exits 0, stderr stays empty, and the
# assistant message ends with ``stopReason: "error"`` + ``errorMessage`` (content ``[]``): see ``_ev_assistant_error``.

def _ev_type(ev: dict) -> str:
    return str(ev.get("type") or "")


def _ev_tool_name(ev: dict) -> str:
    tool = ev.get("tool")
    return str(ev.get("toolName") or ev.get("tool_name") or ev.get("name")
               or (tool.get("name") if isinstance(tool, dict) else tool) or "")


def _ev_tool_id(ev: dict) -> str:
    return str(ev.get("toolCallId") or ev.get("tool_call_id") or ev.get("id") or "")


def _ev_tool_args(ev: dict) -> Any:
    for key in ("args", "arguments", "input", "params"):
        if ev.get(key) is not None:
            return ev[key]
    return None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(b.get("text") or "") if isinstance(b, dict) else str(b) for b in content
                       if not isinstance(b, dict) or b.get("type") in (None, "text"))
    return ""


def _ev_result_text(ev: dict) -> str:
    for key in ("result", "output", "content"):
        value = ev.get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            text = _content_text(value.get("content")) or str(value.get("text") or "")
            return text or json.dumps(value, ensure_ascii=False, default=str)
        if isinstance(value, (list, str)):
            return _content_text(value)
        return str(value)
    return ""


def _ev_is_error(ev: dict) -> bool:
    result = ev.get("result")
    return bool(ev.get("isError") or ev.get("is_error") or ev.get("error")
                or (isinstance(result, dict) and (result.get("isError") or result.get("is_error"))))


def _ev_text_delta(ev: dict) -> str:
    inner = ev.get("assistantMessageEvent") or ev.get("assistant_message_event")
    if isinstance(inner, dict):
        return str(inner.get("delta") or "") if inner.get("type") in ("text_delta", "text") else ""
    return ""


def _ev_message(ev: dict) -> tuple[str, str]:
    """``(role, text)`` of the message an event carries (``("", "")`` when it carries none)."""
    message = ev.get("message")
    if not isinstance(message, dict):
        return "", ""
    return str(message.get("role") or ""), _content_text(message.get("content"))


def _ev_assistant_error(message: Any) -> Optional[str]:
    """Error text of an assistant message that ended in a provider failure (``stopReason: "error"`` or an
    ``errorMessage``), ``None`` for every other message (other roles, ``stop`` / ``toolUse`` / ``pending`` / ...)."""
    if not isinstance(message, dict) or str(message.get("role") or "") not in ("", "assistant"):
        return None
    if message.get("stopReason") == "error" or message.get("errorMessage"):
        return str(message.get("errorMessage") or "") or "provider error"
    return None


def _short_args(args: Any) -> str:
    if not isinstance(args, dict):
        return clip(args, ARGS_CLIP) if args is not None else ""
    parts = []
    for key, value in args.items():
        if key == "draft_id":
            continue
        if key in ("yaml_text", "recipe_yaml") and isinstance(value, str):
            parts.append(f"{key}=<{len(value)} chars>")
        elif key == "provider_recipes" and isinstance(value, list):
            names = ", ".join(str(r.get("name")) for r in value if isinstance(r, dict) and r.get("name"))
            parts.append(f"provider_recipes=<{len(value)}: {clip(names, 60)}>")
        elif isinstance(value, str):
            parts.append(f"{key}={clip(value, 80)}")
        else:
            parts.append(f"{key}={clip(json.dumps(value, ensure_ascii=False, default=str), 40)}")
    return clip(", ".join(parts), ARGS_CLIP)


def _summarize(text: str, ok: bool) -> str:
    """One short line for a tool result: the sandbox answers are JSON, so the telling keys are picked."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return clip(text, SUMMARY_CLIP)
    if not isinstance(data, dict):
        return clip(text, SUMMARY_CLIP)
    parts = []
    for key in ("passed", "valid", "status", "page_id", "count", "candidate_count", "pages_checked", "pages_resolved", "title"):
        if key in data and not isinstance(data[key], (dict, list)):
            parts.append(f"{key}={data[key]}")
    for key in ("errors", "warnings", "resolved", "items", "repeating", "candidates"):
        if isinstance(data.get(key), list):
            parts.append(f"{key}={len(data[key])}")
    if isinstance(data.get("errors"), list) and data["errors"]:
        parts.append("first_error=" + clip(data["errors"][0], 100))
    if isinstance(data.get("error"), dict):
        parts.append("error=" + clip(data["error"].get("message"), 150))
    return clip(", ".join(parts), SUMMARY_CLIP) if parts else clip(text, SUMMARY_CLIP)


ASK_TOOL = "ask_user"      # the agent asks the admin (extension tool, no sandbox call)
SUBMIT_TOOL = "submit_draft"


def _args_dict(args: Any) -> dict:
    """The arguments of a tool call as a dict (pi sends an object; a JSON string is tolerated, anything else gives ``{}``)."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return {}
    return dict(args) if isinstance(args, dict) else {}


class EventParser:
    """Turns pi's JSON lines into agent events (``tool``, ``tool_result``, ``say``, ``ask``, ``error``, ``retry``). ``feed(line)``
    returns the events to append; ``finish()`` flushes the assistant text still buffered. ``label`` / ``phase`` follow the
    running tool, ``turns`` / ``test_runs`` count. ``provider_error`` is the (scrubbed, <= 300 chars) error of the LAST
    assistant message when it ended in a provider failure, else ``""``: a later good assistant message (an automatic retry
    that worked) clears it.

    ``ask`` = the arguments (dict) of the LAST ``ask_user`` call that did not fail (``None`` = the agent asked nothing),
    ``ask_seq`` / ``submit_seq`` = the tool-call counter at that call and at the last ``submit_draft`` call that went through: an
    ask that came AFTER the last submission is a question the admin has to answer (``asking``)."""

    def __init__(self) -> None:
        self.provider_error = ""
        self.ask: Optional[dict] = None
        self.ask_seq = 0
        self.submit_seq = 0
        self.call_seq: dict[str, int] = {}
        self.text = ""
        self.names: dict[str, str] = {}
        self.turn_ends = 0
        self.tool_calls = 0
        self.test_runs = 0
        self.last_say = ""
        self.label = ""
        self.phase = ""

    @property
    def turns(self) -> int:
        return self.turn_ends or self.tool_calls

    @property
    def asking(self) -> bool:
        """The agent's last word is a question for the admin: ``ask_user`` was called (and succeeded) after the last ``submit_draft``."""
        return self.ask is not None and self.ask_seq > self.submit_seq

    def _flush(self) -> list[dict]:
        text, self.text = self.text.strip(), ""
        if not text:
            return []
        self.last_say = text[:SAY_CLIP]
        return [{"t": now(), "kind": "say", "text": self.last_say}]

    def _note_error(self, message: Any, final: bool = True) -> list[dict]:
        """Track the provider error an assistant ``message`` carries (message_end / turn_end / agent_end repeat the same
        message: the ``error`` event is emitted once per distinct text). ``final`` = a message_end, which also clears the
        error when the message is a good one; turn_end / agent_end only ever raise it."""
        error = _ev_assistant_error(message)
        if error is None:
            if final and isinstance(message, dict) and message.get("role") == "assistant":
                self.provider_error = ""
            return []
        text = scrub(error, SUMMARY_CLIP)
        if text == self.provider_error:
            return []
        self.provider_error = text
        return [{"t": now(), "kind": "error", "text": text}]

    def feed(self, line: str) -> list[dict]:
        line = (line or "").strip()
        if not line.startswith("{"):
            return []
        try:
            ev = json.loads(line)
        except ValueError:
            return []
        if not isinstance(ev, dict):
            return []
        kind = _ev_type(ev)
        out: list[dict] = []
        if kind == "tool_execution_start":
            out += self._flush()
            name, args = _ev_tool_name(ev) or "?", _ev_tool_args(ev)
            self.names[_ev_tool_id(ev)] = name
            self.tool_calls += 1
            self.phase = name
            if name == "test_config":
                self.test_runs += 1
                self.label = f"test_config {self.test_runs}. tur"
            else:
                self.label = name
            out.append({"t": now(), "kind": "tool", "name": name, "args_short": _short_args(args)})
            self.call_seq[_ev_tool_id(ev)] = self.tool_calls
            if name == ASK_TOOL:
                given = _args_dict(args)
                self.ask, self.ask_seq = given, self.tool_calls
                question = clip(given.get("question"), 600)
                if question:
                    out.append({"t": now(), "kind": "ask", "field": clip(given.get("field"), 60),
                                "ask_kind": clip(given.get("kind"), 20), "text": question})
        elif kind == "tool_execution_end":
            name = _ev_tool_name(ev) or self.names.get(_ev_tool_id(ev)) or "?"
            ok = not _ev_is_error(ev)
            if name == ASK_TOOL and not ok:   # the extension refused the call (empty question ...): nothing was asked
                self.ask = None
            elif name == SUBMIT_TOOL and ok:   # a submission that went through (the draft is ``ready``)
                self.submit_seq = self.call_seq.get(_ev_tool_id(ev), self.tool_calls)
            out.append({"t": now(), "kind": "tool_result", "name": name, "ok": ok,
                        "summary": _summarize(_ev_result_text(ev), ok)})
        elif kind == "message_update":
            self.text += _ev_text_delta(ev)
        elif kind == "message_end":
            role, text = _ev_message(ev)
            if role in ("", "assistant"):
                if not self.text.strip() and text.strip():
                    self.text = text
                out += self._flush()
                out += self._note_error(ev.get("message"))
        elif kind in ("turn_end", "agent_end"):
            if kind == "turn_end":
                self.turn_ends += 1
            out += self._flush()
            if kind == "turn_end":
                out += self._note_error(ev.get("message"), final=False)
            else:   # agent_end.messages = the messages of this run; the last one is the assistant's final word
                messages = ev.get("messages")
                if isinstance(messages, list) and messages:
                    out += self._note_error(messages[-1], final=False)
        elif kind == "auto_retry_start":
            out.append({"t": now(), "kind": "retry", "text": clip(
                f"deneme {ev.get('attempt', '?')}/{ev.get('maxAttempts', '?')}: "
                f"{ev.get('errorMessage') or ev.get('error') or ''}", 300)})
        elif kind == "auto_retry_end":
            if ev.get("success") is False:
                out.append({"t": now(), "kind": "error", "text": clip(
                    "yeniden deneme başarısız: " + str(ev.get("finalError") or ev.get("error") or ""), 300)})
        return out

    def finish(self) -> list[dict]:
        return self._flush()


# --- running ----------------------------------------------------------------------------------------------------

def terminate(proc: Any, grace: float = KILL_GRACE) -> None:
    """SIGTERM, SIGKILL after ``grace`` seconds."""
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
    except Exception:
        pass


def drain(stream: Any, tail: deque) -> None:
    try:
        for line in stream:
            tail.append(line)
    except Exception:
        pass


@dataclass
class RunResult:
    """How one pi run ended. ``exit_code`` None = no exit code (the process never ran or was lost); ``crash`` = text of
    an exception of the runner itself (pi missing, ...); ``stderr`` = the tail of pi's stderr; ``parser`` = the event
    parser (``last_say``, ``provider_error``, ``turns``, ...)."""
    parser: EventParser
    exit_code: Optional[int] = None
    crash: str = ""
    timed_out: bool = False
    stderr: str = ""


def run(job: Job, *, token: str, timeout: float, on_event: Callable[[dict], None],
        on_label: Optional[Callable[[str, str], None]] = None, on_start: Optional[Callable[[Any], bool]] = None,
        model_name: Optional[str] = None, extension: Optional[str] = None, skill: Optional[str] = None,
        pi_bin: Optional[str] = None) -> RunResult:
    """Run pi once for ``job`` and wait for it. The first message goes to stdin; every event of the stream is handed to
    ``on_event``; ``on_label(label, phase)`` follows the running tool (``("pi başlatılıyor", "start")`` before the
    spawn); ``on_start(proc)`` runs right after the spawn and returns True when the run was cancelled meanwhile (pi is
    then terminated). After ``timeout`` seconds pi is terminated and ``timed_out`` is set. Never raises; every process
    resource is released when it returns."""
    parser = EventParser()
    stderr_tail: deque = deque(maxlen=40)
    timed_out = threading.Event()
    proc = None
    watchdog = None
    drain_thread = None
    exit_code: Optional[int] = None
    crash = ""
    try:
        os.makedirs(sessions_dir(), exist_ok=True)
        work = work_dir()   # the same cwd on every run: pi finds the session of a follow-up by it
        os.makedirs(work, exist_ok=True)
        if on_label:
            on_label("pi başlatılıyor", "start")
        cmd = build_command(job.job_id, tools=job.tools, pi_bin=pi_bin, model_name=model_name, extension=extension, skill=skill)
        env = child_env(token, job.job_id, skill, {"DIZIFLIX_MODE": job.mode, **job.env_extra})
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace", bufsize=1, env=env, cwd=work)
        if on_start is not None and on_start(proc):   # cancelled while pi was starting
            terminate(proc)
        drain_thread = threading.Thread(target=drain, args=(proc.stderr, stderr_tail), daemon=True)
        drain_thread.start()
        try:
            proc.stdin.write(job.first_message)
            proc.stdin.close()
        except Exception:   # pi may already be gone; its exit code tells the rest
            pass
        watchdog = threading.Timer(float(timeout), lambda: (timed_out.set(), terminate(proc)))
        watchdog.daemon = True
        watchdog.start()
        label = ""
        for line in proc.stdout:
            for event in parser.feed(line):
                on_event(event)
            if on_label and parser.label and parser.label != label:
                label = parser.label
                on_label(label, parser.phase)
        for event in parser.finish():
            on_event(event)
        exit_code = proc.wait()
        drain_thread.join(2)   # the stderr tail is complete before it is read
    except FileNotFoundError:
        crash = f"pi bulunamadı ({pi_bin or config.ONBOARD_PI_BIN})"
    except Exception as exc:
        log.exception("pi run %s crashed", job.job_id)
        crash = f"{type(exc).__name__}: {scrub(exc, 300)}"
    finally:
        if watchdog is not None:
            watchdog.cancel()
        if proc is not None and exit_code is None and proc.poll() is None:
            terminate(proc, 1.0)
        for stream in (getattr(proc, "stdout", None), getattr(proc, "stderr", None), getattr(proc, "stdin", None)):
            try:
                stream.close()
            except Exception:
                pass
    return RunResult(parser=parser, exit_code=exit_code, crash=crash, timed_out=timed_out.is_set(),
                     stderr="".join(stderr_tail))
