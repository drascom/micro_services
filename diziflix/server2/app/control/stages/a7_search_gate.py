"""A7 search + gate (optional): the site's own search (form on the home page -> a ``pages.search`` rule, measured with a
title the catalogue already has) and the "not public" gate (``gate: {require, probe, blocked}``): ``blocked`` rules are
proposed from the placeholder pages A5/A6 met and must catch them without catching a good sample. A site without a
search form passes with the search part skipped (a note says so)."""
from __future__ import annotations

import json
import re
from typing import Optional

from selectolax.parser import HTMLParser

from ... import db
from ...core.extract._util import fold as _fold
from ...core.extract import cards, outline, search as x_search
from ...core.play import blocked as play_blocked
from ..sitemap import classify
from . import common, hints, playside, prompts_a4_a7, search_run, state
from .base import Finding, Measurement, Stage, StageCtx, judge, measurement

DEFAULT_GATE = {"require": "player", "probe": 2}


def _known_title(ctx: StageCtx) -> Optional[dict]:
    """``{title, key}`` of a title the catalogue has from this site (K0), else of an A3 sample page."""
    samples = list(((ctx.map.get("meta") or {}).get("title") or {}).get("samples") or [])
    handed = list((ctx.handoff or {}).get("title_urls") or []) if (ctx.handoff or {}).get("from") == "A6" else []
    samples = ctx.pool(list(ctx.pinned) + handed + samples)   # T25: the admin's own first; excluded ones never
    rows = db.query("SELECT key, raw, source_url FROM source_items WHERE site_id=? ORDER BY fetched_at, key LIMIT 200",
                    (ctx.site_id,))
    rows = sorted(rows, key=lambda r: samples.index(r["source_url"]) if r["source_url"] in samples else len(samples))
    for row in rows[:1]:
        try:
            title = (json.loads(row["raw"] or "{}") or {}).get("title")
        except ValueError:
            title = None
        if title:
            return {"title": str(title), "key": row["key"]}
    rule = ctx.page("title")
    for url in samples:
        page = ctx.get(url, "title")
        if page is None:
            continue
        from ...core.extract import parse
        title = parse.parse_detail(page.html, {"title": (rule.get("fields") or {}).get("title") or {"selector": "h1"}}).get("title")
        if title:
            return {"title": str(title), "key": classify.title_key(ctx.map, url)}
    return None


def _placeholders(ctx: StageCtx) -> list[str]:
    urls = []
    for sid in ("A5", "A6"):
        ev = (state.get(ctx.site_id, sid) or {}).get("evidence") or {}
        urls += [p["url"] for p in ev.get("placeholders") or [] if p.get("url")]
    return list(dict.fromkeys(urls))


def _good_samples(ctx: StageCtx) -> list[str]:
    """Pages that play: A6's handoff first, then ``meta.player.samples``."""
    handed = list((ctx.handoff or {}).get("urls") or []) if (ctx.handoff or {}).get("from") == "A6" else []
    meta = list(((ctx.map.get("meta") or {}).get("player") or {}).get("samples") or [])
    return ctx.pool(handed + meta)[:5]


def _rule_of(marker: dict) -> Optional[dict]:
    reason = f"yer tutucu ({marker.get('word') or 'engelli'})"
    if marker.get("kind") == "iframe" and marker.get("src"):
        src = str(marker["src"])
        word = marker.get("word") or ""
        idx = src.lower().find(word) if word else -1
        piece = src[max(0, idx - 20): idx + len(word) + 20] if idx >= 0 else src[-40:]
        return {"on": "episode_page", "iframe_src_regex": re.escape(piece), "reason": reason}
    if marker.get("selector"):
        return {"on": "episode_page", "selector": marker["selector"], "reason": reason}
    return None


def _result_rule(html: str, url: str, title_pattern: str, base: str) -> Optional[dict]:
    """``{row_selector, fields}`` of the result rows: the first repeating card block whose links are title pages."""
    o = outline.outline(html, url)
    tree = HTMLParser(html or "")
    for block in o.get("blocks") or []:
        selector = block.get("selector")
        if not selector:
            continue
        hrefs = block.get("sample_hrefs") or ([block["sample_link"]] if block.get("sample_link") else [])
        if title_pattern and hrefs and not any(classify.pattern_matches(title_pattern, h, base) for h in hrefs):
            continue
        try:
            nodes = tree.css(selector)
        except Exception:
            continue
        fields, _ev = cards.extract_fields(nodes, url)
        if fields and fields.get("url") and fields.get("title"):
            return {"row_selector": selector,
                    "fields": {k: v for k, v in fields.items() if k in ("url", "title", "poster", "year")}}
    return None


class SearchGateStage(Stage):
    id = "A7"
    page_type = "search"
    title = "Arama ve kapı"
    needs = ("A6",)
    consumes_handoff = False
    optional = True
    extra_keys = ("gate",)

    def discover(self, ctx: StageCtx) -> Finding:
        base = ctx.base_url
        evidence: dict = {"tried": [], "looked_for": "arama formu + sonuç satırları; yer tutucu sayfayı yakalayan kapı kuralı"}
        patch: dict = {"pages": {}}
        known = _known_title(ctx)
        evidence["known_title"] = known
        home = ctx.get(common.site_url(base, ctx.page("home").get("url") or "/"), "home")
        form = x_search.search_form(home.html, home.final_url or base) if home is not None else None
        rule = x_search.rule_from_form(form) if form else None
        noted = hints.note_items(ctx.note)["search"] if ctx.note else None
        if noted:   # the site note's search address is the first candidate (the form's rule is the fallback)
            evidence["note_used"] = [f"arama adresi {noted}"]
            note_rule = {"url": noted, "method": "GET"}
            if known:
                url = search_run.request_url(note_rule, base.rstrip("/"), known["title"])
                evidence["tried"].append(url)
                page = ctx.get(url, "search")
                found = _result_rule(page.html, page.final_url or url, ctx.page("title").get("url_pattern") or "", base) \
                    if page is not None else None
                if found:
                    rule, form = note_rule, form or {"from": "site notu"}
                    patch["pages"]["search"] = {**note_rule, **found}
                else:
                    evidence["note_used"] = [f"arama adresi {noted}: sonuç satırı bulunamadı, form denendi"]
            elif not rule:
                rule = note_rule
        if patch["pages"].get("search"):
            evidence["search"] = {"form": form, "rule": patch["pages"]["search"], "query": known["title"], "from": "site notu"}
        elif rule and known:
            url = search_run.request_url(rule, base.rstrip("/"), known["title"])
            evidence["tried"].append(url)
            page = ctx.get(url, "search")
            found = _result_rule(page.html, page.final_url or url, ctx.page("title").get("url_pattern") or "", base) \
                if page is not None else None
            if found:
                patch["pages"]["search"] = {**rule, **found}
            evidence["search"] = {"form": form, "rule": patch["pages"].get("search"), "query": known["title"]}
        else:
            evidence["search"] = {"form": form, "skipped": "arama formu yok" if not form else "bilinen başlık yok"}
        blocked, placeholders = [], []
        for url in _placeholders(ctx):
            page = ctx.get(url, "player")
            marker = playside.placeholder_marker(page.html) if page is not None else None
            if marker:
                placeholders.append({"url": url, **marker})
                rule_ = _rule_of(marker)
                if rule_ and rule_ not in blocked:
                    blocked.append(rule_)
        gate = {**DEFAULT_GATE, **({"blocked": blocked[:play_blocked.MAX_RULES]} if blocked else {})}
        patch["gate"] = gate
        evidence.update({"placeholders": placeholders, "samples": [], "found": {"gate": gate},
                         "summary": ("arama kuralı bulundu" if patch["pages"].get("search") else
                                     f"arama atlandı ({evidence['search'].get('skipped') or 'sonuç satırı bulunamadı'})")
                                    + f"; {len(blocked)} kapı kuralı"})
        if not patch["pages"]:
            patch.pop("pages")
        return Finding(patch, evidence, 0.8)

    def verify(self, ctx: StageCtx, patch: dict) -> Measurement:
        site_map = ctx.merged(patch)
        base = ctx.base_url
        spec = (patch.get("pages") or {}).get("search")
        samples, notes = [], []
        values: dict = {}
        if spec:
            known = _known_title(ctx) or {}
            query = known.get("title") or ""

            def fetch(url, method, data):
                if method != "GET":
                    raise search_run.SearchError("aşama ölçümü yalnız GET arar")
                return ctx.get(url, "search")
            try:
                hits = search_run.run_spec(spec, base.rstrip("/"), query, fetch)
                error = ""
            except search_run.SearchError as exc:
                hits, error = [], str(exc)
            want = _fold(query)
            found = any((known.get("key") and classify.title_key(site_map, h["url"]) == known["key"])
                        or _fold(h["title"]) == want for h in hits)
            values["search_ok"] = int(bool(hits) and found)
            samples.append({"url": search_run.request_url(spec, base.rstrip("/"), query) if query else "", "ok": bool(found),
                            "note": error or f"{len(hits)} sonuç, bilinen başlık {'bulundu' if found else 'yok'}"})
            notes.append(f"arama: {len(hits)} sonuç" + (f" ({error})" if error else ""))
        else:
            values["search_ok"] = 1
            notes.append("arama yok: alt bölüm atlandı")
        rules = play_blocked.rules_of((site_map.get("gate") or {}).get("blocked"))
        bad = _placeholders(ctx)
        good = _good_samples(ctx)
        caught = 0
        for url in bad:
            page = ctx.get(url, "player")
            hit = play_blocked.match(page.html, rules, "episode_page") if page is not None else None
            caught += bool(hit)
            samples.append({"url": url, "ok": bool(hit), "note": "yer tutucu " + ("yakalandı" if hit else "yakalanmadı")})
        false = 0
        for url in good:
            page = ctx.get(url, "player")
            hit = play_blocked.match(page.html, rules, "episode_page") if page is not None else None
            false += bool(hit)
            if hit:
                samples.append({"url": url, "ok": False, "note": "sağlam örnek yanlışlıkla engelli sayıldı"})
        values["gate_catches"] = round(caught / len(bad), 3) if bad else 1.0
        values["gate_false"] = false
        notes.append(f"kapı: {len(rules)} kural, {caught}/{len(bad)} yer tutucu yakalandı, {false} yanlış")
        return measurement(judge(site_map, "search", values), samples, notes)

    def on_passed(self, ctx: StageCtx, patch: dict, m: Measurement) -> None:
        """New gate rules: the playback preparation runs again (blocked titles leave the client lists)."""
        from ..sitemap import store as map_store
        from ..crawl import plan as crawl_plan, run as crawl_run   # T11: titles are judged again by the crawl
        site = map_store.get(ctx.site_id) or {}
        if site.get("status") == "active" and ((patch.get("gate") or {}).get("blocked")) and \
                state.state_of(ctx.site_id, "A6") in state.PASSED and crawl_plan.rules_changed(ctx.site_id, regate=True):
            crawl_run.kick(ctx.site_id)

    def meta_for(self, ctx: StageCtx, patch: dict, m: Measurement) -> dict:
        return {}

    def result_line(self, patch: Optional[dict]) -> str:
        search = ((patch or {}).get("pages") or {}).get("search")
        blocked = ((patch or {}).get("gate") or {}).get("blocked") or []
        return f"arama {'var: ' + str(search.get('url')) if search else 'yok'}; kapı {len(blocked)} kural"

    def prompt(self, ctx: StageCtx, finding: Finding, hint: str, last: Optional[Measurement]) -> str:
        urls = [u for u in (finding.evidence or {}).get("tried") or []] + _placeholders(ctx)
        pages = [p for p in (ctx.get(u, "page") for u in urls[:2]) if p is not None]
        return prompts_a4_a7.full(self, ctx, finding, hint, last, pages)
