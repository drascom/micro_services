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
from urllib.parse import urlsplit

from .fetch import FetchError
from .collections import MAX_POST_BYTES, form_body

_lock = threading.Lock()


def _host_key(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _post_job(cfg, url: str, data) -> dict:
    """The POST part of a worker job (``method`` / ``form`` / ``referer`` / ``origin``). Declarative list fetches only: HTTP mode, the
    site's OWN host (``cfg.base_url``; no side-effecting submit to anywhere else), a small urlencoded body. Never falls back to GET."""
    if cfg.fetch_mode != "http":
        raise FetchError("POST needs fetch_mode: http (the browser engine cannot send a POST body)")
    base = str(cfg.data.get("base_url") or "")
    if not base or not _host_key(base) or _host_key(url) != _host_key(base):
        raise FetchError(f"POST only to the site's own host ({_host_key(base) or 'base_url missing'}), not {_host_key(url) or url!r}")
    if urlsplit(url).scheme not in ("http", "https"):
        raise FetchError(f"POST needs an http(s) URL, not {url!r}")
    form = form_body(data)
    if len(form.encode("utf-8")) > MAX_POST_BYTES:
        raise FetchError(f"POST body is over {MAX_POST_BYTES} bytes")
    origin = f"{urlsplit(base).scheme}://{urlsplit(base).netloc}"
    return {"method": "POST", "form": form, "referer": origin + "/", "origin": origin}


def _worker_result(cfg, url: str, *, wait_for: str = "", capture: str = "page", method: str = "GET", data=None) -> dict:
    mode = cfg.fetch_mode
    post = (method or "GET").upper() == "POST"
    if post and capture != "page":
        raise FetchError("POST is for page fetches only")
    post_job = _post_job(cfg, url, data) if post else {}
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
        **post_job,
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
        if post and result.get("method") != "POST":   # an older installed worker would have sent a GET: never accept that silently
            raise FetchError(f"{engine} runtime cannot POST (it still runs the old worker): reinstall it with tools/install_crawler.sh")
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


def fetch_page_bundle(cfg, url: str, *, wait_for: str = "", method: str = "GET", data=None) -> dict:
    return _worker_result(cfg, url, wait_for=wait_for, method=method, data=data)


def fetch_page(cfg, url: str, *, wait_for: str = "", method: str = "GET", data=None) -> str:
    return fetch_page_bundle(cfg, url, wait_for=wait_for, method=method, data=data)["html"]


def fetch_cookies(cfg, url: str) -> list[dict]:
    """Create a short-lived browser session and return its cookie jar."""
    if cfg.fetch_mode != "browser":
        return []
    return _worker_result(cfg, url, capture="cookies")["cookies"]
