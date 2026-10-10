"""Rendered HTML through the Obscura browser binary (subprocess), based on the old ``obscura_worker.py``.

At most ``POOL`` renders at a time, ``TIMEOUT_S`` per render, ``wait_for`` = a CSS selector to wait for. No secrets
reach the child (clean env). No ``OBSCURA_BIN`` -> ``FetchError("browser_unavailable")``. The binary cannot POST
and does not report redirects (``final_url`` = the requested URL). Tests replace ``render``.

SECURITY: netguard only sees the first URL. Obscura's own redirects, JS/meta navigation and subresource loads are not
guarded (no verified proxy option to route them through a guard), so ``GUARDED = False``: ``core.fetch`` never
escalates ``auto`` to the browser; only an explicit ``mode="browser"`` (admin/site choice) uses it.
"""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import threading
from typing import Optional

from ... import config
from .base import FetchError, is_challenge

GUARDED = False   # set True only once every browser request passes netguard (e.g. a guarding forward proxy)
POOL = 2
TIMEOUT_S = 45
SETTLE_S = 1
_pool = threading.BoundedSemaphore(POOL)


def available() -> bool:
    binary = config.OBSCURA_BIN
    return bool(binary) and os.path.isfile(binary) and os.access(binary, os.X_OK)


def _command(binary: str, url: str, wait_for: str, storage: str, stealth: bool = True) -> list[str]:
    command = [binary]
    if stealth:
        command.append("--stealth")
    command.extend(["--obey-robots", "--storage-dir", storage, "fetch", url,
                    "--wait-until", "domcontentloaded", "--wait", str(SETTLE_S),
                    "--timeout", str(TIMEOUT_S), "--dump", "html", "--quiet"])
    if wait_for:
        command.extend(["--selector", str(wait_for)])
    return command


def render(url: str, *, wait_for: str = "", timeout: Optional[float] = None) -> tuple[str, int]:
    """``(html, status)`` of ``url`` rendered by Obscura. FetchError: browser_unavailable | timeout | challenge | network."""
    if not available():
        raise FetchError("browser_unavailable", "OBSCURA_BIN is not set or not executable", url=url)
    limit = float(timeout or TIMEOUT_S)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
    with _pool, tempfile.TemporaryDirectory(prefix="diziflix2-obscura-") as storage:
        proc = subprocess.Popen(_command(config.OBSCURA_BIN, url, wait_for, storage), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=env, cwd=storage, start_new_session=True)
        try:
            stdout, stderr = proc.communicate(timeout=limit + 5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.communicate()
            raise FetchError("timeout", f"browser after {limit:g}s", url=url)
    if proc.returncode:
        raise FetchError("network", f"obscura exit {proc.returncode}: {(stderr or '')[-300:]}", url=url)
    html = stdout or ""
    if is_challenge(html):
        raise FetchError("challenge", "anti-bot page (browser)", url=url)
    if not html.strip():
        raise FetchError("network", "browser: empty response", url=url)
    return html, 200
