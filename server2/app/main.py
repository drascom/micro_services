"""diziflix server2 — catalogue/playback API (API.md) + admin control plane.

Registration points for later tasks (one line each):
- ``ROUTERS``: modules exposing ``router`` (an ``APIRouter``).
- ``startup_hooks`` / ``shutdown_hooks``: callables (sync or async) run after ``db.init()`` / at shutdown (reverse order).
"""
from __future__ import annotations

import inspect
import logging
from contextlib import asynccontextmanager
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, db
from .admin import routes_jobs
from .admin import assets as admin_assets, routes_sites  # T10
from .api import ROUTERS as API_ROUTERS, health  # T04b
from .control import jobs, wiring

log = logging.getLogger("diziflix")

ROUTERS: list[Any] = [
    health,
    routes_jobs,
    routes_sites, admin_assets,  # T10
    __import__(__package__ + ".admin.routes_catalog", fromlist=["router"]),  # T16
    __import__(__package__ + ".admin.routes_routing", fromlist=["router"]),  # T12
    __import__(__package__ + ".admin.routes_crawl", fromlist=["router"]),  # T11
    __import__(__package__ + ".admin.routes_settings", fromlist=["router"]),  # T15
    __import__(__package__ + ".admin.routes_problems", fromlist=["router"]),  # T14
    __import__(__package__ + ".admin.routes_categories", fromlist=["router"]),  # T24
    *API_ROUTERS,  # T04b
]

# Order matters: wiring binds job handlers / core hooks before the executor starts leasing.
startup_hooks: list[Callable[[], Any]] = [wiring.register, jobs.start]
shutdown_hooks: list[Callable[[], Any]] = [jobs.shutdown]


async def _run(fn: Callable[[], Any]) -> None:
    result = fn()
    if inspect.isawaitable(result):
        await result


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    for fn in startup_hooks:
        await _run(fn)
    try:
        yield
    finally:
        for fn in reversed(shutdown_hooks):
            try:
                await _run(fn)
            except Exception:
                log.exception("shutdown hook %r failed", fn)


app = FastAPI(
    title="diziflix server2",
    version=config.VERSION,
    description="Catalogue and playback API for the diziflix clients (API.md) and the admin control plane.",
    lifespan=lifespan,
)

CONTROL_PREFIX = "/api/control/"
_WRITES = {"POST", "PUT", "PATCH", "DELETE"}


def _same_origin(origin: str, host: str) -> bool:
    netloc = urlsplit(origin).netloc.lower() if "://" in origin else ""
    return bool(netloc) and netloc == host.lower()


class ClientCORS(CORSMiddleware):
    """CORS ``*`` only for the client API. ``/api/control/*`` gets no CORS headers (a foreign page cannot read it) and
    its writes are refused (403 ``forbidden_origin``) when an ``Origin`` header is present and is not the request's
    own host (the admin page is same-origin; curl sends no ``Origin``)."""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not (scope.get("path") or "").startswith(CONTROL_PREFIX):
            await super().__call__(scope, receive, send)
            return
        if scope.get("method") in _WRITES:
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
            origin = headers.get("origin")
            if origin is not None and not _same_origin(origin, headers.get("host", "")):
                response = JSONResponse(status_code=403, content={"error": {
                    "code": "forbidden_origin", "message": "yönetim uçlarına başka bir kökenden yazılamaz"}})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


app.add_middleware(
    ClientCORS,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["ETag"],
)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        payload = {"code": detail["code"], "message": detail.get("message", "")}
        payload.update({k: v for k, v in detail.items() if k not in payload})
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


for module in ROUTERS:
    app.include_router(module.router)
