"""Is the LLM account behind self-heal still valid?  (`pi auth check`, status only.)

Runs ``pi auth check --provider <prefix> --json --no-refresh`` (10 s timeout) where
``<prefix>`` is the provider part of the heal model (``openai-codex/gpt-5.6`` ->
``openai-codex``). ``--credentials`` is never passed and the raw CLI output is never
returned: only a status, the provider/model names and expiry/refresh times if the
CLI reports them. The result is cached for ``CACHE_SECONDS``; ``check(force=True)``
bypasses the cache (the "Test et" button).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("diziflix.llm_health")

CACHE_SECONDS = 60
TIMEOUT_SECONDS = 10

VALID, EXPIRED, ERROR, PI_MISSING = "valid", "expired", "error", "pi_missing"

_MESSAGES = {
    VALID: "Hesap geçerli",
    EXPIRED: "Oturum süresi dolmuş veya geçersiz",
    ERROR: "Kontrol yapılamadı",
    PI_MISSING: "pi kurulu değil",
}

_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "value": None}

_PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def heal_model() -> str:
    from .scraper import heal as sheal
    return _env("SCRAPER_HEAL_MODEL", sheal.PI_DEFAULT_MODEL)


def provider_prefix(model: str) -> Optional[str]:
    """``openai-codex/gpt-5.6-terra`` -> ``openai-codex`` (None if unusable)."""
    prefix = model.split("/", 1)[0].strip() if "/" in model else ""
    return prefix if _PROVIDER_RE.match(prefix) else None


# --- parsing (pure) ---------------------------------------------------------

def _to_iso(value: Any) -> Optional[str]:
    """Epoch (s or ms) or ISO-ish string -> ``YYYY-mm-ddTHH:MM:SSZ``; None if unparseable."""
    try:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 1e11:  # milliseconds
                ts /= 1000.0
            if ts <= 0:
                return None
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
        if isinstance(value, str) and value.strip():
            text = value.strip()
            if re.fullmatch(r"\d+(\.\d+)?", text):
                return _to_iso(float(text))
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return None
    return None


def _first(d: dict, keys: tuple[str, ...]) -> Any:
    lowered = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        if k.lower() in lowered and lowered[k.lower()] is not None:
            return lowered[k.lower()]
    return None


def _pick(obj: Any, provider: str, depth: int = 0) -> Optional[dict]:
    """Find the dict that describes ``provider`` inside whatever shape pi printed."""
    if depth > 3:
        return None
    if isinstance(obj, list):
        dicts = [x for x in obj if isinstance(x, dict)]
        for x in dicts:
            if provider in (x.get("provider"), x.get("id"), x.get("name")):
                return x
        return dicts[0] if len(dicts) == 1 else None
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get(provider), dict):
        return obj[provider]
    for key in ("providers", "results", "checks", "accounts"):
        if isinstance(obj.get(key), (dict, list)):
            found = _pick(obj[key], provider, depth + 1)
            if found is not None:
                return found
    return obj


_OK_WORDS = {"valid", "ok", "active", "authenticated", "logged_in", "loggedin", "logged-in", "ready"}


def parse_output(stdout: str, returncode: int, provider: str) -> dict[str, Any]:
    """Reduce ``pi auth check --json`` output to ``{status, expires_at, refreshed_at}``.

    Deliberately tolerant about the JSON shape; never copies raw text into the result.
    """
    data: Any = None
    text = (stdout or "").strip()
    if text:
        try:
            data = json.loads(text)
        except ValueError:
            start = min([i for i in (text.find("{"), text.find("[")) if i >= 0] or [-1])
            if start >= 0:
                try:
                    data = json.JSONDecoder().raw_decode(text[start:])[0]
                except ValueError:
                    data = None
    entry = _pick(data, provider) if data is not None else None
    expires_at = refreshed_at = None
    verdict: Optional[str] = None
    if isinstance(entry, dict):
        expires_at = _to_iso(_first(entry, ("expires_at", "expiresAt", "expires", "expiry", "expiration", "expiry_at")))
        refreshed_at = _to_iso(_first(entry, ("refreshed_at", "refreshedAt", "last_refresh", "lastRefresh",
                                              "last_refreshed", "updated_at", "updatedAt", "issued_at", "issuedAt")))
        for key in ("valid", "ok", "authenticated", "logged_in", "loggedIn", "isValid"):
            val = _first(entry, (key,))
            if isinstance(val, bool):
                verdict = VALID if val else EXPIRED
                break
        if verdict is None:
            expired = _first(entry, ("expired", "isExpired"))
            if isinstance(expired, bool):
                verdict = EXPIRED if expired else VALID
        if verdict is None:
            word = _first(entry, ("status", "state", "result"))
            if isinstance(word, str):
                w = word.strip().lower()
                if "expire" in w:
                    verdict = EXPIRED
                elif w in _OK_WORDS:
                    verdict = VALID
                elif w in ("missing", "none", "not_logged_in", "unauthenticated", "invalid", "logged_out"):
                    verdict = EXPIRED
                else:
                    verdict = ERROR
        if verdict is None and expires_at:
            verdict = EXPIRED if expires_at <= time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) else VALID
    if verdict is None:
        verdict = VALID if returncode == 0 and data is not None else ERROR
    return {"status": verdict, "expires_at": expires_at, "refreshed_at": refreshed_at}


# --- check ------------------------------------------------------------------

def _result(status: str, *, provider: Optional[str], model: str, detail: Optional[str] = None,
            expires_at: Optional[str] = None, refreshed_at: Optional[str] = None) -> dict[str, Any]:
    heal_provider = _env("SCRAPER_HEAL_PROVIDER", "codex_cli")
    return {
        "status": status,
        "message": _MESSAGES[status],
        "detail": detail,
        "provider": provider,
        "model": model,
        "heal_provider": heal_provider,
        "provider_is_pi": heal_provider == "pi",
        "expires_at": expires_at,
        "refreshed_at": refreshed_at,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _run_check() -> dict[str, Any]:
    model = heal_model()
    provider = provider_prefix(model)
    pi_bin = _env("SCRAPER_HEAL_PI_BIN", "pi")
    if not shutil.which(pi_bin):
        return _result(PI_MISSING, provider=provider, model=model)
    if provider is None:
        return _result(ERROR, provider=None, model=model, detail="model_has_no_provider_prefix")
    cmd = [pi_bin, "auth", "check", "--provider", provider, "--json", "--no-refresh"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return _result(ERROR, provider=provider, model=model, detail="timeout")
    except FileNotFoundError:
        return _result(PI_MISSING, provider=provider, model=model)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("pi auth check failed to run: %s", type(exc).__name__)
        return _result(ERROR, provider=provider, model=model, detail=type(exc).__name__)
    parsed = parse_output(proc.stdout, proc.returncode, provider)
    detail = None
    if parsed["status"] == ERROR:
        detail = f"exit_code_{proc.returncode}" if proc.returncode else "unrecognized_output"
    return _result(parsed["status"], provider=provider, model=model, detail=detail,
                   expires_at=parsed["expires_at"], refreshed_at=parsed["refreshed_at"])


def check(force: bool = False) -> dict[str, Any]:
    """Cached (60 s) account status; ``force`` re-runs the CLI and refreshes the cache."""
    with _lock:
        now = time.time()
        if not force and _cache["value"] is not None and now - _cache["at"] < CACHE_SECONDS:
            return {**_cache["value"], "cached": True, "cache_age": int(now - _cache["at"])}
        value = _run_check()
        _cache["at"], _cache["value"] = time.time(), value
        return {**value, "cached": False, "cache_age": 0}


def reset_cache() -> None:
    with _lock:
        _cache["at"], _cache["value"] = 0.0, None
