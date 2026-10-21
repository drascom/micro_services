"""Standalone bridge to the Obscura browser binary.

The worker deliberately speaks the same JSON stdin/stdout contract as
``crawlee_worker.py``. Site extractors and provider resolvers therefore remain
independent of the transport. Obscura owns all rendered HTML and same-session
artwork retrieval; Crawlee is retained only for plain HTTP jobs.
"""
from __future__ import annotations

import base64
from html.parser import HTMLParser
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import urljoin, urlparse


class _ImageCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "img":
            return
        values = dict(attrs)
        value = values.get("data-src") or values.get("src")
        if value:
            self.values.append(value)


def _image_urls(html: str, page_url: str) -> list[str]:
    parser = _ImageCollector()
    parser.feed(html)
    hostname = urlparse(page_url).hostname
    output: list[str] = []
    for value in parser.values:
        url = urljoin(page_url, value)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or parsed.hostname != hostname or url in output:
            continue
        output.append(url)
        if len(output) >= 100:
            break
    return output


def _base_command(binary: Path, job: dict, storage: str) -> list[str]:
    command = [str(binary)]
    if job.get("stealth", True):
        command.append("--stealth")
    if job.get("obey_robots", True):
        command.append("--obey-robots")
    command.extend(["--storage-dir", storage])
    return command


def _looks_like_image(data: bytes) -> bool:
    return (
        data.startswith(b"\xff\xd8\xff")
        or data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith((b"GIF87a", b"GIF89a"))
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    )


def crawl(job: dict) -> dict:
    if job.get("mode") != "browser":
        raise ValueError("Obscura only supports browser-mode jobs")

    binary = Path(os.environ.get("OBSCURA_BINARY", "/opt/diziflix-obscura/obscura"))
    if not binary.is_file():
        raise FileNotFoundError(f"Obscura binary not found at {binary}")

    clean_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
    with tempfile.TemporaryDirectory(prefix="diziflix-obscura-") as storage:
        command = _base_command(binary, job, storage)
        if job.get("capture") == "cookies":
            command.extend([
                "fetch", job["url"],
                "--wait-until", "domcontentloaded",
                "--wait", str(min(10, max(0, int(job.get("settle_seconds", 1))))),
                "--timeout", "45",
                "--dump", "cookies",
                "--quiet",
            ])
            proc = subprocess.run(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=70, env=clean_env)
            try:
                cookies = json.loads(proc.stdout) if proc.returncode == 0 else []
            except (TypeError, ValueError):
                cookies = []
            error = None
            if proc.returncode:
                error = f"obscura_exit_{proc.returncode}: {proc.stderr[-500:]}"
            elif not isinstance(cookies, list):
                error = "invalid_cookie_response"
            return {"error": error, "cookies": cookies, "engine": "obscura"}

        command.extend([
            "fetch", job["url"],
            "--wait-until", "domcontentloaded",
            "--wait", str(min(10, max(0, int(job.get("settle_seconds", 1))))),
            "--timeout", "45",
            "--dump", "html",
            "--quiet",
        ])
        if job.get("wait_for"):
            command.extend(["--selector", str(job["wait_for"])])

        proc = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=70, env=clean_env)
        html = proc.stdout
        assets: dict[str, str] = {}
        total = 0
        deadline = time.monotonic() + 45
        if proc.returncode == 0 and job.get("cache_images"):
            for asset_url in _image_urls(html, job["url"]):
                if time.monotonic() >= deadline or total >= 12_000_000:
                    break
                asset_command = _base_command(binary, job, storage)
                asset_command.extend([
                    "fetch", asset_url, "--timeout", "15",
                    "--dump", "original", "--quiet",
                ])
                try:
                    asset = subprocess.run(
                        asset_command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        timeout=20, env=clean_env)
                except subprocess.TimeoutExpired:
                    continue
                data = asset.stdout
                if asset.returncode or not _looks_like_image(data) or len(data) > 2_000_000:
                    continue
                assets[asset_url] = base64.b64encode(data).decode("ascii")
                total += len(data)
    lower = html.lower()
    challenge = any(marker in lower for marker in (
        "<title>just a moment", "<title>attention required!",
        "performing security verification", "cf-chl-widget",
    ))
    if proc.returncode:
        error = f"obscura_exit_{proc.returncode}: {proc.stderr[-500:]}"
    elif challenge:
        error = "challenge_blocked"
    elif not html.strip():
        error = "empty_response"
    else:
        error = None
    return {
        "error": error,
        "html": html if not error else "",
        "initial_html": html if not error else "",
        "status": 200 if not error else 0,
        "assets": assets,
        "frames": [],
        "network_pages": [],
        "interaction_responses": [],
        "interaction_error": None,
        "engine": "obscura",
    }


def main() -> None:
    logging.basicConfig(level=logging.ERROR, stream=sys.stderr)
    try:
        result = crawl(json.load(sys.stdin))
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}", "html": "", "engine": "obscura"}
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
