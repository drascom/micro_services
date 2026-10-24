"""Canlı TV M3U sources: an admin playlist (link or uploaded file) -> ``channels`` rows (``source_id`` = ``live_sources.id``).

A source (``live_sources``): ``kind`` ``link`` (an http(s) playlist URL, re-fetched on the hourly live scan, ``live.tick``) or
``file`` (the uploaded text, kept under ``DATA_DIR/live/m3u/<id>.m3u``, static; "Yenile" re-imports and re-probes it).

Parse (:func:`parse`): ``#EXTINF`` attributes (tvg-id, tvg-name, tvg-logo, group-title, tvg-language, tvg-country), the
name after the comma, ``#EXTGRP``, ``#EXTVLCOPT`` / ``#KODIPROP`` http-referrer / http-user-agent (-> the channel's request
headers: the stream is then handed out through the server's stream proxy). Same URL twice = one channel; the same tvg-id
= one channel with several streams. Category = slug of group-title (known names map to famelack's categories), the original
title is kept in ``channels.labels``. M3U channels are never language-filtered and never touched by the famelack pass.

LLM (:func:`llm_fix`, admin "LLM ile düzelt", default on): the parts the parser could not read (an EXTINF without a URL, a
URL without a name, a text line holding a URL, a non-M3U text that looks like a channel list) go in bounded chunks to the
heal LLM (``settings.heal_provider``: pi one-shot ``heal._call_pi`` / codex ``heal._call_codex``) which answers JSON
``[{name,url,logo,group,language}]``. The answer is validated: http(s) URLs only, and only URLs that occur in that chunk
(no invented URL). LLM off (``SCRAPER_HEAL_ENABLED``) / unreachable -> skipped, said in the summary.

Fetch: ``netguard.check_url`` on every hop (redirects followed by hand), timeout, :data:`MAX_BYTES`.
Health: the same probe as famelack (``live.probe_all`` / ``live.apply_probe``), in the background after an import.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import threading
import time
import unicodedata
import uuid
from contextlib import closing
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlsplit

from .. import config, db, netguard
from . import live

log = logging.getLogger("diziflix.m3u")

MAX_BYTES = 5 * 1024 * 1024
FETCH_TIMEOUT = 20.0
MAX_REDIRECTS = 5
INTERVAL_MINUTES = 60
CHUNK_CHARS = 6000          # one LLM prompt's input
MAX_CHUNKS = 6              # per import
LLM_TIMEOUT = 90            # seconds per chunk
MAX_NAME = 200
KINDS = ("link", "file")

# group-title -> famelack category slug (row titles then come from live.CATEGORY_TITLES)
GROUP_ALIASES = {
    "haber": "news", "haberler": "news", "news": "news", "spor": "sports", "sport": "sports", "sports": "sports",
    "cocuk": "kids", "kids": "kids", "children": "kids", "film": "movies", "filmler": "movies", "sinema": "movies",
    "movies": "movies", "movie": "movies", "dizi": "series", "diziler": "series", "series": "series",
    "muzik": "music", "music": "music", "belgesel": "documentary", "documentary": "documentary",
    "genel": "general", "ulusal": "general", "general": "general", "din": "religious", "dini": "religious",
    "religious": "religious", "eglence": "entertainment", "entertainment": "entertainment",
    "ekonomi": "business", "business": "business", "egitim": "education", "education": "education",
}

_lock = threading.Lock()          # one import at a time (writes + probe bookkeeping)
_refresh_lock = threading.Lock()  # one background refresh pass at a time
_URL_RE = re.compile(r"https?://[^\s\"'<>,|]+", re.I)
_ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)\s*=\s*"([^"]*)"|([A-Za-z0-9_-]+)\s*=\s*([^\s",]+)')
_HEADER_OPTS = {"http-referrer": "Referer", "http-referer": "Referer", "http-user-agent": "User-Agent",
                "http-origin": "Origin"}


class LLMUnavailable(Exception):
    """The LLM is switched off or could not be reached (the import goes on without it)."""


# ---- parse ------------------------------------------------------------------------------------------------------------------
def _fold(text: str) -> str:
    text = text.replace("İ", "i").replace("I", "ı").casefold().replace("ı", "i")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def category_of(group: str) -> str:
    """group-title -> category slug ('' = none)."""
    slug = re.sub(r"[^a-z0-9]+", "-", _fold(group or "")).strip("-")[:40]
    return GROUP_ALIASES.get(slug, slug)


def _is_url(line: str) -> bool:
    return bool(re.match(r"^https?://\S+$", line, re.I))


def _blocked_host(url: str) -> bool:
    """A stream URL the server must not touch without a DNS lookup: localhost / a non-public IP literal."""
    try:
        host = (urlsplit(url).hostname or "").rstrip(".").lower()
    except ValueError:
        return True
    if not host or host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return netguard._forbidden(ipaddress.ip_address(host))
    except ValueError:
        return False


def _parse_extinf(line: str) -> Optional[dict]:
    """``#EXTINF:-1 tvg-id=".." group-title="..",Name`` -> attrs + name; ``None`` = unreadable."""
    body = line[len("#EXTINF:"):]
    # the name follows the last comma that is outside quotes
    in_q, cut = False, -1
    for i, ch in enumerate(body):
        if ch == '"':
            in_q = not in_q
        elif ch == "," and not in_q:
            cut = i
    if cut < 0:
        if body.count('"') % 2:      # unbalanced quotes and no comma: broken
            return None
        head, name = body, ""
    else:
        head, name = body[:cut], body[cut + 1:]
    attrs = {}
    for m in _ATTR_RE.finditer(head):
        key = (m.group(1) or m.group(3) or "").lower()
        attrs[key] = (m.group(2) if m.group(1) else m.group(4) or "").strip()
    name = name.strip() or attrs.get("tvg-name", "").strip()
    return {"attrs": attrs, "name": name[:MAX_NAME]}


def parse(text: str) -> tuple[list[dict], list[str], dict]:
    """``(entries, problem lines, stats)``. An entry: ``{name, url, logo, group, language, country, tvg_id, headers}``.
    Problem lines are the raw lines the parser could not turn into an entry (with a little context), for the LLM."""
    text = (text or "").lstrip("﻿")
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    is_m3u = any(ln.upper().startswith(("#EXTM3U", "#EXTINF")) for ln in lines)
    entries: list[dict] = []
    problems: list[str] = []
    stats = {"lines": len(lines), "m3u": is_m3u}
    if not is_m3u:
        # not a playlist: every line holding a URL (plus the line before it, often the name) goes to the LLM
        for i, ln in enumerate(lines):
            if _URL_RE.search(ln):
                if i and lines[i - 1] and not _URL_RE.search(lines[i - 1]):
                    problems.append(lines[i - 1])
                problems.append(ln)
        return entries, problems, stats
    pending: Optional[dict] = None        # the EXTINF waiting for its URL
    pending_raw: list[str] = []
    headers: dict[str, str] = {}
    group_line = ""

    def flush_orphan():
        if pending_raw:
            problems.extend(pending_raw)

    for ln in lines:
        if not ln:
            continue
        up = ln.upper()
        if up.startswith("#EXTM3U"):
            continue
        if up.startswith("#EXTINF"):
            if pending is not None:
                flush_orphan()            # the previous EXTINF never got a URL
            info = _parse_extinf(ln)
            pending, pending_raw, headers, group_line = (info or {"attrs": {}, "name": "", "broken": True}), [ln], {}, ""
            continue
        if up.startswith(("#EXTVLCOPT", "#KODIPROP")):
            opt = ln.split(":", 1)[1] if ":" in ln else ""
            key, _, value = opt.partition("=")
            key = key.strip().lower().rsplit(".", 1)[-1]   # inputstream.adaptive.http-user-agent style too
            if key in _HEADER_OPTS and value.strip():
                headers[_HEADER_OPTS[key]] = value.strip()
            if pending is not None:
                pending_raw.append(ln)
            continue
        if up.startswith("#EXTGRP:"):
            group_line = ln.split(":", 1)[1].strip()
            continue
        if ln.startswith("#"):
            continue
        if _is_url(ln):
            if pending is None or pending.get("broken") or not pending["name"]:
                # a URL without a usable EXTINF / name
                problems.extend(pending_raw + [ln])
            else:
                a = pending["attrs"]
                entries.append({"name": pending["name"], "url": ln, "logo": a.get("tvg-logo", ""),
                                "group": a.get("group-title", "") or group_line, "language": a.get("tvg-language", ""),
                                "country": a.get("tvg-country", ""), "tvg_id": a.get("tvg-id", ""),
                                "headers": dict(headers)})
            pending, pending_raw, headers, group_line = None, [], {}, ""
            continue
        # a text line: maybe "Name http://..." or garbage inside an EXTINF block
        if pending is not None:
            pending_raw.append(ln)
        elif _URL_RE.search(ln):
            problems.append(ln)
    if pending is not None:
        flush_orphan()
    return entries, problems, stats


# ---- LLM --------------------------------------------------------------------------------------------------------------------
def chunks(problem_lines: list[str], size: int = CHUNK_CHARS, limit: int = MAX_CHUNKS) -> list[str]:
    out, cur = [], ""
    for ln in problem_lines:
        ln = ln[:2000]
        if cur and len(cur) + len(ln) + 1 > size:
            out.append(cur)
            cur = ""
            if len(out) >= limit:
                return out
        cur += ln + "\n"
    if cur and len(out) < limit:
        out.append(cur)
    return out


PROMPT = """You repair a broken IPTV channel list. Below is text that a normal M3U parser could not read.
Extract every TV channel it contains. Answer ONLY with a JSON array, no prose:
[{"name": "...", "url": "...", "logo": "...", "group": "...", "language": "..."}]
Rules: "url" must be copied EXACTLY from the text (an http/https stream URL); never invent or alter a URL; skip anything
without a URL. "name" = the channel name (guess it from the text or the URL if missing). "logo", "group", "language" = from
the text or "" when unknown. No markdown fences.

TEXT:
"""


def default_llm(prompt: str) -> str:
    """One-shot text answer of the heal LLM; :class:`LLMUnavailable` when it is off or fails."""
    from .. import settings
    from ..scraper import heal
    if not heal._enabled():
        raise LLMUnavailable("LLM kapalı (SCRAPER_HEAL_ENABLED)")
    provider = settings.heal_provider()
    if provider == "codex_cli":
        res = heal._call_codex(prompt, LLM_TIMEOUT)
    elif provider in ("pi", "pi_agent"):
        res = heal._call_pi(prompt, LLM_TIMEOUT)
    else:
        raise LLMUnavailable(f"sağlayıcı {provider} desteklenmiyor")
    if res.get("status") != "ok" or not res.get("text"):
        raise LLMUnavailable(str(res.get("reason") or res.get("error") or "LLM yanıt vermedi")[:200])
    return res["text"]


def _json_array(text: str) -> list:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start = text.find("[")
    if start < 0:
        return []
    try:
        data = json.JSONDecoder().raw_decode(text[start:])[0]
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def _short(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def validate_llm(answer: str, chunk: str) -> list[dict]:
    """The LLM's JSON answer reduced to entries whose URL is http(s) AND occurs verbatim in ``chunk`` (no invented URL);
    a logo is kept only under the same rule."""
    seen_urls = set(_URL_RE.findall(chunk))
    out = []
    for item in _json_array(answer):
        if not isinstance(item, dict):
            continue
        url = _short(item.get("url"), 2048)
        name = _short(item.get("name"), MAX_NAME)
        if not url or not name or not _is_url(url) or url not in seen_urls:
            continue
        logo = _short(item.get("logo"), 2048)
        if logo and (not _is_url(logo) or logo not in seen_urls):
            logo = ""
        out.append({"name": name, "url": url, "logo": logo, "group": _short(item.get("group"), 80),
                    "language": _short(item.get("language"), 40), "country": "", "tvg_id": "", "headers": {}})
    return out


def llm_fix(problem_lines: list[str], llm: Optional[Callable[[str], str]] = None) -> tuple[list[dict], str]:
    """``(entries, status)``: status ``not_needed`` | ``ok`` | ``off: <why>`` (LLM unavailable, skipped) | ``error: <why>``."""
    if not problem_lines:
        return [], "not_needed"
    llm = llm or default_llm
    out: list[dict] = []
    for chunk in chunks(problem_lines):
        try:
            answer = llm(PROMPT + chunk)
        except LLMUnavailable as exc:
            return out, f"off: {exc}"
        except Exception as exc:   # pragma: no cover - a broken provider must not fail the import
            return out, f"error: {type(exc).__name__}"
        out += validate_llm(answer or "", chunk)
    return out, "ok"


# ---- build + write --------------------------------------------------------------------------------------------------------
def _nanoid(source_id: str, key: str) -> str:
    return "m3u" + source_id + hashlib.sha1(key.encode()).hexdigest()[:10]


def build(source_id: str, entries: list[dict]) -> tuple[dict[str, dict], int]:
    """``({nanoid: record}, skipped)``: same URL = one channel, same tvg-id = one channel with several streams."""
    out: dict[str, dict] = {}
    by_url: dict[str, str] = {}
    skipped = 0
    for e in entries:
        url = e["url"]
        if not _is_url(url) or len(url) > 2048 or _blocked_host(url):
            skipped += 1
            continue
        if url in by_url:            # duplicate URL: merge into the first channel
            rec = out[by_url[url]]
            cat = category_of(e.get("group", ""))
            if cat and cat not in rec["categories"]:
                rec["categories"].append(cat)
                if cat not in live.CATEGORY_TITLES:
                    rec["labels"][cat] = e["group"][:80]
            continue
        key = ("id:" + e["tvg_id"].lower()) if e.get("tvg_id") else ("url:" + url)
        nid = _nanoid(source_id, key)
        rec = out.get(nid)
        if rec is None:
            langs = [x.strip().lower() for x in re.split(r"[;,]", e.get("language") or "") if x.strip()][:5]
            country = next((x.strip().lower() for x in re.split(r"[;,]", e.get("country") or "") if x.strip()), "")
            logo = e.get("logo") or ""
            rec = out[nid] = {"nanoid": nid, "name": e["name"], "country": country[:8], "languages": langs,
                              "categories": [], "labels": {}, "streams": [], "youtube": [], "geo_blocked": False,
                              "logo": logo if _is_url(logo) else "", "headers": {}}
        rec["streams"].append(url)
        by_url[url] = nid
        if e.get("headers") and not rec["headers"]:
            rec["headers"] = dict(e["headers"])
        cat = category_of(e.get("group", ""))
        if cat and cat not in rec["categories"]:
            rec["categories"].append(cat)
            if cat not in live.CATEGORY_TITLES:
                rec["labels"][cat] = e["group"][:80]
    return out, skipped


def upsert(source_id: str, records: dict[str, dict], now: Optional[int] = None) -> dict:
    """Write one source's channels; its channels missing from ``records`` become ``active=0``. Stream health of a url
    that stays is kept. ``{new, updated, unchanged, deactivated}``."""
    from .live import _loads, channel_health, ID_PREFIX, removed_mark
    now = int(time.time()) if now is None else int(now)
    stats = dict.fromkeys(("new", "updated", "unchanged", "deactivated"), 0)
    with closing(db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = {r["nanoid"]: r for r in conn.execute("SELECT * FROM channels WHERE source_id=?", (source_id,))}
        for nid, rec in records.items():
            old = existing.get(nid)
            fields = (rec["name"], rec["country"], json.dumps(rec["languages"]), json.dumps(rec["categories"]),
                      json.dumps(rec["streams"]), json.dumps(rec["youtube"]), 0, rec["logo"] or None,
                      json.dumps(rec["headers"]) if rec["headers"] else None, json.dumps(rec["labels"], ensure_ascii=False))
            if old is None:
                conn.execute("INSERT INTO channels (id,nanoid,name,country,languages,categories,streams,youtube,geo_blocked,"
                             "logo,headers,labels,source_id,stream_health,health,active,seen_at,updated_at,created_at,"
                             "removed,removed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)",
                             (ID_PREFIX + nid, nid, *fields, source_id, "{}", channel_health(rec["streams"], [], {}),
                              now, now, now, *removed_mark(conn, rec["streams"], now)))
                stats["new"] += 1
                continue
            health = {u: h for u, h in _loads(old["stream_health"], {}).items() if u in rec["streams"]}
            same = (old["name"], old["country"], old["languages"], old["categories"], old["streams"], old["youtube"],
                    old["geo_blocked"], old["logo"], old["headers"], old["labels"]) == fields and old["active"] == 1
            conn.execute("UPDATE channels SET name=?,country=?,languages=?,categories=?,streams=?,youtube=?,geo_blocked=?,"
                         "logo=?,headers=?,labels=?,stream_health=?,health=?,active=1,seen_at=?,updated_at=? WHERE nanoid=?",
                         (*fields, json.dumps(health), channel_health(rec["streams"], [], health), now,
                          old["updated_at"] if same else now, nid))
            stats["unchanged" if same else "updated"] += 1
        for nid, old in existing.items():
            if nid not in records and old["active"] == 1:
                conn.execute("UPDATE channels SET active=0,updated_at=? WHERE nanoid=?", (now, nid))
                stats["deactivated"] += 1
    live.invalidate()
    return stats


# ---- fetch -----------------------------------------------------------------------------------------------------------------
def fetch(url: str, client=None) -> str:
    """The playlist text at ``url``: every hop through ``netguard.check_url`` (redirects by hand), at most
    :data:`MAX_BYTES`, :data:`FETCH_TIMEOUT` seconds. ``ValueError`` with a readable reason on failure."""
    own = client is None
    if own:
        import httpx
        client = httpx.Client(timeout=FETCH_TIMEOUT, follow_redirects=False)
    try:
        for _ in range(MAX_REDIRECTS + 1):
            url = netguard.check_url(url)
            with client.stream("GET", url, headers={"User-Agent": live.UA}) as resp:
                if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                    url = urljoin(url, resp.headers["location"])
                    continue
                if resp.status_code >= 400:
                    raise ValueError(f"HTTP {resp.status_code}")
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf += chunk
                    if len(buf) > MAX_BYTES:
                        raise ValueError(f"dosya {MAX_BYTES // (1024 * 1024)} MB sınırını aşıyor")
                return decode(bytes(buf))
        raise ValueError("çok fazla yönlendirme")
    except ValueError:
        raise
    except Exception as exc:   # timeouts, TLS, DNS ...
        raise ValueError(f"indirilemedi ({type(exc).__name__})") from exc
    finally:
        if own:
            client.close()


def decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1254", errors="replace")


# ---- sources --------------------------------------------------------------------------------------------------------------
def _files_dir() -> str:
    return os.path.join(config.DATA_DIR, "live", "m3u")


def _file_path(source_id: str) -> str:
    return os.path.join(_files_dir(), source_id + ".m3u")


def _source_row(source_id: str):
    return db.query_one("SELECT * FROM live_sources WHERE id=?", (source_id,))


def list_sources() -> list[dict]:
    """Admin list: every source with its channel counts (``channels`` active, ``healthy`` = a usable m3u8)."""
    from .live import _loads, channel_health
    counts: dict[str, list[int]] = {}
    for r in db.query("SELECT source_id,streams,stream_health FROM channels WHERE source_id IS NOT NULL AND active=1"):
        c = counts.setdefault(r["source_id"], [0, 0])
        c[0] += 1
        if channel_health(_loads(r["streams"], []), [], _loads(r["stream_health"], {})) == "ok":
            c[1] += 1
    out = []
    for r in db.query("SELECT * FROM live_sources ORDER BY created_at"):
        n, ok = counts.get(r["id"], [0, 0])
        out.append({"id": r["id"], "name": r["name"], "kind": r["kind"], "url": r["url"], "llm": bool(r["llm"]),
                    "channels": n, "healthy": ok, "ran_at": r["ran_at"], "created_at": r["created_at"],
                    "last": live._loads(r["last"], {})})
    return out


def create(name: str, kind: str, *, url: str = "", content: str = "", llm: bool = True,
           llm_call: Optional[Callable[[str], str]] = None, client=None, probe: bool = True) -> dict:
    """New source + its first import. ``ValueError`` on bad input (nothing is stored then)."""
    name = (name or "").strip()[:80]
    if kind not in KINDS:
        raise ValueError("tür link ya da file olmalı")
    if kind == "link":
        url = netguard.check_url((url or "").strip())
        text = fetch(url, client=client)
    else:
        if not content or not content.strip():
            raise ValueError("dosya boş")
        if len(content.encode("utf-8", errors="ignore")) > MAX_BYTES:
            raise ValueError(f"dosya {MAX_BYTES // (1024 * 1024)} MB sınırını aşıyor")
        text, url = content, ""
    sid = uuid.uuid4().hex[:8]
    name = name or (urlsplit(url).hostname if url else "") or "M3U"
    now = int(time.time())
    if kind == "file":
        os.makedirs(_files_dir(), exist_ok=True)
        with open(_file_path(sid), "w", encoding="utf-8") as fh:
            fh.write(text)
    db.execute("INSERT INTO live_sources (id,name,kind,url,llm,created_at) VALUES (?,?,?,?,?,?)",
               (sid, name, kind, url or None, int(bool(llm)), now))
    summary = import_text(sid, text, llm=llm, llm_call=llm_call, probe=probe)
    return {"source": next((s for s in list_sources() if s["id"] == sid), None), "summary": summary}


def refresh(source_id: str, *, llm_call: Optional[Callable[[str], str]] = None, client=None, probe: bool = True) -> dict:
    """Re-import a source (link: fetched again; file: the stored text). ``KeyError`` = unknown source."""
    r = _source_row(source_id)
    if not r:
        raise KeyError(source_id)
    if r["kind"] == "link":
        try:
            text = fetch(r["url"], client=client)
        except ValueError as exc:
            summary = {"error": str(exc)}
            db.execute("UPDATE live_sources SET last=?,ran_at=? WHERE id=?",
                       (json.dumps(summary, ensure_ascii=False), int(time.time()), source_id))
            return summary
    else:
        try:
            with open(_file_path(source_id), "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            raise ValueError("yüklenen dosya bulunamadı")
    return import_text(source_id, text, llm=bool(r["llm"]), llm_call=llm_call, probe=probe)


def delete(source_id: str) -> int:
    """Drop a source and every channel of it; the number of channels removed. ``KeyError`` = unknown source."""
    if not _source_row(source_id):
        raise KeyError(source_id)
    with closing(db.connect()) as conn, conn:
        n = conn.execute("DELETE FROM channels WHERE source_id=?", (source_id,)).rowcount
        conn.execute("DELETE FROM live_sources WHERE id=?", (source_id,))
    try:
        os.remove(_file_path(source_id))
    except OSError:
        pass
    live.invalidate()
    return n


def import_text(source_id: str, text: str, *, llm: bool = True, llm_call: Optional[Callable[[str], str]] = None,
                probe: bool = True) -> dict:
    """Parse (+ LLM for the unreadable parts) -> upsert -> probe in the background. The summary:
    ``{parsed, llm_fixed, llm, added, updated, unchanged, deactivated, skipped, channels, problems}``."""
    with _lock:
        entries, problems, stats = parse(text)
        fixed: list[dict] = []
        status = "not_needed" if not problems else "disabled"
        if problems and llm:
            fixed, status = llm_fix(problems, llm_call)
        known = {e["url"] for e in entries}
        fixed = [e for e in fixed if e["url"] not in known]
        records, skipped = build(source_id, entries + fixed)
        up = upsert(source_id, records)
        # problems = unreadable lines; unresolved = those left unread because the LLM was off / failed
        summary = {"parsed": len(entries), "llm_fixed": len(fixed), "llm": status, "problems": len(problems),
                   "unresolved": len(problems) if status != "ok" else 0,
                   "added": up["new"], "updated": up["updated"], "unchanged": up["unchanged"],
                   "deactivated": up["deactivated"], "skipped": skipped, "channels": len(records), "m3u": stats["m3u"]}
        db.execute("UPDATE live_sources SET last=?,ran_at=? WHERE id=?",
                   (json.dumps(summary, ensure_ascii=False), int(time.time()), source_id))
    if probe and records:
        headers = {u: r["headers"] for r in records.values() for u in r["streams"] if r["headers"]}
        urls = sorted({u for r in records.values() for u in r["streams"]})
        threading.Thread(target=_probe, args=(urls, headers), name="m3u-probe", daemon=True).start()
    log.info("m3u import %s: %s", source_id, summary)
    return summary


def _probe(urls: list[str], headers: dict) -> None:
    try:
        live.apply_probe(live.probe_all(urls, live.load_config(), headers=headers))
    except Exception:
        log.exception("m3u probe failed")


def refresh_due(now: Optional[float] = None) -> list[str]:
    """Link sources whose last import is older than :data:`INTERVAL_MINUTES` are refreshed in one background thread.
    Returns the ids started (``[]`` when a pass is already running or nothing is due)."""
    now = time.time() if now is None else now
    try:
        rows = db.query("SELECT id,ran_at FROM live_sources WHERE kind='link'")
    except Exception:
        return []
    due = [r["id"] for r in rows if now - float(r["ran_at"] or 0) >= INTERVAL_MINUTES * 60]
    if not due or not _refresh_lock.acquire(blocking=False):
        return []

    def work():
        try:
            for sid in due:
                try:
                    refresh(sid)
                except Exception:
                    log.exception("m3u refresh %s failed", sid)
        finally:
            _refresh_lock.release()

    threading.Thread(target=work, name="m3u-refresh", daemon=True).start()
    return due
