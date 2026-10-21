"""ETag / Cache-Control for cacheable JSON GET endpoints (pure ASGI).

The ETag is a hash of the (uncompressed) body, so it must sit *inside*
GZipMiddleware. Clients revalidating with ``If-None-Match`` get a bodiless 304.
"""
from __future__ import annotations

import hashlib

# path prefix -> Cache-Control. The TV client sends cache:'no-cache', which
# always revalidates against the ETag; max-age only helps other clients.
_SHORT = "private, max-age=5, stale-while-revalidate=30"
POLICIES: tuple[tuple[str, str], ...] = (
    ("/api/boot", _SHORT),
    ("/api/row/", _SHORT),
    ("/api/detail/", _SHORT),
    ("/api/catalog", _SHORT),
    ("/api/sources", _SHORT),
    ("/api/avatars", "public, max-age=3600"),
)


def _policy(path: str):
    for prefix, value in POLICIES:
        if path == prefix or path.startswith(prefix):
            return value
    return None


def _matches(header: str, tag: str) -> bool:
    if not header:
        return False
    cands = {c.strip() for c in header.split(",")}
    bare = tag[2:] if tag.startswith("W/") else tag
    return "*" in cands or tag in cands or bare in cands or ("W/" + bare) in cands


class ETagMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["method"] != "GET":
            return await self.app(scope, receive, send)
        cache_control = _policy(scope["path"])
        if cache_control is None:
            return await self.app(scope, receive, send)

        start = None
        chunks: list[bytes] = []

        async def capture(message) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
                return
            if message["type"] != "http.response.body":  # pragma: no cover
                return await send(message)
            chunks.append(message.get("body", b""))
            if message.get("more_body"):
                return
            body = b"".join(chunks)
            headers = [(k, v) for k, v in start["headers"]]
            ctype = next((v for k, v in headers if k.lower() == b"content-type"), b"")
            if start["status"] != 200 or b"json" not in ctype:
                await send(start)
                return await send({"type": "http.response.body", "body": body})
            tag = 'W/"' + hashlib.sha1(body).hexdigest()[:24] + '"'
            headers = [(k, v) for k, v in headers
                       if k.lower() not in (b"etag", b"cache-control")]
            headers += [(b"etag", tag.encode()), (b"cache-control", cache_control.encode())]
            inm = ""
            for k, v in scope["headers"]:
                if k == b"if-none-match":
                    inm = v.decode("latin-1")
            if _matches(inm, tag):
                headers = [(k, v) for k, v in headers
                           if k.lower() not in (b"content-length", b"content-type")]
                await send({"type": "http.response.start", "status": 304, "headers": headers})
                return await send({"type": "http.response.body", "body": b""})
            headers = [(k, v) for k, v in headers if k.lower() != b"content-length"]
            headers.append((b"content-length", str(len(body)).encode()))
            await send({"type": "http.response.start", "status": 200, "headers": headers})
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, capture)
