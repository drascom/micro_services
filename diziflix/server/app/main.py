"""diziflix — catalogue/playback API for the Tizen TV client.

See API.md for the contract this server implements.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import cache, config, db
from .http_cache import ETagMiddleware
from .routers import (
    catalog,
    boot,
    detail,
    health,
    images,
    mylist,
    notifications,
    onboard_sandbox,
    ops,
    ops_library,
    ops_onboard,
    ops_settings,
    ops_sites,
    ops_tmdb,
    profiles,
    progress,
    rows,
    search,
    stream_proxy,
    streams,
    subtitles,
)

log = logging.getLogger("diziflix")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    from .library import videos
    videos.backfill()
    cache.start()
    snap = cache.get()
    log.info("catalogue loaded: source=%s items=%d", snap.source, len(snap.items))
    try:
        from .scraper import onboard
        onboard.recover_stale()  # a site-onboarding run that was live when the server stopped can only be failed now
    except Exception:
        log.exception("onboarding: stale drafts not recovered")
    try:
        yield
    finally:
        cache.stop()


app = FastAPI(
    title="diziflix",
    version="1.0.0",
    description="Catalogue and playback API for the diziflix Tizen TV client.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["ETag"],
)
# Order (last added = outermost): gzip wraps ETag so the tag hashes the raw body.
app.add_middleware(ETagMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=500)


@app.middleware("http")
async def dev_tools(request: Request, call_next):
    """`?delay=<ms>` fakes latency, `?fail=1` forces a 500 — client-side
    skeleton and error-screen testing without touching the client."""
    params = request.query_params
    delay = params.get("delay")
    if delay:
        try:
            ms = max(0, min(30000, int(float(delay))))
            await asyncio.sleep(ms / 1000.0)
        except (TypeError, ValueError):
            pass
    if params.get("fail") in ("1", "true", "yes"):
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "forced_failure", "message": "?fail=1 was requested"}},
        )
    return await call_next(request)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        payload = {"code": detail["code"], "message": detail.get("message", "")}
        payload.update({k: v for k, v in detail.items() if k not in payload})   # extra fields (e.g. already_running -> running)
    else:
        payload = {"code": f"http_{exc.status_code}", "message": str(detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": payload})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "; ".join(
                    f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors()
                )
                or "invalid request",
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):  # pragma: no cover
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": str(exc)}},
    )


for module in (
    health,
    profiles,
    boot,
    rows,
    detail,
    streams,
    stream_proxy,
    subtitles,
    progress,
    mylist,
    notifications,
    search,
    images,
    ops,
    ops_library,
    ops_onboard,
    ops_settings,
    ops_sites,
    ops_tmdb,
    onboard_sandbox,
    catalog,
):
    app.include_router(module.router)


@app.get("/", include_in_schema=False)
def index() -> dict:
    return {"service": "diziflix", "source": config.SOURCE, "docs": "/docs", "api": "/api/health"}


def main() -> None:  # pragma: no cover - convenience entry point
    import uvicorn

    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT)


if __name__ == "__main__":  # pragma: no cover
    main()
