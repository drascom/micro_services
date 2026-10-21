"""A1 home: the home page's card rows (sections), what each row opens (``title``/``episode``) and the menu links."""
from __future__ import annotations

import re
import time
from typing import Optional

from selectolax.parser import HTMLParser

from ...core.extract import cards, fingerprint, outline, urlpatterns
from ...core.extract._util import absolute, host_key
from ..sitemap import classify, labels
from . import common, hints, prompts_a1_a3, prompts_read
from .base import Finding, Measurement, Stage, StageCtx, judge, measurement

_KIND = {"episode": "episode", "series": "series", "film": "film"}
SMALL_SECTION = 3   # T31: a home row with at least this many cards (below the section_cards_min bound) only warns


def _targets(o: dict, base: str) -> tuple[dict, dict]:
    """``(target by section heading, nav listing links)`` from the outline: address clusters classified with
    ``urlpatterns.classify`` (episode cluster -> ``episode``, else ``title``)."""
    hrefs = [h for s in o.get("sections") or [] for h in s.get("sample_hrefs") or []]
    nav = [n for n in o.get("nav_links") or [] if n.get("region") != "footer"]
    clusters = urlpatterns.cluster(hrefs + [n.get("href") or "" for n in nav], base)
    classes = urlpatterns.classify(clusters, o)
    targets = {}
    for s in o.get("sections") or []:
        ep = sum(1 for h in s.get("sample_hrefs") or [] if any(c.matches(h) for c in classes["episode"]))
        if ep:
            targets[s["heading"]] = "episode"
        elif s.get("link_kind") == "episode":
            targets[s["heading"]] = "episode"
        else:
            targets[s["heading"]] = "title"
    links, seen = [], {"/"}
    episode_shapes = classes["episode"]

    def add(n: dict, meaning: str, kind: str = "") -> None:
        url = common.path(absolute(base, n["href"]))
        if url in seen or not classify.same_site(absolute(base, n["href"]), base):
            return
        seen.add(url)
        link = {"name": common.clip_text(n.get("text") or "", 60), "url": url}
        if meaning == "category" and kind:
            meaning = kind
        links.append(labels.apply_meaning(link, meaning))

    for listing in classes["listing"]:
        prefix = common.prefix_of(listing.pattern_regex)
        kind = "genre" if prefix else "category"
        for n in nav:
            if n.get("href") and listing.matches(n["href"]):
                add(n, _guess(n, episode_shapes) or kind)
    for n in nav:   # every other menu link: offered in the review (proposal from its words, else "ignore")
        if n.get("href"):
            add(n, _guess(n, episode_shapes) or "ignore")
    return targets, {"links": links[:40]}


_EPISODE_WORDS = re.compile(r"b[öo]l[üu]m|episode", re.I)
_ARCHIVE_WORDS = re.compile(r"ar[şs]iv|archive|t[üu]m[üu]?\s|all\s", re.I)
_FILM_WORDS = re.compile(r"film|movie", re.I)


def _guess(n: dict, episode_shapes: list) -> str:
    """LLM-free meaning proposal of a menu link from its words / address shape ("" = no opinion)."""
    text = f"{n.get('text') or ''} "
    if _EPISODE_WORDS.search(text) or any(c.matches(n.get("href") or "") for c in episode_shapes):
        return "episode_list"
    if _ARCHIVE_WORDS.search(text):
        return "film_list" if _FILM_WORDS.search(text) else "series_list"
    return ""


def redirect_path(ctx: StageCtx, url: str, page=None) -> Optional[str]:
    """The path the home address redirects to on the SAME site (``/`` -> ``/tr2/``), ``None`` when it does not."""
    page = page if page is not None else ctx.get(url, "home")
    final = getattr(page, "final_url", None) if page is not None else None
    if not final or not classify.same_site(final, ctx.base_url):
        return None
    to = common.path(final)
    return to if to != common.path(url) else None


def home_paths(ctx: StageCtx) -> set[str]:
    """Paths that ARE the site's home page: ``/``, the map's ``pages.home.url`` and where ``/`` redirects to."""
    out = {"/", labels.path(common.site_url(ctx.base_url, ctx.page("home").get("url") or "/"))}
    to = redirect_path(ctx, common.site_url(ctx.base_url, "/"))
    if to:
        out.add(to)
    return out


REDIRECT_NOTE = "Ana sayfa {to} adresine yönleniyor; bu adres kullanılıyor"


def note_listings(ctx: StageCtx) -> list[dict]:
    """List / archive addresses of the site note (``{url: path, name, meaning}``), on the site's own host only."""
    out = []
    for item in hints.note_items(ctx.note)["listings"]:
        full = common.site_url(ctx.base_url, item["url"])
        if classify.same_site(full, ctx.base_url):
            out.append({**item, "url": labels.path(full)})
    return out


def llm_single(ctx: StageCtx, heading, urls: list, base: str) -> bool:
    """The LLM's first read took the section with this heading (one meaning) and one generalised address pattern
    (``urlpatterns.merged_pattern``) covers every card address of it."""
    from . import prompts_read
    item = prompts_read.match(list((ctx.reading or {}).get("interpretation") or []), heading=heading)
    own = [u for u in urls if host_key(u) == host_key(base)]
    if not item or not item.get("take") or not own or len(own) != len(urls):
        return False
    pattern = urlpatterns.merged_pattern(own, base)
    return bool(pattern) and all(classify.pattern_matches(pattern, u, base) for u in own)


def card_target(urls: list[str]) -> Optional[str]:
    """T31: what the card ADDRESSES say a section opens: ``episode`` (>= 80% episode addresses), ``title`` (>= 80% film /
    series addresses and none an episode), else None (no opinion)."""
    kinds = [outline.link_kind(u) for u in urls[:20] if u]
    if not kinds:
        return None
    if kinds.count("episode") * 5 >= len(kinds) * 4:
        return "episode"
    if "episode" not in kinds and sum(1 for k in kinds if k in ("film", "series")) * 5 >= len(kinds) * 4:
        return "title"
    return None


def fix_targets(ctx: StageCtx, home: dict) -> tuple[dict, list[str]]:
    """T31: a section whose ``target`` contradicts its card addresses (an LLM patch said ``episode`` for title cards such
    as ``/dizi/<slug>-izle-3``) gets the target its addresses show. ``(home, notes)``."""
    page = ctx.get(common.site_url(ctx.base_url, home.get("url") or "/"), "home") if home.get("sections") else None
    if page is None:
        return home, []
    base = common.page_base(page, page.url or ctx.base_url)
    out, notes = [], []
    for sec in home.get("sections") or []:
        if isinstance(sec, dict) and not sec.get("ignore") and sec.get("target", "title") in ("title", "episode"):
            urls = common.row_urls(common.rows_of(page.html, sec.get("row_selector"), sec.get("fields") or {}), base)
            said = card_target(urls)
            if said and said != sec.get("target", "title"):
                notes.append(f"{sec.get('title')}: hedef {sec.get('target', 'title')} -> {said} (kart adresleri)")
                sec = {**sec, "target": said}
        out.append(sec)
    return {**home, "sections": out}, notes


def home_proposal(said):
    from .a2_listing import home_proposal as hp
    return hp(said)


class HomeStage(Stage):
    id = "A1"
    page_type = "home"
    title = "Ana sayfa"
    needs = ()

    def home_url(self, ctx: StageCtx) -> str:
        return common.site_url(ctx.base_url, ctx.page("home").get("url") or "/")

    def discover(self, ctx: StageCtx) -> Finding:
        url = self.home_url(ctx)
        evidence = {"tried": [url], "looked_for": "tekrar eden kart blokları (bölüm başlığı + kartlar), menü bağlantıları"}
        page = ctx.get(url, "home")
        if page is None:
            evidence["fetch_errors"] = ctx.errors[-3:]
            evidence["summary"] = "ana sayfa getirilemedi"
            return Finding({}, evidence, 0.0)
        base = common.page_base(page, url)
        moved = redirect_path(ctx, url, page)
        if moved:   # the home page redirects inside the site: that address is the home page from now on
            url = common.site_url(ctx.base_url, moved)
            evidence["home_redirect"] = moved
            evidence["hint_note"] = REDIRECT_NOTE.format(to=moved)
        o = outline.outline(page.html, base, url, page.final_url)
        tree = HTMLParser(page.html)
        targets, nav = _targets(o, base)
        own = {"/", common.path(url)}
        nav["links"] = [l for l in nav["links"] if labels.path(l["url"]) not in own]   # the home page is not a menu list
        sections, found, missing = [], [], []
        for i, sec in enumerate(o.get("sections") or []):
            try:
                nodes = tree.css(sec["selector"])
            except Exception:
                nodes = []
            fields, ev = cards.extract_fields(nodes, base, _KIND.get(sec.get("link_kind") or "", ""))
            if not fields:
                missing.append({"heading": sec["heading"], "problem": ev.get("problem")})
                continue
            fields = {k: v for k, v in fields.items() if k in ("url", "title", "poster", "year")}
            sections.append({"id": f"s{len(sections) + 1}", "title": sec["heading"], "row_selector": sec["selector"],
                             "target": targets.get(sec["heading"], "title"), "fields": fields})
            found.append({"heading": sec["heading"], "selector": sec["selector"], "cards": sec.get("count"),
                          "matches": sec.get("selector_matches", sec.get("count")), "fill": ev.get("fill")})
        page_patch: dict = {"url": common.path(url) if host_key(url) == host_key(ctx.base_url) else "/", "sections": sections}
        if nav["links"]:
            page_patch["nav"] = nav
        evidence.update({"found": found, "missing": missing, "nav_links": len(nav["links"]),
                         "summary": f"{len(sections)} bölüm, {len(nav['links'])} menü bağlantısı bulundu"})
        listed = note_listings(ctx)
        if listed:
            evidence["note_used"] = [f"liste adresi {l['url']} ({labels.MEANING_TR[l['meaning']]}; onay kartında önerilir)"
                                     for l in listed]
        return Finding({"pages": {"home": page_patch}}, evidence, 0.8 if sections else 0.0)

    def verify(self, ctx: StageCtx, patch: dict) -> Measurement:
        site_map = ctx.merged(patch)
        home = (site_map.get("pages") or {}).get("home") or {}
        url = common.site_url(ctx.base_url, home.get("url") or "/")
        page = ctx.get(url, "home")
        if page is None:
            checks = judge(site_map, "home", {})
            return measurement(checks, [{"url": url, "ok": False, "note": "getirilemedi"}], ["ana sayfa getirilemedi"])
        base = common.page_base(page, url)
        per, notes = [], []
        for sec in home.get("sections") or []:
            rows = common.rows_of(page.html, sec.get("row_selector"), sec.get("fields") or {})
            urls = common.row_urls(rows, base)
            single = common.one_cluster(urls, base)
            kinds = {outline.link_kind(u) for u in urls}
            if not single and len(kinds) == 1 and kinds != {"other"}:
                single = True   # T31: one address KIND (all series / film / episode): slug variants are not mixed shapes
            if not single and llm_single(ctx, sec.get("title"), urls, base):
                single = True   # the LLM read this section as ONE kind: a mixed address shape is generalised, not refused
                notes.append(f"{sec.get('title')}: adres biçimleri karışık ama LLM tek tür okudu (genel kalıp)")
            per.append({"title": sec.get("title"), "cards": len(rows),
                        **{f"{f}_fill": common.fill(rows, f) for f in common.CARD_FIELDS},
                        "one_cluster": single})
        expect_bound = lambda name, default: next((c["bound"] for c in judge(site_map, "home", {}) if c["name"] == name),
                                                  default)
        min_cards = expect_bound("section_cards_min", 4)
        values = {"sections_valid_count": 0}
        if per:
            values.update({"section_cards_min": min(p["cards"] for p in per),
                           "target_one_cluster": int(all(p["one_cluster"] for p in per))})
            for f in common.CARD_FIELDS:
                values[f"{f}_fill"] = min(p[f"{f}_fill"] for p in per)
            values["sections_valid_count"] = sum(1 for p in per if p["cards"] >= min_cards and p["one_cluster"]
                                                 and p["url_fill"] >= 0.95 and p["title_fill"] >= 0.95)
        checks = judge(site_map, "home", values)
        small = [p for p in per if p["cards"] < min_cards]
        cmin = next((c for c in checks if c["name"] == "section_cards_min"), None)
        if cmin is not None and not cmin["ok"] and small and values["sections_valid_count"] >= 1 \
                and all(p["cards"] >= SMALL_SECTION for p in small):
            # T31: a short row (a 4-card slider / trend strip) only warns: dropping it to pass lost the rows the site note
            # routes (slider, trend); a section with fewer than SMALL_SECTION cards still fails
            cmin.update(optional=True)
            notes.append("kısa bölüm (uyarı): " + ", ".join(f"{p['title']} {p['cards']} kart" for p in small[:4]))
        notes.append(f"{len(per)} bölüm bulundu" + (": " + ", ".join(f"{p['title']} {p['cards']} kart" for p in per[:6])
                                                    if per else ""))
        no_poster = sum(1 for p in per if p["poster_fill"] < 0.8)
        if no_poster:
            notes.append(f"{no_poster}/{len(per)} bölümde afiş eksik")
        mixed = [p["title"] for p in per if not p["one_cluster"]]
        if mixed:
            notes.append("kart adresleri tek kalıpta değil: " + ", ".join(str(t) for t in mixed[:4]))
        ok_page = bool(per) and all(p["cards"] > 0 for p in per)
        sample = {"url": url, "ok": ok_page, "note": "; ".join(notes)[:300], "sections": per}
        return measurement(checks, [sample], notes)

    def meta_for(self, ctx: StageCtx, patch: dict, m: Measurement) -> dict:
        out = super().meta_for(ctx, patch, m)
        home = (ctx.merged(patch).get("pages") or {}).get("home") or {}
        first = next(iter(home.get("sections") or []), None)
        url = common.site_url(ctx.base_url, home.get("url") or "/")
        page = ctx.get(url, "home") if first else None
        if page is not None:
            out["fingerprint"] = fingerprint.make(page.html, {"row_selector": first["row_selector"], "fields": first["fields"]})
        return out

    def handoff(self, ctx: StageCtx, patch: dict, m: Measurement) -> dict:
        """A1 -> A2: the confirmed title listings (labels) + the home section cards by target."""
        from ..sitemap import labels
        site_map = ctx.merged(patch)
        home = (site_map.get("pages") or {}).get("home") or {}
        base = ctx.base_url
        out = {"urls": [common.site_url(base, l["url"]) for l in labels.title_links(site_map)][:10],
               "title_cards": [], "episode_cards": []}
        page = ctx.get(common.site_url(base, home.get("url") or "/"), "home") if home.get("sections") else None
        if page is not None:
            for sec in home.get("sections") or []:
                if sec.get("ignore"):
                    continue
                key = "title_cards" if sec.get("target", "title") == "title" else "episode_cards"
                rows = common.rows_of(page.html, sec.get("row_selector"), sec.get("fields") or {})
                out[key] += common.row_urls(rows, common.page_base(page, base))[:5]
        return out

    def result_line(self, patch: Optional[dict]) -> str:
        home = ((patch or {}).get("pages") or {}).get("home") or {}
        secs = home.get("sections") or []
        names = ", ".join(f"{s.get('title')}->{labels.section_choice(s)}" for s in secs[:6])
        links = len((home.get("nav") or {}).get("links") or [])
        return f"{len(secs)} bölüm ({names}); {links} menü bağlantısı"

    # --- review (T17): the admin labels what the sections / menu links are ----------------------------------------------

    def review(self, ctx: StageCtx, patch: dict, ask: bool) -> tuple:
        """Labels confirmed before carry over; sections / links never labelled -> ``review`` items (only when ``ask``)."""
        home = ((patch or {}).get("pages") or {}).get("home")
        if not home:
            return patch, None
        if (home.get("url") or "/") == "/":   # an LLM patch keeps "/" while the site redirects: the target is the home
            moved = redirect_path(ctx, common.site_url(ctx.base_url, "/"))
            if moved:
                home = {**home, "url": moved}
                patch = {**patch, "pages": {**patch["pages"], "home": home}}
        home, _fixed = fix_targets(ctx, home)   # T31: the card addresses overrule a wrong title/episode target
        patch = {**patch, "pages": {**patch["pages"], "home": home}}
        merged, new_secs, new_links = labels.merge_previous(home, ctx.page("home"), labels.reviewed(ctx.map))
        patch = {**patch, "pages": {**patch["pages"], "home": merged}}
        # site note: its list addresses are proposals (a menu link it names takes the note's meaning; a missing one is
        # pre-filled as "adres ekle"); the admin still confirms
        noted = {l["url"]: l for l in note_listings(ctx)} if ask else {}
        known = {labels.path(l["url"]) for l in (merged.get("nav") or {}).get("links") or [] if l.get("url")}
        suggested = [{"url": u, "name": l["name"], "meaning": l["meaning"], "from_note": True,
                      "content": labels.apply_meaning({"url": u}, l["meaning"]).get("content")}
                     for u, l in noted.items() if u not in known]
        if not ask or not (new_secs or new_links or suggested):
            return patch, None
        page = ctx.get(common.site_url(ctx.base_url, merged.get("url") or "/"), "home")
        base = common.page_base(page, page.url) if page is not None else ctx.base_url
        interp = list((ctx.reading or {}).get("interpretation") or [])
        from_llm = (ctx.reading or {}).get("source") == "llm"
        sections = []
        for sec in merged.get("sections") or []:
            if sec.get("id") not in new_secs:
                continue
            rows = common.rows_of(page.html, sec.get("row_selector"), sec.get("fields") or {}) if page is not None else []
            item = {"id": sec.get("id"), "title": sec.get("title"), "cards": len(rows),
                    "samples": common.row_urls(rows, base)[:3], "proposed": labels.section_choice(sec)}
            said = prompts_read.match(interp, heading=sec.get("title"))
            if said is not None:   # T23: the LLM's reading of the section (and its proposal when the system's rules won)
                item["llm"] = prompts_read.card_note(said)
                home = home_proposal(said)   # T30: "Ana ekranda" proposal
                if home:
                    item["home"] = home
                if not from_llm and not said.get("take"):
                    item["proposed"] = "ignore"
            sections.append(item)
        links = []
        for l in (merged.get("nav") or {}).get("links") or []:
            key = labels.path(l["url"])
            if key not in new_links:
                continue
            item = {"url": l["url"], "name": l.get("name") or "", "proposed": labels.meaning_of(l)}
            said = prompts_read.match(interp, href=l["url"])
            if said is not None:
                item["llm"] = prompts_read.card_note(said)
                if not from_llm and not said.get("take"):
                    item["proposed"] = "ignore"
                home = home_proposal(said)
                if home:
                    item["home"] = home
            if key in noted:
                item.update(proposed=noted[key]["meaning"], from_note=True)
            links.append(item)
        return patch, {"sections": sections, "links": links, "suggested": suggested, "first": not labels.reviewed(ctx.map),
                       "at": int(time.time()), **({"llm_read": True} if interp else {})}

    def apply_review(self, patch: dict, labelled: dict, site: dict) -> dict:
        """``labelled``: ``{sections: {id: title|episode|ignore}, links: {path: meaning}, added: [{name, url, meaning}]}``
        (addresses already checked by ``actions.review``)."""
        home = dict(((patch or {}).get("pages") or {}).get("home") or {})
        secs = {s.get("id"): s for s in home.get("sections") or []}
        unknown = [k for k in (labelled.get("sections") or {}) if k not in secs]
        if unknown:
            raise ValueError(f"bilinmeyen bölüm: {unknown[0]}")
        home["sections"] = [labels.apply_section(s, labelled["sections"][s.get("id")])
                            if s.get("id") in (labelled.get("sections") or {}) else s for s in home.get("sections") or []]
        links = list((home.get("nav") or {}).get("links") or [])
        by_path = {labels.path(l["url"]): i for i, l in enumerate(links)}
        for key, meaning in (labelled.get("links") or {}).items():
            if key not in by_path:
                raise ValueError(f"bilinmeyen bağlantı: {key}")
            links[by_path[key]] = labels.apply_meaning(links[by_path[key]], meaning)
        for item in labelled.get("added") or []:
            link = labels.apply_meaning({"name": item.get("name") or item["url"], "url": item["url"], "manual": True},
                                        item["meaning"])
            if labels.path(item["url"]) in by_path:
                links[by_path[labels.path(item["url"])]] = link
            else:
                by_path[labels.path(item["url"])] = len(links)
                links.append(link)
        if links:
            home["nav"] = {**(home.get("nav") or {}), "links": links}
        return {**patch, "pages": {**patch["pages"], "home": home}}

    reads_first = True

    def read_pages(self, ctx: StageCtx, finding: Finding, last: Optional[Measurement] = None) -> list:
        page = ctx.get(self.home_url(ctx), "home")
        return [page] if page else []

    def prompt(self, ctx: StageCtx, finding: Finding, hint: str, last: Optional[Measurement]) -> str:
        return prompts_a1_a3.full(self, ctx, finding, hint, last, self.read_pages(ctx, finding, last))
