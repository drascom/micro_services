"""A3 title: the title (series / film) page: address pattern, key rule, type rule and detail fields."""
from __future__ import annotations

import functools
import re
from collections import Counter
from typing import Optional

from selectolax.parser import HTMLParser

from urllib.parse import urlsplit

from ...core.extract import cards, detail, fingerprint, outline, parse, urlpatterns
from ...core.extract._util import absolute
from ..sitemap import classify
from . import common, hints, prompts_a1_a3
from .base import Finding, Measurement, Stage, StageCtx, judge, measurement

MIN_SAMPLES = 3
MAX_SAMPLES = 5
DETAIL_FIELDS = ("title", "poster", "year", "genres", "overview", "rating", "cast", "trailer")
_SEASON_LINK = re.compile(r"(?:sezon|season)", re.I)


def series_signal(html: str, url: str) -> Optional[str]:
    """``series`` when the page lists episodes / seasons; ``None`` = no signal (a film, or episodes elsewhere)."""
    return _series_signal(html or "", url)


@functools.lru_cache(maxsize=64)   # the same sample page is judged by discovery and by every measurement
def _series_signal(html: str, url: str) -> Optional[str]:
    o = outline.outline(html, url)
    if o.get("episode_links"):
        return "series"
    tree = HTMLParser(html or "")
    seasons = [a for a in tree.css("a[href]") if _SEASON_LINK.search(a.attributes.get("href") or "")]
    return "series" if len(seasons) >= 1 else None


def _home_cards(ctx: StageCtx) -> tuple[list[str], list[str]]:
    """``(title card addresses, episode card addresses)`` of the home sections (``target: title`` / ``target: episode``;
    ``ignore`` never)."""
    base = ctx.base_url
    home = ctx.page("home")
    titles: list[str] = []
    episodes: list[str] = []
    if not home.get("sections"):
        return titles, episodes
    page = ctx.get(common.site_url(base, home.get("url") or "/"), "home")
    if page is None:
        return titles, episodes
    for sec in home["sections"]:
        if sec.get("ignore"):
            continue
        bucket = titles if sec.get("target", "title") == "title" else episodes
        for row in common.rows_of(page.html, sec.get("row_selector"), sec.get("fields") or {}):
            url = absolute(common.page_base(page, base), row.get("url"))
            if url and url not in bucket:
                bucket.append(url)
    return titles, episodes


def listing_urls(ctx: StageCtx) -> list[str]:
    """Addresses of the confirmed title listings (A1 review ``content: title`` links + ``listing.listings``) and of the A2
    listing samples: list pages, never title samples themselves."""
    from ..sitemap import labels
    base = ctx.base_url
    out = [common.site_url(base, l["url"]) for l in labels.listing_items(ctx.map)]
    out += list(((ctx.map.get("meta") or {}).get("listing") or {}).get("samples") or [])
    out += [common.site_url(base, l["url"]) for l in labels.nav_links(ctx.map)]
    return list(dict.fromkeys(out))


def listing_rows(ctx: StageCtx, url: str) -> list[str]:
    """Row links of one list page by the A2 rule (or, before A2, its biggest non-episode card block)."""
    page = ctx.get(url, "listing")
    if page is None:
        return []
    base = common.page_base(page, url)
    rule = ctx.page("listing")
    if rule.get("row_selector"):
        return common.row_urls(common.rows_of(page.html, rule["row_selector"], rule.get("fields") or {}), base)
    o = outline.outline(page.html, base, url, page.final_url)
    blocks = [b for b in o.get("blocks") or [] if b.get("link_kind") != "episode"]
    tree = HTMLParser(page.html)
    for block in sorted(blocks, key=lambda b: -(b.get("count") or 0)):
        try:
            nodes = tree.css(block["selector"])
        except Exception:
            continue
        got, _ev = cards.extract_fields(nodes, base)
        if got.get("url"):
            return common.row_urls(common.rows_of(page.html, block["selector"], {"url": got["url"]}), base)
    return []


class Picker:
    """A3 sample filter (T18): title samples come only from title listing rows and ``target: title`` home cards; list pages
    (confirmed listings, the A2 ``url_pattern``) and the episode card address cluster are never title samples."""

    def __init__(self, ctx: StageCtx):
        self.ctx = ctx
        base = ctx.base_url
        self.titles, episodes = _home_cards(ctx)
        self.lists = {common.path(u).rstrip("/") for u in listing_urls(ctx)}
        self.list_pattern = ctx.page("listing").get("url_pattern")
        self.episode_rx = [c.pattern_regex for c in urlpatterns.cluster(episodes, base)] if episodes else []

    def ok(self, url: str) -> bool:
        base = self.ctx.base_url
        if not url or not classify.same_site(url, base) or not self.ctx.allowed(url):
            return False
        path = common.path(url)
        if path.rstrip("/") in self.lists or path.rstrip("/") == "":
            return False
        if self.list_pattern and classify.pattern_matches(self.list_pattern, url, base):
            return False
        if outline.link_kind(url) == "episode" or any(re.search(rx, urlsplit(url).path or "/") for rx in self.episode_rx):
            return False
        return True

    def candidates(self) -> list[str]:
        """The home title cards interleaved with the handoff (A2 listing rows; read here when there is no handoff)."""
        ctx = self.ctx
        handed = [u for u in ctx.samples if u not in ctx.pinned]
        rows: list[str] = []
        if not handed:
            for lurl in [u for u in listing_urls(ctx) if self._is_title_list(u)][:2]:
                rows += listing_rows(ctx, lurl)
        out = []
        sources = [list(self.titles), list(handed + rows)]
        while any(sources):   # interleaved: both sources reach the samples
            for src in sources:
                if src:
                    url = src.pop(0)
                    if url not in out and self.ok(url):
                        out.append(url)
        return out

    def _is_title_list(self, url: str) -> bool:
        from ..sitemap import labels
        title_paths = {labels.path(common.site_url(self.ctx.base_url, l["url"])).rstrip("/")
                       for l in labels.listing_items(self.ctx.map)}
        meta = {common.path(u).rstrip("/") for u in
                ((self.ctx.map.get("meta") or {}).get("listing") or {}).get("samples") or []}
        return common.path(url).rstrip("/") in title_paths | meta

    def pick(self, extra: tuple = ()) -> list[str]:
        """The admin's own samples first (not filtered: the admin decided), then the filtered candidates by shape."""
        pinned = [u for u in self.ctx.pinned if classify.same_site(u, self.ctx.base_url) and self.ctx.allowed(u)]
        cands = [u for u in list(extra) + self.candidates() if u not in pinned and self.ok(u)]
        return _both_types(pinned, _pick(cands, self.ctx.base_url), cands,
                           (self.ctx.handoff or {}).get("types") or {})[:MAX_SAMPLES]


def _both_types(pinned: list[str], picked: list[str], cands: list[str], types: dict) -> list[str]:
    """T31: a site with films AND series (the A2 handoff's ``types``; a film address need not look different) gives A3 at
    least one sample of each: a missing type's first candidate replaces the last picked one of the other type."""
    out = list(pinned) + list(picked)
    for want in ("movie", "series"):
        if any(types.get(u) == want for u in out[:MAX_SAMPLES]):
            continue
        extra = next((u for u in cands if types.get(u) == want and u not in out), None)
        if extra is None:
            continue
        head = out[:MAX_SAMPLES]
        kept = [types.get(u) for u in head]
        drop = next((u for u in reversed(head) if u not in pinned and types.get(u) != want
                     and (types.get(u) is None or kept.count(types.get(u)) > 1)), None)
        if drop is not None:
            head[head.index(drop)] = extra
        else:
            head.append(extra)
        out = head + [u for u in out if u not in head]
    return out


def _pick(urls: list[str], base: str) -> list[str]:
    """Round robin over address shapes (film and series mixed), at most ``MAX_SAMPLES``."""
    groups: dict[str, list[str]] = {}
    for url in urls:   # group by the address without its last segment (``/dizi/``, ``/film/`` ...)
        groups.setdefault(common.path(url).rstrip("/").rsplit("/", 1)[0], []).append(url)
    queues = list(groups.values())
    picked: list[str] = []
    while any(queues) and len(picked) < MAX_SAMPLES:
        for q in queues:
            if q and len(picked) < MAX_SAMPLES:
                url = q.pop(0)
                if url not in picked:
                    picked.append(url)
    return picked


class TitleStage(Stage):
    id = "A3"
    page_type = "title"
    title = "Başlık sayfası"
    needs = ("A2",)
    never_skip = ("url", "title", "key")

    def discover(self, ctx: StageCtx) -> Finding:
        base = ctx.base_url
        picker = Picker(ctx)
        picked = picker.pick()
        cards = [(u, outline.link_kind(u)) for u in picker.candidates()]
        evidence = {"tried": picked, "looked_for": "başlık, afiş, yıl, tür, özet; adres kalıbı, anahtar, tür (dizi/film)"}
        pages = [(u, ctx.get(u, "title")) for u in picked]
        pages = [(u, p) for u, p in pages if p is not None]
        if not pages:
            evidence.update({"fetch_errors": ctx.errors[-3:], "summary": "başlık sayfası getirilemedi"})
            return Finding({}, evidence, 0.0)
        proposals: dict[str, list] = {}
        tried: dict[str, list] = {}
        hinted = hints.candidates(ctx.hints, DETAIL_FIELDS)
        noted = hints.candidates([ctx.note], DETAIL_FIELDS, pages=("title",)) if ctx.note else {}
        for name, specs in hinted.items():   # T18: selectors from the admin's hint are measured FIRST (win ties)
            proposals[name] = list(specs)
        for name, specs in noted.items():    # then the site note's (same page type only)
            for spec in specs:
                if spec not in proposals.setdefault(name, []):
                    proposals[name].append(spec)
        for url, page in pages:
            fields, ev = detail.detail_fields(page.html, page.final_url or url)
            for name, spec in fields.items():
                if name in DETAIL_FIELDS and spec not in proposals.setdefault(name, []):
                    proposals[name].append(spec)
            for name, what in (ev.get("_tried") or {}).items():
                tried.setdefault(name, what)
        fields = {}
        for name, specs in proposals.items():
            best, score = None, 0
            for spec in specs:
                got = sum(1 for _, p in pages if common.filled(name, parse.parse_detail(p.html, {name: spec}).get(name)))
                if got > score:
                    best, score = spec, got
            if best is not None:
                fields[name] = best
        if hinted:
            used = [n for n in hinted if any(fields.get(n) == spec for spec in hinted[n])]
            evidence["hint_note"] = (f"İpucundan {len(hints.selectors_of(hinted))} seçici denendi: "
                                     + ", ".join(hints.selectors_of(hinted))
                                     + (f"; seçilen alan: {', '.join(used)}" if used else "; keşif daha iyisini buldu"))[:500]
        note_used = []
        if noted:
            won = [n for n in noted if any(fields.get(n) == spec for spec in noted[n])]
            note_used.append(f"{len(hints.selectors_of(noted))} seçici denendi ({', '.join(hints.selectors_of(noted))})"
                             + (f", seçilen alan: {', '.join(won)}" if won else ", keşif daha iyisini buldu"))
        urls = [u for u, _ in pages]
        signals = {u: series_signal(p.html, u) for u, p in pages}
        kinds = dict(cards)
        for url, kind in ((ctx.handoff or {}).get("types") or {}).items():   # A2 blocks: the confirmed block's title type
            if kinds.get(url) in (None, "other", "mixed") and kind in ("series", "movie"):
                kinds[url] = "series" if kind == "series" else "film"
        prefixes = common.path_prefixes([u for u, _ in cards] + urls)
        if prefixes:
            url_pattern, key = common.prefix_pattern(prefixes), common.prefix_key(prefixes)
        else:
            head = urlpatterns.head_pattern(urls)   # T18: a constant head (/diziler/) never leaks into the key
            if head:
                url_pattern, key = head, urlpatterns.key_rule(urls)
            else:
                regexes = [c.pattern_regex for c in urlpatterns.cluster([u for u, _ in cards] + urls, base)
                           if any(c.matches(u) for u in urls)]
                url_pattern, key = (common.union_regex(regexes) if regexes else None), common.key_rule(regexes)
        shape = hints.note_items(ctx.note)["title"] if ctx.note else None
        if shape:   # the note's title address shape (/diziler/<slug>/): taken when every sample fits it
            if all(classify.pattern_matches(shape["url_pattern"], u, base) for u in urls):
                url_pattern, key = shape["url_pattern"], shape["key"]
                note_used.append(f"adres kalıbı {shape['example']} -> {url_pattern} (örneklere uydu)")
            else:
                note_used.append(f"adres kalıbı {shape['example']} örneklere uymadı; keşfin kalıbı kullanıldı")
        if note_used:
            evidence["note_used"] = note_used
        patch = {"url_pattern": url_pattern, "key": key, "type": self._type_rule(prefixes or [], signals, kinds),
                 "fields": fields}
        patch = {k: v for k, v in patch.items() if v is not None}
        missing = [n for n in ("title", "poster", "year", "genres", "overview") if n not in fields]
        evidence.update({"found": {n: fields[n] for n in fields}, "missing": {n: tried.get(n) for n in missing},
                         "samples": urls, "type_signals": signals,
                         "summary": f"{len(pages)} başlık sayfası, alanlar: {', '.join(fields) or '-'}"})
        return Finding({"pages": {"title": patch}}, evidence, 0.7)

    @staticmethod
    def _type_rule(prefixes: list[str], signals: dict, kinds: dict) -> dict:
        """``from: url`` with ``map`` = last head segment -> type (votes: the page lists episodes / seasons, or the card
        opened a series / film address); ``default`` = the majority."""
        votes: Counter = Counter()
        mapping: dict[str, str] = {}
        for prefix in prefixes:
            kind_votes: Counter = Counter()
            for url in set(signals) | set(kinds):
                if not common.path(url).startswith("/" + prefix):
                    continue
                signal, kind = signals.get(url), kinds.get(url)
                if signal == "series" or kind == "series":
                    kind_votes["series"] += 1
                elif kind == "film" or url in signals:
                    kind_votes["movie"] += 1
            if kind_votes:
                mapping[prefix.rstrip("/").split("/")[-1]] = kind_votes.most_common(1)[0][0]
                votes.update(kind_votes)
        if not prefixes:
            votes.update("series" if s == "series" or kinds.get(u) == "series" else "movie" for u, s in signals.items())
        default = votes.most_common(1)[0][0] if votes else "series"
        return {"from": "url", "map": mapping, "default": default}

    def _samples(self, ctx: StageCtx, rule: dict) -> list[str]:
        picker = Picker(ctx)
        meta = ((ctx.map.get("meta") or {}).get("title") or {}).get("samples") or []
        pinned = [u for u in ctx.pinned if classify.same_site(u, ctx.base_url) and ctx.allowed(u)]
        urls = list(pinned)
        rest = [u for u in dict.fromkeys(picker.pick() + list(meta) + list(ctx.samples)) if u not in urls and picker.ok(u)]
        urls += _pick(rest, ctx.base_url)   # handoff / discovery samples, filtered, mixed by address shape (film + series)
        if len(urls) < MIN_SAMPLES:   # excluded ones left too few: more from the listing rows / title cards
            urls += [u for u in picker.pick() if u not in urls]
        return urls[:MAX_SAMPLES]

    def verify(self, ctx: StageCtx, patch: dict) -> Measurement:
        site_map = ctx.merged(patch)
        rule = (site_map.get("pages") or {}).get("title") or {}
        base = ctx.base_url
        samples, keys, rows, types_ok = [], [], [], []
        fallback = mismatch = 0
        for url in self._samples(ctx, rule):
            page = ctx.get(url, "title")
            if page is None:
                samples.append({"url": url, "ok": False, "note": "getirilemedi"})
                continue
            values = parse.parse_detail(page.html, rule.get("fields") or {})
            pats = classify.patterns_of(rule)
            pattern_ok = not pats or any(classify.pattern_matches(p, url, base) for p in pats)
            key = classify.key_from(rule.get("key"), url, base)
            how = ""
            if not key:   # T30: the address does not fit the key regex -> fallback key (title is never dropped)
                key = classify.fallback_key(site_map, url, page.html)
                how = "yedek anahtar" if key else ""
                fallback += bool(key)
            mismatch += not pattern_ok
            kind = classify.title_type(site_map, url, values)
            signal = series_signal(page.html, url)
            type_ok = kind in ("series", "movie") and (signal is None or kind == signal)
            rows.append(values)
            keys.append(key)
            types_ok.append(type_ok)
            missing = [f for f in ("title", "poster", "year") if not common.filled(f, values.get(f))]
            note = f"anahtar {key or '-'}" + (f" ({how})" if how else "") + f", tür {kind or '-'}" + \
                (f", eksik: {', '.join(missing)}" if missing else "") + ("" if pattern_ok else ", adres kalıba uymuyor (uyarı)")
            samples.append({"url": url, "ok": bool(key) and common.filled("title", values.get("title")) and type_ok,
                            "note": note, "key": key, "type": kind, **({"signal": signal} if signal else {})})
        n = len(rows)
        values = {"samples_count": n}
        if n:
            real = [k for k in keys if k]
            values.update({
                "key_ok_ratio": round(len(real) / n, 3),
                "duplicate_key_ratio": round((len(real) - len(set(real))) / n, 3),
                "type_ok": round(sum(types_ok) / n, 3),
                **{f"{f}_fill": common.fill(rows, f) for f in ("title", "poster", "year", "genres", "overview")},
            })
        extra = None
        if n:   # T30 warnings: fallback keys / addresses outside the url patterns (never a failure)
            values["fallback_key_count"], values["url_mismatch_count"] = fallback, mismatch
            extra = {"fallback_key_count": {"bound": 0, "op": "max", "optional": True},
                     "url_mismatch_count": {"bound": 0, "op": "max", "optional": True}}
        checks = judge(site_map, "title", values, extra=extra)
        notes = [f"{n} başlık sayfası ölçüldü"]
        if fallback:
            notes.append(f"{fallback} örnekte anahtar kalıbı tutmadı; yedek anahtar kullanıldı (son yol parçası / canonical / id)")
        if mismatch:
            notes.append(f"{mismatch} örneğin adresi url kalıbına uymuyor (uyarı; tür bağlantının geldiği yerden)")
        for f in ("poster", "year", "genres", "overview"):
            miss = sum(1 for r in rows if not common.filled(f, r.get(f)))
            if n and miss:
                notes.append(f"{miss}/{n} örnekte {f} yok")
        if n and values["duplicate_key_ratio"] > 0:
            notes.append("aynı anahtarı veren örnekler var")
        return measurement(checks, samples, notes)

    def meta_for(self, ctx: StageCtx, patch: dict, m: Measurement) -> dict:
        out = super().meta_for(ctx, patch, m)
        rule = (ctx.merged(patch).get("pages") or {}).get("title") or {}
        pats = classify.patterns_of(rule)
        if pats and out.get("samples"):
            out["samples"] = [u for u in out["samples"] if any(classify.pattern_matches(p, u, ctx.base_url) for p in pats)]
        if out.get("samples"):
            page = ctx.get(out["samples"][0], "title")
            if page is not None:
                out["fingerprint"] = fingerprint.make(page.html, {"fields": rule.get("fields") or {}})
        return out

    def handoff(self, ctx: StageCtx, patch: dict, m: Measurement) -> dict:
        """A3 -> A4: the verified title samples + the title address pattern / key."""
        rule = (ctx.merged(patch).get("pages") or {}).get("title") or {}
        good = [s["url"] for s in m.samples if s.get("ok") and s.get("url")]
        hinted = dict((ctx.handoff or {}).get("types") or {}) if (ctx.handoff or {}).get("from") == "A2" else {}
        types = {}
        for s in m.samples:   # T30: provenance (the A2 block / list type) first, then the page content, then the A3 rule
            kind = hinted.get(s.get("url")) or s.get("signal") or s.get("type")
            if s.get("url") and kind in ("series", "movie"):
                types[s["url"]] = kind
        out = {"urls": good[:MAX_SAMPLES], "url_pattern": rule.get("url_pattern"), "key": rule.get("key")}
        if types:
            out["types"] = types   # A4 reads only the series, A5 takes episodes AND films (mixed sites)
        return out

    def result_line(self, patch: Optional[dict]) -> str:
        rule = ((patch or {}).get("pages") or {}).get("title") or {}
        return (f"başlık kalıbı {' | '.join(classify.patterns_of(rule)) or None}, anahtar {(rule.get('key') or {}).get('regex')}, "
                f"alanlar {', '.join((rule.get('fields') or {}).keys())}")

    reads_first = True

    def read_pages(self, ctx: StageCtx, finding: Finding, last: Optional[Measurement] = None) -> list:
        urls = (finding.evidence or {}).get("samples") or self._samples(ctx, ctx.page("title"))
        return [p for p in (ctx.get(u, "title") for u in urls[:2]) if p is not None]

    def prompt(self, ctx: StageCtx, finding: Finding, hint: str, last: Optional[Measurement]) -> str:
        return prompts_a1_a3.full(self, ctx, finding, hint, last, self.read_pages(ctx, finding, last))
