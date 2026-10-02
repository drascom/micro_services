"""Synchronous bridge to isolated, bounded scraper worker processes."""
from __future__ import annotations

import base64
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

from .fetch import FetchError

_lock = threading.Lock()


def _worker_result(cfg, url: str, *, wait_for: str = "", capture: str = "page") -> dict:
    mode = cfg.fetch_mode
    engine = "obscura" if mode == "browser" else "crawlee-http"
    worker_name = "obscura_worker.py" if mode == "browser" else "crawlee_worker.py"
    worker_env = "SCRAPER_OBSCURA_WORKER" if mode == "browser" else "SCRAPER_CRAWLEE_WORKER"
    runtime = Path("/opt/diziflix-crawler")
    installed = (runtime / "venv/bin/python").is_file()
    command = [
        os.environ.get("SCRAPER_WORKER_PYTHON")
        or os.environ.get("SCRAPER_CRAWLEE_PYTHON")
        or (str(runtime / "venv/bin/python") if installed else sys.executable),
        os.environ.get(worker_env)
        or (str(runtime / worker_name) if installed else str(Path(__file__).with_name(worker_name))),
    ]
    user = os.environ.get("SCRAPER_WORKER_USER") or os.environ.get("SCRAPER_CRAWLEE_USER")
    if not user and installed and os.geteuid() == 0:
        user = "diziflix-crawler"
    if user:
        command = ["runuser", "-u", user, "--", *command]

    # Do not pass API/admin/TMDB/LLM credentials to scraper processes.
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
    if mode == "browser":
        env["OBSCURA_BINARY"] = os.environ.get(
            "SCRAPER_OBSCURA_BINARY", "/opt/diziflix-obscura/obscura")
    job = {
        "url": url,
        "mode": mode,
        "capture": capture,
        "wait_for": wait_for,
        "cache_images": mode == "browser" and bool(wait_for) and bool(cfg.data.get("cache_images")),
        "stealth": bool(cfg.data.get("obscura_stealth", True)),
        "obey_robots": True,
        "settle_seconds": int(cfg.data.get("obscura_settle_seconds", 1)),
    }
    lock_path = (
        os.environ.get("SCRAPER_WORKER_LOCK")
        or os.environ.get("SCRAPER_CRAWLEE_LOCK")
        or str(Path(tempfile.gettempdir()) / "diziflix-scraper.lock")
    )
    # Cross-thread + cross-process limit: one scraper on the 1 GB host.
    with _lock, open(lock_path, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        time.sleep(2)
        proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=env,
            cwd=tempfile.gettempdir(), start_new_session=True)
        try:
            stdout, stderr = proc.communicate(json.dumps(job), timeout=95)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise FetchError(f"{engine} timeout for {url}")
        if proc.returncode:
            raise FetchError(f"{engine} process failed ({proc.returncode}): {stderr[-500:]}")
        try:
            result = json.loads(stdout)
        except ValueError as exc:
            raise FetchError(f"invalid {engine} worker response") from exc
        if result.get("error"):
            raise FetchError(
                f"{engine} {mode}: {result.get('error')} for {url}")
        if capture == "cookies":
            if mode != "browser" or not isinstance(result.get("cookies"), list):
                raise FetchError(f"invalid {engine} cookie response for {url}")
            return result
        if not result.get("html"):
            raise FetchError(f"{engine} {mode}: empty response for {url}")
        if result.get("assets"):
            from ..images import cache_remote_original
            for asset_url, encoded in result["assets"].items():
                cache_remote_original(asset_url, base64.b64decode(encoded))
        return result


def fetch_page_bundle(cfg, url: str, *, wait_for: str = "") -> dict:
    return _worker_result(cfg, url, wait_for=wait_for)


def fetch_page(cfg, url: str, *, wait_for: str = "") -> str:
    return fetch_page_bundle(cfg, url, wait_for=wait_for)["html"]


def fetch_cookies(cfg, url: str) -> list[dict]:
    """Create a short-lived browser session and return its cookie jar."""
    if cfg.fetch_mode != "browser":
        return []
    return _worker_result(cfg, url, capture="cookies")["cookies"]
