"""Is the LLM account usable? ``pi auth check`` (10 s timeout, 60 s cache). Raw CLI output is never returned."""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from typing import Any, Optional

from ... import config

PI_DEFAULT_NAME = "pi-default"   # display name when LLM_MODEL is empty and pi's settings are unreadable
CACHE_SECONDS = 60
TIMEOUT_SECONDS = 10

_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "value": None}
_PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def configured_model() -> str:
    """``LLM_MODEL`` as set; empty = pi picks its own default model (and thinking level) - no ``--model`` is passed."""
    return (config.LLM_MODEL or "").strip()


def _pi_settings_path() -> str:
    base = os.environ.get("PI_CODING_AGENT_DIR") or os.path.join(os.path.expanduser("~"), ".pi", "agent")
    return os.path.join(base, "settings.json")


def pi_default() -> tuple[Optional[str], Optional[str]]:
    """(defaultProvider, defaultModel) from pi's settings.json; only these two keys are read (never auth files)."""
    try:
        with open(_pi_settings_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    prov, mod = data.get("defaultProvider"), data.get("defaultModel")
    prov = prov.strip() if isinstance(prov, str) and _PROVIDER_RE.match(prov.strip()) else None
    mod = mod.strip() if isinstance(mod, str) and mod.strip() else None
    return prov, mod


def model() -> str:
    """Display/log name of the model in use: ``LLM_MODEL``, else pi's default from its settings, else ``pi-default``."""
    name = configured_model()
    if name:
        return name
    prov, mod = pi_default()
    if mod:
        return f"{prov}/{mod}" if prov and "/" not in mod else mod
    return PI_DEFAULT_NAME


def provider() -> Optional[str]:
    name = configured_model()
    return provider_prefix(name) if name else pi_default()[0]


def provider_prefix(name: str) -> Optional[str]:
    """``openai-codex/gpt-5.6-terra`` -> ``openai-codex`` (None if unusable)."""
    prefix = name.split("/", 1)[0].strip() if "/" in name else ""
    return prefix if _PROVIDER_RE.match(prefix) else None


def _verdict(stdout: str, returncode: int) -> Optional[str]:
    """None = valid, else a short error code. Tolerant about the JSON shape."""
    text = (stdout or "").strip()
    data: Any = None
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0] or [-1])
    if start >= 0:
        try:
            data = json.JSONDecoder().raw_decode(text[start:])[0]
        except ValueError:
            data = None
    entries = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for key in ("valid", "ok", "authenticated", "logged_in", "loggedIn"):
            if isinstance(entry.get(key), bool):
                return None if entry[key] else "expired"
        if isinstance(entry.get("expired"), bool):
            return "expired" if entry["expired"] else None
        status = str(entry.get("status") or entry.get("state") or "").lower()
        if status:
            if "expire" in status or status in ("invalid", "missing", "unauthenticated", "logged_out"):
                return "expired"
            if status in ("valid", "ok", "active", "authenticated", "logged_in", "ready"):
                return None
    if returncode == 0 and data is not None:
        return None
    return f"exit_code_{returncode}" if returncode else "unrecognized_output"


def _run() -> dict[str, Any]:
    name = model()
    prov = provider()
    out = {"ok": False, "provider": prov, "model": name, "error": None,
           "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    pi_bin = (config.PI_BIN or "").strip()
    if not pi_bin or not shutil.which(pi_bin):
        out["error"] = f"pi kurulu değil (PI_BIN={pi_bin or '-'})"
        return out
    cmd = [pi_bin, "auth", "check", "--json", "--no-refresh"]
    if prov:   # unknown provider: let pi check its own default instead of failing here
        cmd[3:3] = ["--provider", prov]
    from .client import child_env   # lazy: client imports this module
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=child_env(), start_new_session=True)
    except OSError as exc:
        out["error"] = f"pi çalıştırılamadı ({type(exc).__name__})"
        return out
    try:
        stdout, _ = proc.communicate(timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)   # the whole group: pi may have spawned helpers
        except OSError:
            pass
        proc.communicate()
        out["error"] = "timeout"
        return out
    err = _verdict(stdout, proc.returncode)
    out["ok"], out["error"] = err is None, err
    return out


def check(force: bool = False) -> dict[str, Any]:
    """``{ok, provider, model, error, checked_at, cached}``; never raises."""
    with _lock:
        now = time.time()
        if not force and _cache["value"] is not None and now - _cache["at"] < CACHE_SECONDS:
            return {**_cache["value"], "cached": True}
        try:
            value = _run()
        except Exception as exc:  # pragma: no cover - defensive
            value = {"ok": False, "provider": None, "model": model(), "error": type(exc).__name__}
        _cache["at"], _cache["value"] = time.time(), value
        return {**value, "cached": False}


def reset_cache() -> None:
    with _lock:
        _cache["at"], _cache["value"] = 0.0, None
