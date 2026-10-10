"""Field proposals of a title (series / film) page: labelled visible text -> JSON-LD -> og/meta, each verified with the parse
engine. Pure. Field names follow the site map (README 5.5): title, poster, year, genres, overview, cast, rating, trailer."""
from __future__ import annotations

import json
import re
from typing import Any

from selectolax.parser import HTMLParser

from . import parse
from ._select import unique_selector
from ._util import clip, fold, text_of

_LABELS = {
    "year": ("Yapım Yılı", "Yapım Yıl", "Çıkış Yılı", "Vizyon Tarihi", "Yıl", "Year"),
    "cast": ("Oyuncular", "Oyuncu", "Cast"),
    "genres": ("Türü", "Tür", "Kategoriler", "Kategori", "Genre"),
    "rating": ("IMDb Puanı", "IMDb", "IMDB", "Puan"),
}
_SYNOPSIS_LABELS = ("Özet", "Konu", "Hakkında", "Açıklama", "Synopsis")
_STOP_LABELS = ("Yönetmen", "Director", "Süre", "Ülke", "Yapımcı", "Senarist", "Fragman", "Dil", "Yayın", "Durum")
_SYNOPSIS_TOKENS = ("ozet", "summary", "description", "aciklama", "synopsis", "konu", "plot", "icerik", "overview", "storyline")
INFO_FIELDS = ("title", "overview", "year", "cast", "genres", "rating", "trailer", "poster")
_LD_TYPES = {"TVSeries", "Movie", "TVSeason", "CreativeWorkSeries"}


_I = "[iıİI]"
#: a "read more" toggle the summary element carries as its last words (``... Devamını Göster``, ``Read more``, ``[Daha fazla]``)
_MORE_TAIL = re.compile(rf"\s*(?:\.{{3}}|…)?\s*[\[(]?\s*(?:devam[ıi]n[ıi]\s+(?:g[öo]ster|oku)|devam[ıi]\s+(?:i[çc]in\s+t[ıi]kla|ediyor)|"
                        rf"daha\s+fazla(?:\s+g[öo]ster)?|t[üu]m[üu]n[üu]\s+g[öo]ster|read\s+more|show\s+more|see\s+more)\s*[\])]?\s*$", re.I)
#: the separator of a two-language summary (``Türkçe metin ---- English text``): only the first part is kept
_LANG_SPLIT = re.compile(r"\s*(?:-{4,}|_{4,}|={4,}|\u2014{2,}|\u2013{3,})\s*")


def clean_overview(text):
    """A summary without the "read more" toggle tail and, for a two-language summary split by ``----``, only its first part
    (``Bir aile... ---- A family...`` -> ``Bir aile...``). Whitespace collapsed; non-strings returned as they are."""
    if not isinstance(text, str):
        return text
    out = " ".join(text.split())
    for _ in range(2):
        out = _MORE_TAIL.sub("", out)
    parts = [p.strip() for p in _LANG_SPLIT.split(out) if p.strip()]
    out = parts[0] if parts else out
    return _MORE_TAIL.sub("", out).strip()


def json_ld(tree) -> list[dict]:
    """The TVSeries / Movie / TVSeason / CreativeWorkSeries objects of the page's JSON-LD scripts."""
    out: list[dict] = []
    for node in tree.css('script[type="application/ld+json"]'):
        raw = node.text() or ""
        if not raw.strip() or len(raw) > 200_000:
            continue
        try:
            stack: list = [json.loads(raw)]
        except ValueError:
            continue
        while stack:
            item = stack.pop(0)
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                if isinstance(item.get("@graph"), list):
                    stack.extend(item["@graph"])
                kinds = item.get("@type")
                kinds = kinds if isinstance(kinds, list) else [kinds]
                if _LD_TYPES & {k for k in kinds if isinstance(k, str)}:
                    out.append(item)
    return out


def _label_alt(labels: tuple) -> str:
    return "|".join(re.escape(label).replace("\\ ", r"\s*") for label in labels)


def detail_fields(html: str, page_url: str = "") -> tuple[dict, dict]:
    """``(fields, evidence)``: ``evidence[field] = {source: label|jsonld|meta|css, selector, sample}``, ``evidence['_tried']`` = what
    was looked for per field that was not found."""
    tree = HTMLParser(html or "")
    root = tree.root
    fields: dict[str, dict] = {}
    evidence: dict[str, Any] = {"_tried": {}}
    containers: list[tuple[Any, str]] = []
    for node in tree.css("div, p, span, li, td, dd, ul, section, article"):
        text = text_of(node)
        if 8 <= len(text) <= 3000:
            containers.append((node, text))
    containers.sort(key=lambda row: len(row[1]))
    all_labels = tuple(l for ls in _LABELS.values() for l in ls) + _SYNOPSIS_LABELS + _STOP_LABELS
    stop_alt = _label_alt(all_labels)

    def accept(name: str, spec: dict, source: str) -> bool:
        value = parse.apply_field(root, spec)
        if value in (None, "", []) or (name == "rating" and not (isinstance(value, (int, float)) and 0 < value <= 10)):
            return False
        fields[name] = spec
        evidence[name] = {"source": source, "selector": spec.get("selector"),
                          "sample": clip(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), 80)}
        return True

    for spec, source in (({"selector": "h1"}, "css"), ({"selector": 'meta[property="og:title"]', "attr": "content"}, "meta")):
        if accept("title", spec, source):
            break
    for name, labels in _LABELS.items():
        alt = _label_alt(labels)
        if name == "year":
            value_rx, extras = rf"(?:{alt})\s*:?\s*((?:19|20)\d{{2}})", {"cast": "int"}
            probe = re.compile(value_rx, re.I)
        elif name == "rating":
            value_rx, extras = rf"(?:{alt})\s*:?\s*([0-9]+(?:[.,][0-9]+)?)", {"cast": "float"}
            probe = re.compile(value_rx, re.I)
        else:
            probe = re.compile(rf"(?:{alt})\s*:\s*(\S.{{1,}})", re.I)
        for node, text in containers:
            match = probe.search(text)
            if not match:
                continue
            if name in ("cast", "genres"):
                others = [l for l in all_labels if l not in labels]
                after = [l for l in others if re.search(re.escape(l) + r"\s*:", text[match.start():], re.I)]
                stop = f"(?=(?:{_label_alt(tuple(after))})\\s*:|$)" if after else "$"
                value_rx, extras = rf"(?:{alt})\s*:\s*(.*?){stop}", {"then_split": ","}
            selector = unique_selector(tree, node)
            if not selector:
                continue
            spec = {"selector": selector, "regex": value_rx, **extras}
            if accept(name, spec, "label"):
                break
        else:
            evidence["_tried"][name] = [f"label '{labels[0]}' in visible text"]
    for node, text in containers:
        cls = ((node.attributes.get("class") or "") + " " + (node.attributes.get("id") or "")).lower()
        if len(text) >= 80 and any(t in fold(cls) for t in _SYNOPSIS_TOKENS) and node.tag != "ul":
            selector = unique_selector(tree, node)
            if not selector:
                continue
            spec: dict[str, Any] = {"selector": selector}
            first_label = re.search(rf"(?:{stop_alt})\s*:", text, re.I)
            if first_label and first_label.start() > 40:
                spec["regex"] = rf"^(.+?)(?=(?:{stop_alt})\s*:)"
            if accept("overview", spec, "label"):
                break
    ld = json_ld(tree)
    ld_text = " ".join((n.text() or "") for n in tree.css('script[type="application/ld+json"]'))
    ld_script = 'script[type="application/ld+json"]'
    if "overview" not in fields:
        for spec, source in (({"selector": 'meta[property="og:description"]', "attr": "content"}, "meta"),
                             ({"selector": 'meta[name="description"]', "attr": "content"}, "meta"),
                             ({"selector": ld_script, "regex": r'"description"\s*:\s*"((?:[^"\\]|\\.)+)"'}, "jsonld")):
            if (source != "jsonld" or ld) and accept("overview", spec, source):
                break
        else:
            evidence["_tried"]["overview"] = ["summary-like element, og:description, meta description, JSON-LD description"]
    if "year" not in fields and ld and re.search(r'"(?:datePublished|dateCreated)"\s*:\s*"(?:19|20)\d{2}', ld_text):
        accept("year", {"selector": ld_script, "regex": r'"(?:datePublished|dateCreated)"\s*:\s*"((?:19|20)\d{2})', "cast": "int"}, "jsonld")
    if "rating" not in fields and ld and re.search(r'"ratingValue"\s*:\s*"?[0-9]', ld_text):
        accept("rating", {"selector": ld_script, "regex": r'"ratingValue"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)', "cast": "float"}, "jsonld")
    if "genres" not in fields and ld and re.search(r'"genre"\s*:\s*"', ld_text):
        accept("genres", {"selector": ld_script, "regex": r'"genre"\s*:\s*"([^"]+)"', "then_split": ","}, "jsonld")
    for spec in ({"selector": 'meta[property="og:video:url"]', "attr": "content"}, {"selector": 'meta[property="og:video"]', "attr": "content"},
                 {"selector": "iframe[src*='youtube']", "attr": "src"}, {"selector": "a[href*='youtube.com/watch']", "attr": "href"},
                 {"selector": "[data-trailer]", "attr": "data-trailer"}):
        if accept("trailer", spec, "meta" if "meta" in spec["selector"] else "css"):
            break
    else:
        evidence["_tried"]["trailer"] = ["og:video, youtube iframe / link, data-trailer"]
    accept("poster", {"selector": 'meta[property="og:image"]', "attr": "content"}, "meta")
    for name in INFO_FIELDS:
        if name not in fields and name not in evidence["_tried"]:
            evidence["_tried"][name] = [f"{name}: no label, meta or JSON-LD"]
    return fields, evidence
