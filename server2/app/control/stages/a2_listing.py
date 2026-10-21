"""A2 listing: list pages reached from the home menu (genre / category): row rule, pagination, the list of listings."""
from __future__ import annotations

import json
import time
from typing import Optional

from selectolax.parser import HTMLParser

from ...core.extract import blocks as x_blocks, cards, fingerprint, outline, urlpatterns
from ...core.extract._util import absolute
from ..sitemap import classify, labels
from . import common, hints, prompts_a1_a3, prompts_read
from .base import Finding, Measurement, Stage, StageCtx, judge, measurement

MAX_LISTINGS = 3


def _menu(ctx: StageCtx) -> list[dict]:
    """Title listings of the home menu as the A1 review labelled them (archive > type > genre > category); ``ignore`` and
    ``content: episode`` links never (an episode list is not a title listing)."""
    return labels.title_links(ctx.map)


def _home_paths(ctx: StageCtx) -> set[str]:
    from .a1_home import home_paths
    return home_paths(ctx)


def _confirmed(ctx: StageCtx) -> tuple[list[str], list[str]]:
    """``(archive/type lists, genre/category lists)`` the A1 review confirmed as title listings (same site, not the home
    page). Only these (and the admin's own samples) are ever listing samples."""
    base = ctx.base_url
    home = _home_paths(ctx)
    links = [l for l in _menu(ctx) if classify.same_site(common.site_url(base, l["url"]), base)
             and labels.path(common.site_url(base, l["url"])) not in home]
    top = [common.site_url(base, l["url"]) for l in links if l.get("kind") in ("archive", "type")]
    menu = [common.site_url(base, l["url"]) for l in links if l.get("kind") not in ("archive", "type")]
    return top, menu


def _confirmed_paths(ctx: StageCtx) -> set[str]:
    """Paths the admin confirmed as title listings: every A1-reviewed ``content: title`` link of ANY kind (archive / type /
    genre / category) and the admin's own samples; one of them with enough rows is a sufficient single sample."""
    base = ctx.base_url
    links = [l for l in _menu(ctx) if l.get("content") == "title" or l.get("manual") or labels.reviewed(ctx.map)]
    out = {labels.path(common.site_url(base, l["url"])) for l in links}
    return out | {labels.path(u) for u in ctx.pinned}


# --- blocks (one listing page, several headed blocks of the same rows) -----------------------------------------------------

def _title_heads(ctx: StageCtx) -> set[str]:
    """First path segments of the home ``target: title`` cards (what a title page address looks like before A3)."""
    from .a3_title import _home_cards
    try:
        titles, _eps = _home_cards(ctx)
    except Exception:
        return set()
    return {h for h in (x_blocks.head_of(u) for u in titles) if h}


def propose_blocks(found: list[dict], order: dict, title_heads: set) -> list[dict]:
    """``found`` blocks with ``proposed`` (series / movie / mixed / ignore) + ``reason``: the hint's ordinal words first
    ("ilk ... dizi, ikinci ... film"), then the heading words (kullanıcı/üye/user -> ignore), then the addresses (film /
    series addresses; a block whose address head is none of the title heads -> ignore)."""
    heads = set(title_heads) | {b.get("head") for b in found if b.get("kind") in ("film", "series") and b.get("head")}
    out = []
    for b in found:
        word = hints.block_word(b.get("heading") or "")
        if b["index"] in order:
            choice, reason = order[b["index"]], "ipucu sırası"
        elif word == "ignore":
            choice, reason = "ignore", "başlık sözcüğü"
        elif heads and b.get("kind") not in ("film", "series") and (b.get("head") or "") not in heads:
            choice, reason = "ignore", "adresler başlık sayfası adresine uymuyor"
        elif word:
            choice, reason = word, "başlık sözcüğü"
        elif b.get("kind") in ("film", "series"):
            choice, reason = ("movie" if b["kind"] == "film" else "series"), "adres"
        else:
            choice, reason = "mixed", ""
        out.append({**b, "proposed": choice, "reason": reason})
    return out


PAGINATION_OF = {"kind", "selector", "template", "attr"}


def _block_pagination(block: dict, paged: bool) -> Optional[dict]:
    """T30 item 8: on a page where a pagination link sits next to some block, each block gets its own rule (the one
    near it, else ``none``); a page without such a link keeps the listing's / site's rule (None)."""
    if not paged:
        return None
    own = block.get("pagination")
    return {k: v for k, v in own.items() if k in PAGINATION_OF} if isinstance(own, dict) else {"kind": "none"}


def home_proposal(said: Optional[dict]) -> Optional[str]:
    """T30: the LLM's ``home_target`` of a row -> the "Ana ekranda" dropdown's proposal."""
    from ..routing import home_targets
    return home_targets.propose((said or {}).get("home_target")) if (said or {}).get("home_target") else None


def _entry(url: str, block: dict, choice: str, paged: bool = False) -> dict:
    entry = {"url": labels.path(url), "index": block["index"], "selector": block.get("selector")}
    if block.get("row_selector"):   # T31: a block with its own row rule (blocks of different row shapes)
        entry.update(row_selector=block["row_selector"], fields=block.get("fields") or {})
    if block.get("heading"):
        entry["heading"] = block["heading"]
    pag = _block_pagination(block, paged)
    if pag:
        entry["pagination"] = pag
    return {k: v for k, v in labels.apply_block(entry, choice).items() if v not in (None, "")}


MIN_LIST_ROWS = 2   # T31: a list page where the site-wide row rule reads fewer rows gets its own rule


def page_specs(ctx: StageCtx, url: str, html: str) -> list[dict]:
    """T31: the title row rules of one list page (its outline's repeated card blocks, no episode lists, fields verified on
    the rows), page order: ``[{row_selector, fields, heading, count, link_kind}]``."""
    cache = ctx.__dict__.setdefault("_a2_specs", {})
    if url in cache:
        return cache[url]
    base = common.doc_base(html, url)
    o = outline.outline(html, base, url, url)
    tree = HTMLParser(html)
    out = []
    for block in o.get("blocks") or []:
        if block.get("link_kind") == "episode":
            continue
        try:
            nodes = tree.css(block["selector"])
        except Exception:
            continue
        got, _ev = cards.extract_fields(nodes, base)
        if not got or not got.get("url"):
            continue
        out.append({"row_selector": block["selector"], "heading": block.get("heading") or "",
                    "fields": {k: v for k, v in got.items() if k in ("url", "title", "poster", "year")},
                    "count": block.get("count") or len(nodes), "link_kind": block.get("link_kind")})
    cache[url] = out
    return out


def _card_link(ctx: StageCtx, url: str, html: str, link: Optional[str]) -> bool:
    """``link`` is the address of one of the page's title cards (a pager never is)."""
    if not link:
        return False
    base = common.doc_base(html, url)
    for spec in page_specs(ctx, url, html):
        if link in common.row_urls(common.rows_of(html, spec["row_selector"], {"url": spec["fields"]["url"]}), base):
            return True
    return False


def own_rule(ctx: StageCtx, url: str, html: str, site_rule: dict) -> Optional[dict]:
    """T31: the list page's own row rule when the site-wide one reads (almost) nothing there (a film archive whose cards
    differ from the series archive's): the page's paginated block, else its biggest one. ``None`` = the site rule reads it."""
    if len(common.rows_of(html, site_rule.get("row_selector"), site_rule.get("fields") or {})) >= MIN_LIST_ROWS:
        return None
    specs = page_specs(ctx, url, html)
    if not specs:
        return None
    multi = x_blocks.find_multi(html, specs, url) if len(specs) >= 2 else []
    best = next((b for b in multi if b.get("pagination")), None) or \
        (max(multi, key=lambda b: b.get("rows") or 0) if multi else max(specs, key=lambda b: b.get("count") or 0))
    return {"row_selector": best["row_selector"], "fields": best.get("fields") or {}}


def _multi_found(ctx: StageCtx, url: str, html: str) -> list[dict]:
    """Blocks of different row shapes (T31) with at least two distinct headings, else []."""
    specs = page_specs(ctx, url, html)
    found = x_blocks.find_multi(html, specs, url) if len(specs) >= 2 else []
    heads = {common.clip_text(b.get("heading") or "", 80).lower() for b in found if b.get("heading")}
    return found if len(heads) >= 2 else []


def block_plan(ctx: StageCtx, url: str, html: str, rule: dict) -> tuple[list[dict], list[dict]]:
    """``(found blocks with proposals, block entries)`` of one listing page: the rule's own entries for the page when it has
    them, else the proposals. ``([], [])`` for a page whose rows are one list. Blocks are the row rule's rows under
    several headings, else (T31) the page's card blocks of different row shapes."""
    cache = ctx.__dict__.setdefault("_a2_blocks", {})
    rule = labels.rule_for(rule, url)
    own_entries = labels.blocks_for(rule, url)
    multi = labels.multi_blocks(own_entries)
    key = (url, rule.get("row_selector"), json.dumps((rule.get("fields") or {}).get("url"), sort_keys=True), multi)
    if key not in cache:
        if multi:
            specs = [{"row_selector": e["row_selector"], "fields": e.get("fields") or rule.get("fields") or {},
                      "heading": e.get("heading") or ""} for e in sorted(own_entries, key=lambda e: e.get("index") or 0)
                     if e.get("row_selector")]
            specs = list({s["row_selector"]: s for s in reversed(specs)}.values())[::-1]
            found = x_blocks.find_multi(html, specs, url)
        else:
            found = x_blocks.find(html, rule.get("row_selector"), rule.get("fields") or {}, url) \
                or _multi_found(ctx, url, html)
        if found:
            order = hints.block_order([ctx.note] + list(reversed(ctx.hints or [])))
            found = propose_blocks(found, order, _title_heads(ctx))
        cache[key] = found
    found = cache[key]
    if not found:
        return [], []
    own = labels.blocks_for(rule, url)
    if own:
        return found, own
    paged = any(b.get("pagination") for b in found)
    return found, [_entry(url, b, b["proposed"], paged) for b in found]


def listing_rows(ctx: StageCtx, url: str, html: str, rule: dict) -> list[dict]:
    """The rows of the row rule, only from the chosen blocks when the page has several (``_type`` = the block's type)."""
    rule = labels.rule_for(rule, url)   # T31: the list's own row rule
    _found, entries = block_plan(ctx, url, html, rule)
    if not entries:
        return common.rows_of(html, rule.get("row_selector"), rule.get("fields") or {})
    try:
        return x_blocks.parse_rows(html, rule.get("row_selector"), rule.get("fields") or {}, entries)
    except Exception:
        return []


def _candidates(ctx: StageCtx, pattern: Optional[str] = None) -> list[str]:
    """The admin's samples first (never the home page), then the confirmed archive / type lists; the genre / category
    lists (biggest same-shape group) only when the site has no archive / type list. A handed-over address that is not one
    of these confirmed lists is never a sample. At most ``MAX_LISTINGS``."""
    base = ctx.base_url
    excluded = labels.excluded_paths(ctx.map) | _home_paths(ctx)   # episode lists / ignored links / the home page
    top, menu = _confirmed(ctx)
    if pattern:
        top = [u for u in top if classify.pattern_matches(pattern, u, base)]
        menu = [u for u in menu if classify.pattern_matches(pattern, u, base)] or ([] if top else menu)
    else:
        clusters = urlpatterns.cluster(menu, base)
        if clusters:
            menu = [u for u in menu if clusters[0].matches(u)]
    lists = top or menu
    known = {labels.path(u) for u in lists}
    out = [u for u in ctx.samples if classify.same_site(u, base) and ctx.allowed(u) and labels.path(u) not in excluded
           and (u in ctx.pinned or labels.path(u) in known)]
    for url in lists:
        if url not in out and ctx.allowed(url):
            out.append(url)
    return _both_types(ctx, out)[:MAX_LISTINGS]


def _both_types(ctx: StageCtx, urls: list[str]) -> list[str]:
    """T31: a film list and a series list (A1 ``title_type``) are both among the first ``MAX_LISTINGS`` when both exist."""
    typed = {labels.path(common.site_url(ctx.base_url, l["url"])): l.get("title_type") for l in _menu(ctx)}
    head = urls[:MAX_LISTINGS]
    for want in ("movie", "series"):
        if any(typed.get(labels.path(u)) == want for u in head):
            continue
        extra = next((u for u in urls[MAX_LISTINGS:] if typed.get(labels.path(u)) == want), None)
        if extra and head:
            drop = next((u for u in reversed(head) if typed.get(labels.path(u)) not in ("movie", "series")), head[-1])
            head = [u for u in head if u != drop] + [extra]
    return head + [u for u in urls if u not in head]


def _probe_next(ctx: StageCtx, url: str, html: str, rule: dict, rows: list) -> tuple[Optional[dict], str]:
    """A list on page 1 of a numbered address with no page-2 link: page 2 (``-02``, ``/page/2/``, ``?page=2``) is fetched;
    it exists and the row rule reads rows page 1 does not have -> that pagination rule. ``(rule | None, note)``."""
    guess = urlpatterns.first_page_rule(url)
    if guess is None:
        return None, ""
    page2 = ctx.get(guess["next_url"], "listing")
    if page2 is None:
        return None, f"2. sayfa yok ({common.path(guess['next_url'])})"
    first = set(common.row_urls(rows, common.doc_base(html, url)))
    rows2 = listing_rows(ctx, page2.final_url or guess["next_url"], page2.html, rule)
    new = set(common.row_urls(rows2, common.page_base(page2, guess["next_url"]))) - first
    if not new:
        return None, f"2. sayfa {common.path(guess['next_url'])}: yeni satır yok"
    return guess, f"2. sayfa {common.path(guess['next_url'])}: {len(new)} yeni satır"


class ListingStage(Stage):
    id = "A2"
    page_type = "listing"
    title = "Liste sayfaları"
    needs = ("A1",)

    def discover(self, ctx: StageCtx) -> Finding:
        urls = _candidates(ctx)
        evidence = {"tried": urls, "looked_for": "liste satırları (tekrar eden kart), sayfalama bağlantısı"}
        if not urls:
            evidence["summary"] = "ana sayfa menüsünde liste adresi yok"
            return Finding({}, evidence, 0.0)
        fields = row_selector = pagination = None
        found, first_page, pages = [], None, []
        own_pag: dict = {}   # T30: each listing page's own pagination (a single-page archive next to a paged one)
        for url in urls:
            page = ctx.get(url, "listing")
            if page is None:
                continue
            pages.append((url, page.html))
            base = common.page_base(page, url)
            o = outline.outline(page.html, base, url, page.final_url)
            tree = HTMLParser(page.html)
            blocks = [b for b in o.get("blocks") or [] if b.get("link_kind") != "episode"] or (o.get("blocks") or [])
            for block in sorted(blocks, key=lambda b: -(b.get("count") or 0)):
                try:
                    nodes = tree.css(block["selector"])
                except Exception:
                    continue
                got, ev = cards.extract_fields(nodes, base)
                found.append({"url": url, "selector": block["selector"], "rows": len(nodes), "fill": ev.get("fill"),
                              "problem": ev.get("problem")})
                if got and fields is None:
                    fields = {k: v for k, v in got.items() if k in ("url", "title", "poster", "year")}
                    row_selector = block["selector"]
                    first_page = (url, page.html)
                break
            rule = urlpatterns.pagination(page.html, base)
            if rule and _card_link(ctx, url, page.html, rule.get("next_url")):
                evidence.setdefault("pagination_rejected", []).append(f"{labels.path(url)}: {rule.get('evidence')}")
                rule = None   # T31: a "next" that is a title card's link (a show named "Next") is no pager
            own_pag[labels.path(url)] = {k: rule[k] for k in ("kind", "selector", "template") if rule.get(k)} \
                if rule else {"kind": "none"}
            if pagination is None and rule:
                pagination = dict(own_pag[labels.path(url)])
        if not fields:
            evidence.update({"found": found, "fetch_errors": ctx.errors[-3:], "summary": "liste satırı bulunamadı"})
            return Finding({}, evidence, 0.0)
        pattern_urls = list(urls)
        site_rule = {"row_selector": row_selector, "fields": fields}
        own: dict = {}   # T31: each list page the site-wide rows do not read gets its own row rule
        for url, html in pages:
            mine = None if first_page and url == first_page[0] else own_rule(ctx, url, html, site_rule)
            if mine:
                own[labels.path(url)] = mine
        if own:
            evidence["own_rows"] = {k: v["row_selector"] for k, v in own.items()}
        block_entries, block_found = [], []
        for url, html in pages:   # a page with several headed blocks of the same rows: proposals (the admin confirms)
            got, entries = block_plan(ctx, url, html, own.get(labels.path(url)) or site_rule)
            block_entries += entries
            block_found += [{"url": labels.path(url), **{k: b.get(k) for k in ("index", "heading", "rows", "kind",
                                                                                "proposed", "reason")}} for b in got]
        if block_found:
            evidence["blocks"] = block_found
        if pagination is None and first_page is not None:   # no page-2 link: a numbered page-1 address -> page 2 is tried
            rule = {"row_selector": row_selector, "fields": fields, "blocks": block_entries}
            rows = listing_rows(ctx, first_page[0], first_page[1], rule)
            guess, pnote = _probe_next(ctx, first_page[0], first_page[1], rule, rows)
            if pnote:
                evidence["pagination_probe"] = pnote
            if guess:
                pagination = {k: guess[k] for k in ("kind", "template")}
                own_pag[labels.path(first_page[0])] = dict(pagination)
                pattern_urls.append(guess["next_url"])
        if pagination and pagination.get("kind") == "page_param" and first_page is not None:
            nxt = urlpatterns.page_url_of(pagination, first_page[0], 2)
            if nxt and common.path(nxt).split("?")[0] != common.path(first_page[0]).split("?")[0]:
                pattern_urls.append(nxt)   # the list pattern covers its own page 2 (``-01`` -> ``-\d+``)
        clusters = urlpatterns.cluster(list(dict.fromkeys(pattern_urls)), ctx.base_url)
        listing_links = [{k: l[k] for k in ("name", "url", "kind", "title_type") if l.get(k)} for l in _menu(ctx)]
        default = pagination or {"kind": "none"}
        for link in listing_links:   # a listing whose pagination differs from the site-wide default carries its own
            rows_rule = own.get(labels.path(common.site_url(ctx.base_url, link["url"])))
            if rows_rule:   # T31: and its own row rule
                link.update(rows_rule)
            mine = own_pag.get(labels.path(common.site_url(ctx.base_url, link["url"])))
            if mine and mine.get("kind") != default.get("kind"):
                link["pagination"] = mine
        if len({p.get("kind") for p in own_pag.values()}) > 1:
            evidence["pagination_per_listing"] = {k: v.get("kind") for k, v in own_pag.items()}
        patch = {"url_pattern": common.union_regex([c.pattern_regex for c in clusters[:2]]) if clusters else None,
                 "row_selector": row_selector, "fields": fields, "pagination": pagination or {"kind": "none"},
                 "listings": listing_links[:40], "blocks": block_entries or None}
        patch = {k: v for k, v in patch.items() if v is not None}
        evidence.update({"found": found, "pagination": pagination or {"kind": "none"},
                         "summary": f"satır kuralı {row_selector}, sayfalama {(pagination or {}).get('kind', 'none')}"})
        return Finding({"pages": {"listing": patch}}, evidence, 0.7)

    def verify(self, ctx: StageCtx, patch: dict) -> Measurement:
        site_map = ctx.merged(patch)
        rule = (site_map.get("pages") or {}).get("listing") or {}
        urls = _candidates(ctx, rule.get("url_pattern"))
        samples, per, notes = [], [], []
        pagination_ok, page2_rows = None, False
        confirmed = _confirmed_paths(ctx)
        for url in urls:
            page = ctx.get(url, "listing")
            if page is None:
                samples.append({"url": url, "ok": False, "note": "getirilemedi"})
                continue
            base = common.page_base(page, url)
            rows = listing_rows(ctx, url, page.html, rule)
            matches = not rule.get("url_pattern") or classify.pattern_matches(rule["url_pattern"], url, ctx.base_url)
            entry = {"rows": len(rows), **{f"{f}_fill": common.fill(rows, f) for f in common.CARD_FIELDS}}
            if rows:
                per.append(entry)
            note = f"{len(rows)} satır" + ("" if matches else ", adres url_pattern'e uymuyor")
            own = labels.rule_for(rule, url) is not rule or labels.multi_blocks(labels.blocks_for(rule, url))
            if rows and (pagination_ok is None or own):   # T31: every list with its own rows / blocks is paged-checked too
                ok_p, pnote = self._pagination(ctx, rule, url, page.html, rows, base)
                if pagination_ok is None:
                    page2_rows = bool(ok_p) and labels.pagination_for(rule, url)[0].get("kind", "none") != "none"
                pagination_ok = ok_p if pagination_ok is None else (pagination_ok and ok_p)
                note += "; " + pnote
            kind = labels.list_type(rule, url)
            samples.append({"url": url, "ok": bool(rows) and matches, "note": note, **entry,
                            **({"type": kind} if kind else {}),
                            **({"confirmed": True} if labels.path(url) in confirmed else {})})
        values: dict = {"samples_count": len(per) + int(page2_rows)}   # a measured page 2 is the second sample
        if per:
            values["valid_count"] = min(p["rows"] for p in per[:2])
            for f in common.CARD_FIELDS:
                values[f"{f}_fill"] = min(p[f"{f}_fill"] for p in per[:2])
        if pagination_ok is not None:
            values["pagination_ok"] = int(pagination_ok)
        checks = judge(site_map, "listing", values)
        empty = [s for s in samples if s.get("rows") == 0 and s.get("confirmed")]
        checks.append({"name": "empty_listings", "value": len(empty), "bound": 0, "op": "max", "ok": not empty,
                       "optional": True})   # T31: a confirmed list the rules read nothing from (each list is measured)
        if empty:
            notes.append("satır okunamayan onaylı liste: " + ", ".join(common.path(s["url"]) for s in empty[:3]))
        self._one_archive_is_enough(checks, samples, notes)
        notes.append(f"{len(per)}/{len(urls)} liste örneğinde satır bulundu")
        notes += [f"{common.path(s['url'])}: {s['note']}" for s in samples[:3]]
        return measurement(checks, samples, notes)

    @staticmethod
    def _one_archive_is_enough(checks: list, samples: list, notes: list) -> None:
        """``samples_count``: >= 1 CONFIRMED list (an A1-confirmed ``content: title`` list of any kind, or the admin's own
        sample) whose rows pass the row threshold, or >= 2 discovered lists."""
        sc = next((c for c in checks if c["name"] == "samples_count"), None)
        rows = next((c for c in checks if c["name"] == "valid_count"), None)
        if sc is None or sc["ok"] or rows is None or not rows["ok"]:
            return
        good = [s for s in samples if s.get("ok") and s.get("confirmed")]
        if good:
            sc.update(ok=True, bound=1, rule=">=1 onaylı liste ya da >=2 keşfedilen")
            notes.append(f"onaylı liste {common.path(good[0]['url'])} ({good[0].get('rows')} satır): tek örnek yeterli")

    @staticmethod
    def _pagination(ctx: StageCtx, listing: dict, url: str, html: str, rows: list, base: str) -> tuple[bool, str]:
        """Page 2 address from the rule, and page 2 gives rows page 1 does not (or: rule ``none`` + no pager on page 1)."""
        rule, walked = labels.pagination_for(listing, url)
        kind = rule.get("kind") or "none"
        where = "" if walked is None else (f" (yalnız blok {', '.join(str(i) for i in sorted(walked))})" if walked
                                           else " (bloklar tek sayfa)")
        if kind == "none":
            if walked is not None:   # blocks with their own rule, none paginated: nothing to check on page 2
                return True, "tek sayfa" + where
            pager = urlpatterns.pagination(html, base)
            if pager is not None and not _card_link(ctx, url, html, pager.get("next_url")):
                return False, "sayfada sayfalama bağlantısı var ama kural 'none'"
            guess, pnote = _probe_next(ctx, url, html, listing, rows)
            if guess:
                return False, pnote + " ama kural 'none'"
            return True, "tek sayfa" + (f" ({pnote})" if pnote else "")
        nxt = urlpatterns.page_url_of(rule if kind in labels.NUMBERED else {**rule, "attr": "href"}, url, 2, html)
        if not nxt:
            return False, "2. sayfa adresi üretilemedi" + where
        page2 = ctx.get(nxt, "listing")
        if page2 is None:
            return False, f"2. sayfa getirilemedi ({common.path(nxt)})"
        if walked:   # only the paginated block(s): page 1 rows of those blocks vs page 2 rows of those blocks
            rows = [r for r in rows if r.get("_block") in walked] or rows
            entries = labels.walked_blocks(listing, url, walked)
            mine = labels.rule_for(listing, url)
            rows2 = x_blocks.parse_rows(page2.html, mine.get("row_selector"), mine.get("fields") or {}, entries)
        else:
            rows2 = listing_rows(ctx, page2.final_url or nxt, page2.html, listing)
        first = set(common.row_urls(rows, base))
        new = set(common.row_urls(rows2, common.page_base(page2, nxt))) - first
        return bool(new), f"2. sayfa {common.path(nxt)}: {len(new)} yeni satır" + where

    def meta_for(self, ctx: StageCtx, patch: dict, m: Measurement) -> dict:
        out = super().meta_for(ctx, patch, m)
        rule = (ctx.merged(patch).get("pages") or {}).get("listing") or {}
        pattern = rule.get("url_pattern")
        if pattern and out.get("samples"):
            out["samples"] = [u for u in out["samples"] if classify.pattern_matches(pattern, u, ctx.base_url)]
        if out.get("samples"):
            page = ctx.get(out["samples"][0], "listing")
            if page is not None:
                out["fingerprint"] = fingerprint.make(page.html, {"row_selector": rule.get("row_selector"),
                                                                  "fields": rule.get("fields") or {}})
        return out

    def handoff(self, ctx: StageCtx, patch: dict, m: Measurement) -> dict:
        """A2 -> A3: the listings that measured fine + their row links (title addresses); a page with blocks gives the rows of
        its chosen blocks only, interleaved (series + film samples), with their type (``types``: A3's type hint)."""
        rule = (ctx.merged(patch).get("pages") or {}).get("listing") or {}
        good = [s["url"] for s in m.samples if s.get("ok") and s.get("url")]
        queues: list[list[str]] = []
        types: dict[str, str] = {}
        for url in good[:MAX_LISTINGS]:   # T31: every measured list (a film archive's rows reach A3 too)
            page = ctx.get(url, "listing")
            if page is None:
                continue
            base = common.page_base(page, url)
            by_block: dict = {}
            kind = labels.list_type(rule, url)
            for row in listing_rows(ctx, url, page.html, rule):
                link = absolute(base, row.get("url"))
                if not link:
                    continue
                by_block.setdefault(row.get("_block"), []).append(link)
                typed = row.get("_type") if row.get("_type") in ("series", "movie") else kind
                if typed:
                    types.setdefault(link, typed)
            queues += list(by_block.values())
        rows: list[str] = []
        while any(queues):
            for q in queues:
                if q:
                    url = q.pop(0)
                    if url not in rows:
                        rows.append(url)
        out = {"urls": rows[:12], "listings": good[:MAX_LISTINGS]}
        kept = {u: types[u] for u in out["urls"] if u in types}
        if kept:
            out["types"] = kept
        return out

    def outcome(self, ctx: StageCtx, patch: dict, m: Measurement) -> Optional[dict]:
        """What the row rule finds, block labels left aside (they are confirmed on the block card): two rule sets that read
        the same rows are one result, the labels of the chosen one pre-fill the card."""
        rule = {k: v for k, v in (((patch or {}).get("pages") or {}).get("listing") or {}).items() if k != "blocks"}
        return super().outcome(ctx, {**(patch or {}), "pages": {**((patch or {}).get("pages") or {}), "listing": rule}}, m)

    def result_line(self, patch: Optional[dict]) -> str:
        rule = ((patch or {}).get("pages") or {}).get("listing") or {}
        line = (f"liste kalıbı {rule.get('url_pattern')}, satır {rule.get('row_selector')}, "
                f"sayfalama {(rule.get('pagination') or {}).get('kind', 'none')}, {len(rule.get('listings') or [])} liste")
        blocks = labels.block_lines(patch or {}, limit=2)
        return line + ("; bloklar " + "; ".join(b[2:] for b in blocks) if blocks else "")

    # --- review: a listing page with several blocks (A1-like card: what each block is) ------------------------------------

    def review(self, ctx: StageCtx, patch: dict, ask: bool) -> tuple:
        """Blocks of the measured listing pages -> ``listing.blocks`` (labels confirmed before carry over: same page, same
        heading / order); blocks never confirmed -> ``review`` items (only when ``ask``)."""
        rule = dict(((patch or {}).get("pages") or {}).get("listing") or {})
        if not rule.get("row_selector"):
            return patch, None
        prev = ctx.page("listing")
        was = bool(((ctx.map.get("meta") or {}).get("listing") or {}).get("reviewed"))
        entries = [b for b in rule.get("blocks") or [] if isinstance(b, dict)]
        items, measured = [], set()
        interp = list((ctx.reading or {}).get("interpretation") or [])
        from_llm = (ctx.reading or {}).get("source") == "llm"
        for url in _candidates(ctx, rule.get("url_pattern")):
            page = ctx.get(url, "listing")
            if page is None:
                continue
            found, own = block_plan(ctx, url, page.html, {**rule, "blocks": []})
            if not found:
                continue
            measured.add(labels.path(url))
            old = labels.blocks_for(prev, url) if was else []
            carried = x_blocks.match(found, old)
            mine = x_blocks.match(found, labels.blocks_for(rule, url))
            page_entries = []
            for b in found:
                label = carried.get(b["index"]) or mine.get(b["index"])
                choice = labels.block_choice(label) if label else b["proposed"]
                reason = "LLM önerisi" if b["index"] in mine and from_llm else b.get("reason") or ""
                said = prompts_read.match(interp, heading=b.get("heading"))
                if said is not None and b["index"] not in carried and not (b["index"] in mine and from_llm):
                    if not said.get("take"):   # T23: the LLM's reading pre-fills the dropdown (the admin still confirms)
                        choice, reason = "ignore", "LLM önerisi"
                    elif choice == "ignore":
                        word = hints.block_word(said.get("meaning") or "")
                        choice, reason = (word if word in ("series", "movie") else b["proposed"] if b["proposed"] != "ignore"
                                          else "mixed"), "LLM önerisi"
                page_entries.append(_entry(url, b, choice))
                if b["index"] not in carried:
                    entry = page_entries[-1]
                    item = {"url": labels.path(url), "index": b["index"], "heading": b.get("heading") or "",
                            "rows": b.get("rows"), "samples": b.get("samples") or [], "kind": b.get("kind"),
                            "head": b.get("head") or "", "proposed": choice, "reason": reason,
                            "paged": (entry.get("pagination") or {}).get("kind", "none") != "none"
                            if "pagination" in entry else bool(b.get("pagination"))}
                    if said is not None:
                        item["llm"] = prompts_read.card_note(said)
                        home = home_proposal(said)
                        if home:
                            item["home"] = home
                    items.append(item)
            entries = [e for e in entries if labels.path(e.get("url") or "") != labels.path(url)] + page_entries
        if entries:
            rule["blocks"] = entries
        else:
            rule.pop("blocks", None)
        patch = {**patch, "pages": {**patch["pages"], "listing": rule}}
        if not ask or not items:
            return patch, None
        return patch, {"blocks": items, "first": not was, "at": int(time.time()), **({"llm_read": True} if interp else {})}

    def apply_review(self, patch: dict, labelled: dict, site: dict) -> dict:
        """``labelled``: ``{blocks: {"<path>#<index>": series|movie|mixed|ignore}}`` (checked by ``actions.review``)."""
        rule = dict(((patch or {}).get("pages") or {}).get("listing") or {})
        entries = [dict(b) for b in rule.get("blocks") or [] if isinstance(b, dict)]
        keys = {f"{labels.path(b['url'])}#{b.get('index')}": i for i, b in enumerate(entries) if b.get("url")}
        for key, choice in (labelled.get("blocks") or {}).items():
            if key not in keys:
                raise ValueError(f"bilinmeyen blok: {key}")
            entries[keys[key]] = labels.apply_block(entries[keys[key]], choice)
        pages = labelled.get("block_pages") or {}
        for key, paged in pages.items():   # T30: "sayfalama: var/yok" of each block
            if key not in keys:
                raise ValueError(f"bilinmeyen blok: {key}")
            entry = entries[keys[key]]
            if not paged:
                entry["pagination"] = {"kind": "none"}
            elif (entry.get("pagination") or {}).get("kind", "none") == "none":
                own, _w = labels.pagination_for({**rule, "blocks": []}, entry["url"])
                entry["pagination"] = own if own.get("kind", "none") != "none" else {"kind": "next_link",
                                                                                     "selector": "a[rel=next]"}
        if pages:   # a page where one block got its own rule: its other blocks are first-page only
            for key, i in keys.items():
                page = key.rsplit("#", 1)[0]
                if any(k.rsplit("#", 1)[0] == page for k in pages) and "pagination" not in entries[i]:
                    entries[i]["pagination"] = {"kind": "none"}
        if entries:
            rule["blocks"] = entries
        return {**patch, "pages": {**patch["pages"], "listing": rule}}

    reads_first = True

    def read_pages(self, ctx: StageCtx, finding: Finding, last: Optional[Measurement] = None) -> list:
        return [p for p in (ctx.get(u, "listing") for u in _candidates(ctx)[:2]) if p is not None]

    def prompt(self, ctx: StageCtx, finding: Finding, hint: str, last: Optional[Measurement]) -> str:
        return prompts_a1_a3.full(self, ctx, finding, hint, last, self.read_pages(ctx, finding, last))
