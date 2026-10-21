"""Manual smoke run of the onboarding flow against a RUNNING server2 (real network, real LLM). Never run by the tests.

    venv/bin/python -m tools.smoke_site https://example.tld [--server http://127.0.0.1:8091] [--timeout 1800]

Adds the site through the admin API (``POST /api/control/sites``), polls ``/api/control/sites/<id>/stages`` and prints every
stage state change with its summary. Stops with exit 2 when a stage needs a human (prints the evidence), 0 when every
non-optional stage passed and nothing is running, 1 on timeout / API error.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DONE = {"passed", "passed_warn", "skipped"}
HUMAN = {"needs_human", "engine_gap"}


def call(server: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(server.rstrip("/") + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read().decode("utf-8")).get("error") or {}
        except ValueError:
            err = {}
        raise SystemExit(f"HATA {path}: HTTP {exc.code} {err.get('code', '')} {err.get('message', '')}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"HATA sunucuya ulaşılamıyor ({server}): {exc.reason}") from None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="server2 site onboarding smoke run")
    ap.add_argument("url")
    ap.add_argument("--server", default="http://127.0.0.1:8091")
    ap.add_argument("--timeout", type=int, default=1800, help="seconds")
    ap.add_argument("--poll", type=float, default=3.0)
    args = ap.parse_args(argv)

    created = call(args.server, "/api/control/sites", {"url": args.url})
    site_id = created["site_id"]
    print(f"site: {site_id}  (aşamalar başladı: {created.get('started')})")
    seen: dict[str, tuple] = {}
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        data = call(args.server, f"/api/control/sites/{site_id}/stages")
        stages = data.get("stages") or []
        for st in stages:
            sig = (st.get("state"), st.get("summary"), st.get("round"))
            if seen.get(st["id"]) != sig:
                seen[st["id"]] = sig
                print(f"{time.strftime('%H:%M:%S')} {st['id']:<3} {st.get('title', ''):<14} {st.get('state'):<12} {st.get('summary') or ''}")
        stuck = [st for st in stages if st.get("state") in HUMAN]
        if stuck:
            for st in stuck:
                print(f"\n== {st['id']} {st.get('state')}: {st.get('summary') or ''}")
                for c in st.get("checks") or []:
                    print(f"   {'ok ' if c.get('ok') else 'NO '} {c.get('name')}: {c.get('value')} (sınır {c.get('bound')})")
                print(json.dumps(st.get("evidence") or {}, ensure_ascii=False, indent=2)[:3000])
            print(f"\nadmin: {args.server.rstrip('/')}/admin#/site/{site_id}")
            return 2
        required = [st for st in stages if not st.get("optional")]
        if required and all(st.get("state") in DONE for st in required) and not data.get("busy"):
            print(f"\ntamam: zorunlu aşamalar geçti. admin: {args.server.rstrip('/')}/admin#/site/{site_id}")
            return 0
        time.sleep(args.poll)
    print("zaman aşımı")
    return 1


if __name__ == "__main__":
    sys.exit(main())
