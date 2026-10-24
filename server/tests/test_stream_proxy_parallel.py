"""Stream proxy parallel range relay (``routers/stream_proxy._ParallelRelay``): a per-connection-capped host (OK.ru okcdn,
~2 Mbit/s each) is read as parallel ranges and handed out in order. Network-free (httpx MockTransport)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import asyncio
import unittest

import httpx

from app.routers import stream_proxy as sp

BODY = bytes(i % 251 for i in range(10_000))


def _handler(fail_from=None):
    def handle(request: httpx.Request) -> httpx.Response:
        first, last = (int(x) for x in request.headers["range"].split("=")[1].split("-"))
        if fail_from is not None and first >= fail_from:
            return httpx.Response(403)
        return httpx.Response(206, stream=httpx.ByteStream(BODY[first:last + 1]),
                              headers={"content-range": f"bytes {first}-{last}/{len(BODY)}"})
    return handle


class _Slot:
    released = 0

    def release(self):
        self.released += 1


async def _drain(handler, start, end, trace=None):
    slot = _Slot()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        relay = sp._ParallelRelay(client, "https://vd1.okcdn.ru/?id=1", {"User-Agent": "x", "Range": "bytes=0-"},
                                  start, end, slot, chunk=1024, parallel=3, trace=trace)
        out = b"".join([piece async for piece in relay])
    return out, slot.released


class ParallelRelayTest(unittest.TestCase):
    def test_in_order_whole_and_partial(self):
        out, released = asyncio.run(_drain(_handler(), 0, len(BODY) - 1))
        self.assertEqual(out, BODY)
        self.assertEqual(released, 1)
        out, _ = asyncio.run(_drain(_handler(), 1500, 7777))
        self.assertEqual(out, BODY[1500:7778])

    def test_failed_range_ends_body_short(self):
        trace = {"at": 0.0, "bytes": 0, "end": "open", "ms": None}
        out, released = asyncio.run(_drain(_handler(fail_from=4096), 0, len(BODY) - 1, trace))
        self.assertEqual(out, BODY[:4096])
        self.assertEqual(released, 1)
        self.assertEqual((trace["bytes"], trace["end"]), (4096, "upstream-error"))   # request log: /api/ops/stream-proxy/recent
        trace = {"at": 0.0, "bytes": 0, "end": "open", "ms": None}
        asyncio.run(_drain(_handler(), 0, len(BODY) - 1, trace))
        self.assertEqual((trace["bytes"], trace["end"]), (len(BODY), "done"))

    def test_capped_hosts_and_span(self):
        self.assertTrue(sp._parallel_host("https://vd1.okcdn.ru/?x=1"))
        self.assertTrue(sp._parallel_host("https://ok6-29.vkuser.net/?x=1"))
        self.assertFalse(sp._parallel_host("https://prx-1316-ant.vmget.online/a.m3u8"))
        self.assertEqual(sp._span(httpx.Response(206, headers={"content-range": "bytes 5-9/20"})), (5, 9))
        self.assertEqual(sp._span(httpx.Response(200, headers={"content-length": "20"})), (0, 19))


if __name__ == "__main__":
    unittest.main()
