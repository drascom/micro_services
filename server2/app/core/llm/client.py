"""The ONE way server2 talks to an LLM: a short, tool-less, one-shot ``pi -p`` call (README K6).

``complete`` never raises. Prompt on stdin, answer on stdout; the child gets a copy of the environment without anything
that looks like a secret; on timeout the whole process group is killed. Every call (fake ones included) is logged to
``llm_calls`` (sizes only, never the text); the scrubbed text of the last ``LAST_KEEP`` calls is kept in
``DATA_DIR/llm/last/<id>.txt`` for debugging.

Short sessions: ``session="<site>:<stage>:<job attempt>"`` keeps a pi session (``--session-dir DATA_DIR/llm/sessions``) so a
stage's follow-up turn sends only the new facts. The caller ends it with ``end_session`` when the stage run is over;
files older than an hour are removed by ``gc_sessions`` (first call of the process, and ``jobs_gc``). If the session is
gone (restart, cleanup) the call is not made: ``Reply(error="session_lost", session_lost=True)`` and the caller retries
with the full prompt and no session (a session only saves tokens, correctness never depends on it).
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from ... import config, db
from . import fake, health

log = logging.getLogger("diziflix.llm")

LAST_KEEP = 20
SESSION_MAX_AGE_S = 3600
_SESSION_NS = uuid.UUID("6f1d3c52-6a0e-4b8e-9a51-2f4f0d2b7c11")
# Flags that keep skills, extensions, context files (AGENTS.md/CLAUDE.md) and every tool off, in both modes.
LOCKDOWN_FLAGS = ("--no-tools", "--no-skills", "--no-context-files", "--no-extensions", "--offline")

_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH|COOKIE|BEARER|SESSION)", re.I)
_SECRET_TEXT = [
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}(?:\.[A-Za-z0-9_-]*)?"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|passwd)\b(\s*[=:]\s*)[^\s,;\"']+"),
]

_gc_lock = threading.Lock()
_gc_done = False


@dataclass
class Reply:
    ok: bool
    text: str = ""
    error: Optional[str] = None
    ms: int = 0
    model: str = ""
    session_lost: bool = False
    call_id: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None


# --------------------------------------------------------------------------- secrets

def is_secret_name(name: str) -> bool:
    return name.startswith("TMDB_") or bool(_SECRET_NAME.search(name))


def child_env() -> dict[str, str]:
    """A copy of the server environment without secret-looking variables; pi authenticates with its own auth.json."""
    return {k: v for k, v in os.environ.items() if not is_secret_name(k)}


def scrub(text: Any) -> str:
    """Mask known secret values (from the environment) and secret-looking patterns; newlines are kept."""
    out = str(text or "")
    values = {v for k, v in os.environ.items() if is_secret_name(k) and v and len(v) >= 8}
    for value in sorted(values, key=len, reverse=True):
        out = out.replace(value, "***")
    for pattern in _SECRET_TEXT:
        out = pattern.sub(lambda m: (m.group(1) + m.group(2) + "***") if m.lastindex and m.lastindex >= 2 else "***", out)
    return out


# --------------------------------------------------------------------------- paths / sessions

def _llm_dir(*parts: str) -> str:
    path = os.path.join(config.DATA_DIR, "llm", *parts)
    os.makedirs(path, exist_ok=True)
    return path


def sessions_dir() -> str:
    return _llm_dir("sessions")


def session_id(session: str) -> str:
    """pi wants a UUID-like id; derived from the caller's key (``<site>:<stage>:<job attempt>``), so it is stable."""
    return str(uuid.uuid5(_SESSION_NS, session))


def _marker(sid: str) -> str:
    return os.path.join(sessions_dir(), f"{sid}.started")


def _session_files(sid: str) -> list[str]:
    found = glob.glob(os.path.join(sessions_dir(), "**", f"*{sid}*"), recursive=True)
    return [p for p in found if os.path.isfile(p) and not p.endswith(".started")]


def end_session(session: str) -> int:
    """Delete the session's files (pi's and our marker). Returns how many were removed."""
    sid = session_id(session)
    removed = 0
    for path in [*_session_files(sid), _marker(sid)]:
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return removed


def gc_sessions(max_age_s: int = SESSION_MAX_AGE_S) -> int:
    """Remove session files older than ``max_age_s`` (no session outlives a stage run)."""
    cutoff = time.time() - max_age_s
    removed = 0
    for base, _dirs, files in os.walk(sessions_dir()):
        for name in files:
            path = os.path.join(base, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
    return removed


def _gc_once() -> None:
    global _gc_done
    with _gc_lock:
        if _gc_done:
            return
        _gc_done = True
    try:
        gc_sessions()
    except Exception:  # pragma: no cover - defensive
        log.exception("llm session gc failed")


def _usage(sid: str) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Token usage of the LAST turn, read from pi's session file (only session mode records it)."""
    files = sorted(_session_files(sid), key=os.path.getmtime)
    if not files:
        return None, None, None
    try:
        with open(files[-1], "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None, None, None
    starts = [m.end() - 1 for m in re.finditer(r'"usage"\s*:\s*\{', text)]
    if not starts:
        return None, None, None
    try:
        usage = json.JSONDecoder().raw_decode(text[starts[-1]:])[0]
    except ValueError:
        return None, None, None

    def num(key: str) -> Optional[int]:
        val = usage.get(key)
        return int(val) if isinstance(val, (int, float)) and not isinstance(val, bool) else None

    return num("input"), num("output"), num("cacheRead")


# --------------------------------------------------------------------------- command / process

def build_command(model: str, *, session: Optional[str] = None, pi_bin: Optional[str] = None) -> list[str]:
    """Empty ``model`` = no ``--model``: pi uses its own default model and thinking level (pi settings)."""
    cmd = [pi_bin or config.PI_BIN, "-p"]
    if (model or "").strip():
        cmd += ["--model", model.strip()]
    if session:
        cmd += ["--session-dir", sessions_dir(), "--session-id", session_id(session)]
    else:
        cmd.append("--no-session")
    return cmd + list(LOCKDOWN_FLAGS)


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _run(cmd: list[str], prompt: str, timeout: int) -> tuple[str, Optional[str]]:
    """(stdout, error). Own process group: a timeout kills pi and anything it started (no orphans, no zombies)."""
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=child_env(), cwd=_llm_dir("work"), start_new_session=True)
    except FileNotFoundError:
        return "", "pi_missing"
    except OSError as exc:
        return "", f"spawn_error:{type(exc).__name__}"
    try:
        out, err = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            proc.communicate(timeout=5)
        except Exception:  # pragma: no cover - defensive
            proc.wait(timeout=5)
        return "", "timeout"
    except Exception as exc:  # pragma: no cover - defensive
        _kill_group(proc)
        proc.wait(timeout=5)
        return "", f"run_error:{type(exc).__name__}"
    text = (out or "").strip()
    if proc.returncode:
        summary = stderr_summary(err)
        return "", f"exit_code_{proc.returncode}" + (f": {summary}" if summary else "")
    if not text:
        return "", "empty_reply"
    return text, None


def stderr_summary(err: Any, limit: int = 160) -> str:
    """First non-empty stderr line, scrubbed and shortened (shown on the admin card; never a secret)."""
    for line in str(err or "").splitlines():
        line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line).strip()
        if line:
            line = scrub(line)
            return line if len(line) <= limit else line[:limit - 1] + "…"
    return ""


# --------------------------------------------------------------------------- log

def _record(reply: Reply, *, purpose: str, site_id: Optional[str], stage: Optional[str], session: Optional[str],
            prompt: str, background: bool) -> None:
    try:
        cur = db.execute(
            "INSERT INTO llm_calls(at, purpose, site_id, stage, session, model, prompt_chars, reply_chars, input_tokens,"
            " output_tokens, cached_tokens, ms, ok, error, background) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(time.time()), purpose, site_id, stage, session, reply.model, len(prompt), len(reply.text),
             reply.input_tokens, reply.output_tokens, reply.cached_tokens, reply.ms, int(reply.ok), reply.error,
             int(bool(background))),
        )
        reply.call_id = cur.lastrowid
    except Exception:
        log.exception("llm_calls insert failed")
        return
    try:
        last = _llm_dir("last")
        with open(os.path.join(last, f"{reply.call_id}.txt"), "w", encoding="utf-8") as fh:
            fh.write(f"# call {reply.call_id} purpose={purpose} site={site_id} stage={stage} ok={reply.ok} "
                     f"error={reply.error}\n## PROMPT\n{scrub(prompt)}\n## REPLY\n{scrub(reply.text)}\n")
        names = sorted((n for n in os.listdir(last) if n[:-4].isdigit() and n.endswith(".txt")), key=lambda n: int(n[:-4]))
        for name in names[:-LAST_KEEP]:
            os.remove(os.path.join(last, name))
    except OSError:
        log.warning("llm last-call text not written")


# --------------------------------------------------------------------------- public

def complete(prompt: str, *, purpose: str, site_id: Optional[str] = None, stage: Optional[str] = None,
             timeout: int = 120, max_prompt_chars: int = 60_000, session: Optional[str] = None,
             background: bool = False) -> Reply:
    """One short LLM call. ``background=True`` marks system-started calls (the ones ``budget.allowed`` limits per site/day;
    ``False`` = admin-started, logged but outside the budget);
    the caller checks the budget before calling. Never raises."""
    t0 = time.monotonic()
    prompt = str(prompt or "")
    reply = Reply(ok=False, model=health.model())
    try:
        _gc_once()
        sid = session_id(session) if session else None
        if len(prompt) > max_prompt_chars:
            reply.error = "prompt_too_large"
        elif sid and os.path.exists(_marker(sid)) and not _session_files(sid):
            reply.error, reply.session_lost = "session_lost", True
        elif os.environ.get("DIZIFLIX_FAKE_LLM") == "1" and config.test_sandbox_active():
            text = fake.next_reply(prompt)
            if text is None:
                reply.error = "fake_empty"
            else:
                reply.ok, reply.text = True, text
                if sid:  # behave like pi: a session file with the turn's usage
                    with open(os.path.join(sessions_dir(), f"fake_{sid}.jsonl"), "a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"usage": {"input": len(prompt) // 4, "output": len(text) // 4,
                                                       "cacheRead": 0}}) + "\n")
        elif not (config.PI_BIN or "").strip():
            reply.error = "pi_missing"
        else:
            text, error = _run(build_command(health.configured_model(), session=session), scrub(prompt), timeout)
            reply.ok, reply.text, reply.error = error is None, text, error
        if reply.ok and sid:
            with open(_marker(sid), "w", encoding="utf-8") as fh:
                fh.write(session or "")
            reply.input_tokens, reply.output_tokens, reply.cached_tokens = _usage(sid)
    except Exception as exc:  # pragma: no cover - defensive: complete never raises
        log.exception("llm complete failed")
        reply.ok, reply.error = False, f"internal:{type(exc).__name__}"
    reply.ms = int((time.monotonic() - t0) * 1000)
    _record(reply, purpose=purpose, site_id=site_id, stage=stage, session=session, prompt=prompt, background=background)
    return reply
