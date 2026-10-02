"""Isolated Crawlee HTTP worker; browser rendering is owned by Obscura."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import logging
import os
import sys
import tempfile


async def crawl(job: dict) -> dict:
    from crawlee import ConcurrencySettings
    from crawlee.crawlers import HttpCrawler

    if job.get("mode") != "http":
        raise ValueError("Crawlee worker only supports HTTP-mode jobs")

    result = {"error": "request_not_processed (check robots.txt/access)", "html": "",
              "engine": "crawlee-http"}
    crawler = HttpCrawler(
        max_requests_per_crawl=1,
        max_request_retries=0,
        use_session_pool=False,
        retry_on_blocked=False,
        respect_robots_txt_file=True,
        ignore_http_error_status_codes=[403, 429, 503],
        concurrency_settings=ConcurrencySettings(
            min_concurrency=1, max_concurrency=1, desired_concurrency=1),
        request_handler_timeout=timedelta(seconds=50),
        configure_logging=False,
    )

    @crawler.router.default_handler
    async def handle(context):
        html = (await context.http_response.read()).decode("utf-8", errors="replace")
        status = context.http_response.status_code
        lower = html.lower()
        challenge = any(marker in lower for marker in (
            "<title>just a moment", "<title>attention required!", "cf-chl-widget"))
        error = "challenge_blocked" if challenge else (f"http_{status}" if status >= 400 else None)
        result.update(html=html if not error else "", status=status, error=error)

    @crawler.failed_request_handler
    async def failed(context, error):
        result["error"] = str(error)[:1500]

    await asyncio.wait_for(crawler.run([job["url"]]), timeout=85)
    return result


def main() -> None:
    logging.basicConfig(level=logging.ERROR, stream=sys.stderr)
    try:
        job = json.load(sys.stdin)
        with tempfile.TemporaryDirectory(prefix="diziflix-crawl-") as temp:
            os.environ["CRAWLEE_STORAGE_DIR"] = temp
            result = asyncio.run(crawl(job))
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}", "html": "",
                  "engine": "crawlee-http"}
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
