"""Per-site handoff note ("devir notu"): what the next edit / repair agent needs to know about a site, in a few KB.

``DATA_DIR/site_handoffs/<site_id>.md`` (atomic write, one lock). The edit and the heal / repair agent read it with
``load_site_config`` (answer key ``handoff``) instead of being given the whole history. Sections:

    # <site> devir notu
    <!-- meta: {"scans": n, "last_scan_at": ts, "last_sig": "..."} -->
    ## Güncel durum                      deterministic: version, playback, list, collections, players, search ...
    ## Bulgular ve çözümler             the AGENT's text (``submit_draft(handoff=...)``), <= NARRATIVE_MAX
    ## Kullanıcı talimatları ve kararları   deterministic: the admin's messages, ask_user questions + answers, skipped fields
    ## Sonuçlar                         deterministic: criteria summary + the measured lines of the first scans
    ## Değişiklik geçmişi               dated entries (<= CHANGE_MAX each), newest first; at most HISTORY_MAX, older ones one line each

Hard limits: the whole file <= TOTAL_MAX bytes (``_fit`` trims scan lines, old changes, the narrative ... in that order), nothing that looks
like a secret is ever written (``pi_agent.scrub`` + cookie / URL-token patterns), the site id must be ``[a-z0-9_-]``.

The API never raises on content (a bad site id returns False / ""); the scan hook ``record_scan`` swallows everything: a handoff problem
must never break a scan. No network, no LLM.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from typing import Any, Optional

from .. import config

log = logging.getLogger("scraper.site_handoff")

TOTAL_MAX = 6 * 1024          # bytes of the whole file
NARRATIVE_MAX = 2560          # the agent's "Bulgular ve çözümler" text
CHANGE_MAX = 600              # one change-history entry
HISTORY_MAX = 8               # change entries kept in full; older ones become one line each
OLD_MAX = 5                   # one-line summaries kept
USER_MAX = 14                 # user lines kept (newest)
SCAN_LINES_MAX = 4            # measured scan lines kept (newest)
SCAN_EARLY = 2                # the first scans are always written
SCAN_INTERVAL = 86400         # afterwards: at most once a day, and only on a change

SITE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DIR_NAME = "site_handoffs"
SECTIONS = ("Güncel durum", "Bulgular ve çözümler", "Kullanıcı talimatları ve kararları", "Sonuçlar", "Değişiklik geçmişi")
OLD_HEADING = "Eski girişler (özet)"
SCAN_PREFIX = "- Tarama "

_lock = threading.RLock()
_META_RE = re.compile(r"^<!--\s*meta:\s*(\{.*\})\s*-->\s*$")

# secrets: the shared scrubber handles key/token/bearer/JWT text; these add cookies and URL query tokens
_EXTRA_SECRET = [
    re.compile(r"(?i)\b(set-cookie|cookie|authorization|proxy-authorization|x-api-key)\b(\s*[:=]\s*)[^\n]+"),
    re.compile(r"(?i)([?&;](?:[a-z_]*token|[a-z_]*key|sig|signature|expires?|auth|hash|session|sid|udys|cookie|code)=)[^&\s)\]\"']+"),
]


# --- helpers ----------------------------------------------------------------------------------------------------------

def valid_site(site: Any) -> bool:
    return isinstance(site, str) and bool(SITE_RE.match(site))


def _dir() -> str:
    return os.path.join(config.DATA_DIR, DIR_NAME)


def path(site: str) -> Optional[str]:
    return os.path.join(_dir(), site + ".md") if valid_site(site) else None


def _now_date(now: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() if now is None else now))


def _atomic_write(target: str, text: str) -> None:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _scrub_line(line: str, limit: int = 400) -> str:
    from . import pi_agent
    text = " ".join(str(line or "").split())
    for pattern in _EXTRA_SECRET:
        text = pattern.sub(lambda m: (m.group(1) + (m.group(2) if m.lastindex and m.lastindex >= 2 else "") + "***"), text)
    return pi_agent.scrub(text, limit)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:max(0, limit - 1)].rstrip() + "…"


def _scrub_block(text: Any, limit: int, line_limit: int = 300) -> str:
    """Multi-line text: every line scrubbed (secrets masked), a Markdown heading flattened (it would break the section parse),
    blank runs collapsed, the whole cut to ``limit`` characters."""
    out: list[str] = []
    for raw in str(text or "").replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)          # '## x' would start a section
        line = re.sub(r"^<!--.*-->$", "", line)          # the meta comment is ours
        if line:
            out.append(_scrub_line(line, line_limit))
    while out and out[-1] == "":
        out.pop()
    return _cut("\n".join(out), limit)


def clip_agent_text(text: Any, limit: int = NARRATIVE_MAX) -> str:
    """The text an agent hands in (``handoff``): scrubbed, headings flattened, cut to ``limit`` characters. Used when it is stored with a
    draft / repair record, so no secret reaches the disk twice."""
    return _scrub_block(text, limit)


def _bytes(text: str) -> int:
    return len(text.encode("utf-8"))


# --- model <-> markdown -----------------------------------------------------------------------------------------------

def _empty(site: str) -> dict:
    return {"site": site, "meta": {}, "state": [], "narrative": "", "user": [], "results": [], "scans": [], "changes": [], "old": []}


def _render(model: dict) -> str:
    site = model["site"]
    meta = {k: v for k, v in (model.get("meta") or {}).items() if v not in (None, "")}
    lines = [f"# {site} devir notu", "<!-- meta: " + json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + " -->", ""]
    lines += ["## Güncel durum", *(model["state"] or ["- (bilgi yok)"]), ""]
    lines += ["## Bulgular ve çözümler", model["narrative"] or "(ajan notu yok)", ""]
    lines += ["## Kullanıcı talimatları ve kararları", *(model["user"] or ["- (kayıt yok)"]), ""]
    lines += ["## Sonuçlar", *([*model["results"], *model["scans"]] or ["- (ölçüm yok)"]), ""]
    lines += ["## Değişiklik geçmişi"]
    if not model["changes"] and not model["old"]:
        lines.append("- (değişiklik yok)")
    for entry in model["changes"]:
        lines.append(f"### {entry['at']} · {entry['title']}")
        if entry.get("text"):
            lines.append(entry["text"])
    if model["old"]:
        lines.append(f"### {OLD_HEADING}")
        lines += model["old"]
    return "\n".join(lines).rstrip() + "\n"


def _parse(site: str, text: str) -> dict:
    model = _empty(site)
    section = ""
    sub: Optional[str] = None       # "old" or None while inside the change history
    entry: Optional[dict] = None
    narrative: list[str] = []
    for raw in (text or "").replace("\r", "").split("\n"):
        meta = _META_RE.match(raw)
        if meta:
            try:
                data = json.loads(meta.group(1))
                model["meta"] = data if isinstance(data, dict) else {}
            except ValueError:
                model["meta"] = {}
            continue
        if raw.startswith("## "):
            section = raw[3:].strip()
            sub, entry = None, None
            continue
        if raw.startswith("# "):
            continue
        if section == "Güncel durum":
            if raw.strip() and not raw.startswith("- (bilgi yok)"):
                model["state"].append(raw.rstrip())
        elif section == "Bulgular ve çözümler":
            narrative.append(raw.rstrip())
        elif section == "Kullanıcı talimatları ve kararları":
            if raw.strip() and not raw.startswith("- (kayıt yok)"):
                model["user"].append(raw.rstrip())
        elif section == "Sonuçlar":
            if raw.strip() and not raw.startswith("- (ölçüm yok)"):
                (model["scans"] if raw.startswith(SCAN_PREFIX) else model["results"]).append(raw.rstrip())
        elif section == "Değişiklik geçmişi":
            if raw.startswith("### "):
                head = raw[4:].strip()
                if head.startswith(OLD_HEADING):
                    sub, entry = "old", None
                else:
                    sub = None
                    at, _, title = head.partition(" · ")
                    entry = {"at": at.strip(), "title": title.strip(), "text": ""}
                    model["changes"].append(entry)
            elif sub == "old":
                if raw.strip():
                    model["old"].append(raw.rstrip())
            elif entry is not None:
                if raw.strip():
                    entry["text"] = (entry["text"] + "\n" + raw.rstrip()).strip("\n")
    body = "\n".join(narrative).strip()
    model["narrative"] = "" if body == "(ajan notu yok)" else body
    return model


def _summary_line(entry: dict) -> str:
    first = (entry.get("text") or "").split("\n", 1)[0]
    first = re.sub(r"^[-*]\s*", "", first)
    return "- " + _cut(f"{entry.get('at', '')} {entry.get('title', '')}: {first}".strip(), 150)


def _compact_history(model: dict) -> None:
    """At most HISTORY_MAX full entries (newest first); the older ones become a one-line summary, at most OLD_MAX of those."""
    while len(model["changes"]) > HISTORY_MAX:
        gone = model["changes"].pop()
        model["old"].insert(0, _summary_line(gone))
    del model["old"][OLD_MAX:]


def _fit(model: dict) -> str:
    """Render, then trim until the file fits TOTAL_MAX bytes: scan lines, history, old summaries, user lines, the narrative, results."""
    _compact_history(model)
    model["user"] = model["user"][-USER_MAX:]
    model["scans"] = model["scans"][-SCAN_LINES_MAX:]
    model["narrative"] = _cut(model["narrative"], NARRATIVE_MAX)
    text = _render(model)
    guard = 0
    while _bytes(text) > TOTAL_MAX and guard < 200:
        guard += 1
        if len(model["scans"]) > 1:
            model["scans"].pop(0)
        elif len(model["changes"]) > 3:
            gone = model["changes"].pop()
            model["old"].insert(0, _summary_line(gone))
            del model["old"][OLD_MAX:]
        elif model["old"]:
            model["old"].pop()
        elif len(model["user"]) > 4:
            model["user"].pop(0)
        elif len(model["narrative"]) > 600:
            model["narrative"] = _cut(model["narrative"], int(len(model["narrative"]) * 0.85))
        elif model["results"] and len(model["results"]) > 1:
            model["results"].pop()
        elif model["changes"] and len(model["changes"]) > 1:
            model["changes"].pop()
        elif model["state"] and len(model["state"]) > 3:
            model["state"].pop()
        else:
            break
        text = _render(model)
    if _bytes(text) > TOTAL_MAX:   # last resort: a hard cut on a character boundary
        text = text.encode("utf-8")[:TOTAL_MAX - 4].decode("utf-8", errors="ignore").rstrip() + "…\n"
    return text


def _load(site: str) -> dict:
    target = path(site)
    try:
        with open(target, "r", encoding="utf-8") as fh:  # type: ignore[arg-type]
            return _parse(site, fh.read())
    except (OSError, TypeError):
        return _empty(site)


def _save(model: dict) -> bool:
    target = path(model["site"])
    if not target:
        return False
    _atomic_write(target, _fit(model))
    return True


# --- facts ------------------------------------------------------------------------------------------------------------

def _state_lines(state: Any) -> list[str]:
    """``{label: value}`` (or ready lines) -> ``- label: value`` lines, scrubbed."""
    if isinstance(state, dict):
        return [f"- {_scrub_line(k, 40)}: {_scrub_line(v, 220)}" for k, v in state.items() if v not in (None, "", [], {})]
    return [("- " + _scrub_line(str(x).lstrip("- "), 260)) for x in (state or []) if str(x).strip()]


def _user_lines(lines: Any) -> list[str]:
    return [("- " + _scrub_line(str(x).lstrip("- "), 260)) for x in (lines or []) if str(x).strip()]


def state_facts(data: dict, version: Any = None, now: Optional[float] = None) -> dict:
    """The "Güncel durum" facts of a site yaml (``data``): version + date, playback, list, collections, players, search ..."""
    data = data if isinstance(data, dict) else {}
    cols = [str(c.get("id")) for c in (data.get("collections") or []) if isinstance(c, dict) and c.get("id")]
    resolvers = [str(r.get("type")) for r in (data.get("resolvers") or []) if isinstance(r, dict) and r.get("type")]
    providers = [str(p) for p in (data.get("providers") or [])] if isinstance(data.get("providers"), list) else []
    series = data.get("series_page") if isinstance(data.get("series_page"), dict) else {}
    facts: dict[str, Any] = {
        "Sürüm": f"v{version} · {_now_date(now)}" if version is not None else _now_date(now),
        "Oynatma": data.get("playback") or "video",
        "Liste": data.get("list_url") or "",
        "Tarama": f"fetch_mode {data.get('fetch_mode') or 'http'}, öğe sınırı {data.get('item_limit') or 'varsayılan'}",
        "Koleksiyonlar": ", ".join(cols[:8]) + (f" (+{len(cols) - 8})" if len(cols) > 8 else ""),
        "Oynatıcı": (", ".join(resolvers[:5]) if resolvers else "site modülü") + (" · sağlayıcı: " + ", ".join(providers[:5]) if providers else ""),
        "Dizi bölüm envanteri": "var" if series else "yok",
        "Arama": "var" if data.get("search") else "yok",
    }
    return facts


def _criteria_line(report: Any, force: bool = False) -> list[str]:
    criteria = (report or {}).get("criteria") if isinstance(report, dict) else None
    if not isinstance(criteria, dict) or not criteria:
        return []
    failed = [k for k, c in criteria.items() if isinstance(c, dict) and not c.get("ok")]
    ok = len(criteria) - len(failed)
    line = f"- Kriterler: {ok}/{len(criteria)} geçti"
    if failed:
        line += "; başarısız: " + ", ".join(failed[:6]) + (f" (+{len(failed) - 6})" if len(failed) > 6 else "")
    if force and failed:
        line += " (zorla kaydedildi)"
    return [_scrub_line(line, 300)]


def facts_from_draft(draft: Optional[dict], data: Optional[dict], version: Any = None, *, force: bool = False,
                     report: Optional[dict] = None, now: Optional[float] = None) -> dict:
    """The DETERMINISTIC parts of a note, built by the server from what the draft recorded (the agent writes none of this):

    * ``state``   - ``state_facts`` of the saved yaml
    * ``user``    - the admin's messages (``events`` kind ``user``: the answers of an ``ask_user`` question are paired with it), the
      first request (``hint``), the "Sitede yok, atla" fields (``skipped_fields``), at most 12 events
    * ``results`` - the criteria summary (passed / failed / forced), the provider recipes and the notes of the agent when it gave no handoff
    """
    draft = draft if isinstance(draft, dict) else {}
    report = report if isinstance(report, dict) else (draft.get("report") if isinstance(draft.get("report"), dict) else {})
    user: list[str] = []
    hint = str(draft.get("hint") or "").strip()
    if hint:
        user.append("İstek: " + _cut(hint, 200))
    pending: Optional[dict] = None
    rows: list[str] = []
    for ev in draft.get("events") or []:
        if not isinstance(ev, dict):
            continue
        kind = ev.get("kind")
        if kind == "ask":
            if pending:
                rows.append(f"Soru ({pending.get('field') or '?'}): {_cut(str(pending.get('text') or ''), 140)} -> cevap yok")
            pending = ev
        elif kind == "user":
            text = _cut(" ".join(str(ev.get("text") or "").split()), 160)
            if not text:
                continue
            if pending:
                rows.append(f"Soru ({pending.get('field') or '?'}): {_cut(str(pending.get('text') or ''), 120)} -> Cevap: {text}")
                pending = None
            else:
                rows.append("Talimat: " + text)
    if pending:
        rows.append(f"Soru ({pending.get('field') or '?'}): {_cut(str(pending.get('text') or ''), 140)} -> cevap yok")
    user += rows[-12:]
    skipped = [str(s) for s in (draft.get("skipped_fields") or []) if str(s).strip()]
    if skipped:
        user.append("Sitede yok, atla: " + ", ".join(skipped[:12]))
    results: list[str] = []
    results += _criteria_line(report, force)
    recipes = [str(r.get("name")) for r in (draft.get("provider_recipes") or []) if isinstance(r, dict) and r.get("name")]
    if recipes:
        results.append("- Bu kayıtla eklenen/güncellenen oynatıcı tarifi: " + ", ".join(recipes[:5]))
    out: dict[str, Any] = {"state": state_facts(data or {}, version, now), "user": user, "results": results}
    return out


# --- public API -------------------------------------------------------------------------------------------------------

def read(site: str) -> str:
    """The note as text ("" when there is none or the site id is not a safe one)."""
    target = path(site)
    if not target:
        return ""
    with _lock:
        try:
            with open(target, "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""


def write_initial(site: str, narrative: Optional[str], facts: Optional[dict] = None) -> bool:
    """Create (replace) the note of a freshly onboarded site: the agent's ``narrative`` (cut to NARRATIVE_MAX, scrubbed) + the
    deterministic ``facts`` ``{state, user, results}``. Scan counters of an older note are kept."""
    if not valid_site(site):
        return False
    facts = facts or {}
    with _lock:
        old = _load(site)
        model = _empty(site)
        model["meta"] = {k: v for k, v in (old.get("meta") or {}).items() if k in ("scans", "last_scan_at", "last_sig")}
        model["state"] = _state_lines(facts.get("state"))
        model["narrative"] = _scrub_block(narrative, NARRATIVE_MAX)
        model["user"] = _user_lines(facts.get("user"))[-USER_MAX:]
        model["results"] = _user_lines(facts.get("results"))
        model["scans"] = old.get("scans") or []
        model["changes"] = old.get("changes") or []
        model["old"] = old.get("old") or []
        return _save(model)


def append_change(site: str, text: str, facts: Optional[dict] = None, *, title: str = "Düzenleme", now: Optional[float] = None) -> bool:
    """Add one dated entry (<= CHANGE_MAX bytes of text, newest first) to the change history; ``facts`` (``state`` replaces, ``user`` lines are
    added, ``results`` replace the criteria lines) refresh the deterministic sections. Creates the note when there is none."""
    if not valid_site(site):
        return False
    facts = facts or {}
    with _lock:
        model = _load(site)
        body = _scrub_block(text, CHANGE_MAX)
        while _bytes(body) > CHANGE_MAX:
            body = _cut(body, len(body) - 20)
        model["changes"].insert(0, {"at": _now_date(now), "title": _scrub_line(title, 60) or "Düzenleme", "text": body})
        if facts.get("state"):
            model["state"] = _state_lines(facts["state"])
        if facts.get("user"):
            seen = set(model["user"])
            model["user"] += [x for x in _user_lines(facts["user"]) if x not in seen]
            model["user"] = model["user"][-USER_MAX:]
        if facts.get("results"):
            model["results"] = _user_lines(facts["results"])
        return _save(model)


def append_scan_findings(site: str, metrics: dict, *, now: Optional[float] = None) -> bool:
    """One measured line from a finished scan (``metrics``: see ``scan_metrics``). The first SCAN_EARLY scans are always written; later
    ones at most once a day and only when the measured picture changed. Returns True when a line was written."""
    if not valid_site(site) or not isinstance(metrics, dict):
        return False
    ts = time.time() if now is None else now
    with _lock:
        model = _load(site)
        meta = model["meta"]
        scans = int(meta.get("scans") or 0) + 1
        sig = _signature(metrics)
        write = scans <= SCAN_EARLY or (ts - float(meta.get("last_scan_at") or 0) >= SCAN_INTERVAL and sig != meta.get("last_sig"))
        meta["scans"] = scans
        if write:
            meta["last_scan_at"] = ts
            meta["last_sig"] = sig
            model["scans"].append(_scan_line(metrics, scans, ts))
        return _save(model) and write


def delete(site: str) -> bool:
    """Remove the note of a site (site deletion); False when there was none."""
    target = path(site)
    if not target:
        return False
    with _lock:
        try:
            os.unlink(target)
            return True
        except OSError:
            return False


def move(old: str, new: str) -> bool:
    """Carry the note to a new site id (the old file goes); False when there is no note or an id is not a safe one."""
    src, dst = path(old), path(new)
    if not src or not dst or src == dst:
        return False
    with _lock:
        model = _load(old)
        if not os.path.exists(src):
            return False
        model["site"] = new
        try:
            _save(model)
            os.unlink(src)
        except OSError:
            return False
        return True


# --- scan findings: measured, deterministic -----------------------------------------------------------------------------

def _pct(n: Any, d: Any) -> Optional[int]:
    try:
        return int(round(100.0 * float(n) / float(d))) if d else None
    except (TypeError, ValueError):
        return None


def _signature(m: dict) -> str:
    """What counts as "the picture changed": ratios rounded to 5 points, the counters of problems, the item count to 10 percent."""
    def five(v: Any) -> Any:
        return None if v is None else int(round(float(v) / 5.0)) * 5
    items = int(m.get("items") or 0)
    parts = [round(items / 10), *(five(m.get(k)) for k in ("poster", "overview", "sources", "tmdb", "year", "genres", "cast", "rating", "fallback")),
             m.get("crawl_errors"), m.get("crawl_skipped"), m.get("blocked")]
    return json.dumps(parts, separators=(",", ":"))


def _scan_line(m: dict, number: int, ts: float) -> str:
    def p(label: str, key: str) -> Optional[str]:
        return f"{label} %{m[key]}" if m.get(key) is not None else None
    detail = ", ".join(x for x in (p("yıl", "year"), p("tür", "genres"), p("oyuncu", "cast"), p("puan", "rating")) if x)
    parts = [f"{m.get('items', 0)} öğe",
             ", ".join(x for x in (p("posterli", "poster"), p("özetli", "overview"), p("kaynaklı", "sources"), p("TMDB", "tmdb")) if x)]
    if detail:
        parts.append("detay: " + detail)
    if m.get("crawl_series") is not None:
        parts.append(f"dizi envanteri: {m.get('crawl_series', 0)} dizi, hata {m.get('crawl_errors', 0)}, atlanan {m.get('crawl_skipped', 0)}")
    if m.get("blocked"):
        parts.append(f"engelli {m['blocked']}")
    if m.get("fallback") is not None:
        parts.append(f"yedek yoldan okunan dizi %{m['fallback']}")
    return _scrub_line(f"{SCAN_PREFIX}#{number} ({_now_date(ts)}): " + "; ".join(x for x in parts if x), 360)


def scan_metrics(site: str, result: Optional[dict] = None) -> dict:
    """The measurements of a finished scan, from records that already exist (the ingest ``result`` + the library tables; no network):
    ``items``, ``poster`` / ``overview`` / ``sources`` / ``tmdb`` (percent of the site's titles), the detail field fill ``year`` / ``genres``
    / ``cast`` / ``rating``, ``crawl_series`` / ``crawl_errors`` / ``crawl_skipped``, ``blocked`` (sum of the gate counters), ``fallback``
    (percent of series read by the link-scan fallback)."""
    from .. import db
    result = result if isinstance(result, dict) else {}
    out: dict[str, Any] = {"items": int(result.get("ingested") or result.get("scraped") or 0)}
    row = db.query_one(
        "SELECT COUNT(*) AS n, "
        "SUM(COALESCE(NULLIF(i.poster_url,''), NULLIF(i.tmdb_poster_url,'')) IS NOT NULL) AS poster, "
        "SUM(NULLIF(i.overview,'') IS NOT NULL) AS overview, "
        "SUM(i.tmdb_id IS NOT NULL) AS tmdb, "
        "SUM(i.year IS NOT NULL) AS year, "
        "SUM(NULLIF(i.genres,'') IS NOT NULL AND i.genres != '[]') AS genres, "
        "SUM(NULLIF(i.\"cast\",'') IS NOT NULL AND i.\"cast\" != '[]') AS cast, "
        "SUM(i.rating IS NOT NULL) AS rating, "
        "SUM(i.id IN (SELECT canonical_id FROM video_sources WHERE kind != 'trailer')) AS sources "
        "FROM library_items i WHERE i.id IN (SELECT DISTINCT canonical_id FROM source_items WHERE source=? AND canonical_id IS NOT NULL)",
        (site,))
    got = dict(row) if row is not None else {}
    total = int(got.get("n") or 0)
    if total:
        out["titles"] = total
        for key in ("poster", "overview", "tmdb", "year", "genres", "cast", "rating", "sources"):
            out[key] = _pct(got.get(key) or 0, total)
    crawl = result.get("series_crawl") if isinstance(result.get("series_crawl"), dict) else None
    if crawl and not crawl.get("disabled"):
        series = int(crawl.get("series") or 0)
        out["crawl_series"] = series
        out["crawl_errors"] = int(crawl.get("errors") or 0)
        out["crawl_skipped"] = int(crawl.get("skipped") or 0) + int(crawl.get("deferred") or 0)
        out["fallback"] = _pct(crawl.get("fallback") or 0, series)
    blocked = result.get("blocked")
    if isinstance(blocked, dict):
        out["blocked"] = sum(v for k, v in blocked.items() if isinstance(v, int) and not isinstance(v, bool) and k not in ("probed", "deferred"))
    return out


def _ensure_base(site: str) -> None:
    """A site without a note (hand-built, onboarded before notes existed) gets one with the current "Güncel durum" before its first scan line."""
    target = path(site)
    if not target or os.path.exists(target):
        return
    try:
        from . import config as scfg
        cfg = scfg.load_site(site)
        write_initial(site, "", {"state": state_facts(cfg.data, cfg.version)})
    except Exception:
        log.debug("handoff: no base note for %s", site, exc_info=True)


def record_scan(site: str, result: Optional[dict] = None, *, now: Optional[float] = None) -> bool:
    """The scan hook (``library.ingest.ingest_source`` after the run record): measure and append, never raise. Failed scans are skipped."""
    try:
        if not valid_site(site) or (isinstance(result, dict) and result.get("error")):
            return False
        _ensure_base(site)
        return append_scan_findings(site, scan_metrics(site, result), now=now)
    except Exception:
        log.warning("handoff: scan findings of %s not written", site, exc_info=True)
        return False
