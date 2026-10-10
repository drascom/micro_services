"""Title / slug / artwork text helpers (copied from the old generic normalizer).

No per-site normalize functions and no yaml ``normalize:`` engine: fields arrive by name from the site map.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

# --- placeholder artwork ("no image" files) -----------------------------------------------------------------------
# A site's "no picture" file is not artwork: it must not count as poster / backdrop / episode still (empty -> TMDB fills it,
# otherwise the client draws its own placeholder).
_PH_STRONG = re.compile(
    r"placeholder|place[-_]?holder|no[-_]?image|no[-_]?img|no[-_]?pic|no[-_]?photo|no[-_]?poster|no[-_]?cover|no[-_]?thumb"
    r"|image[-_]?(not[-_]?)?(available|found)|default[-_]?(poster|image|img|cover|thumb\w*|banner|backdrop)"
    r"|poster[-_]?(not[-_]?)?(available|found)|coming[-_]?soon[-_]?(poster|image)", re.I)
_PH_STEMS = {"none", "blank", "missing", "dummy", "default", "empty", "nothing", "noimage", "nopic", "nophoto", "na", "n-a",
             "unknown", "notfound", "not-found", "not_found", "no-poster", "no_poster", "1x1", "pixel", "spacer"}
_PH_SIZE = re.compile(r"([-_.@]\d+x\d*|[-_.]\d+|[-_](small|medium|large|thumb|thumbnail|poster|cover|image|img|bg|portrait|landscape))+$", re.I)


def is_placeholder_image(url, shared=None) -> bool:
    """True for an image URL that is a site's "no picture" file: the path holds ``placeholder`` / ``no-image`` / ``noimage`` /
    ``no_image`` / ``nopic`` / ``default-poster`` ..., or the file name is just ``none`` / ``blank`` / ``missing`` / ``dummy`` /
    ``default`` ... (optionally with a size tail: ``none-300x450.png``). ``shared`` = URLs used by several different titles
    (``write.shared_art_urls``). A real poster (a title word in the path, a hash name) is never matched. ``data:`` URLs are not judged."""
    if not isinstance(url, str) or not url.strip():
        return False
    url = url.strip()
    if shared and url in shared:
        return True
    if url.startswith("data:"):
        return False
    try:
        path = urlparse(url).path or ""
    except ValueError:
        return False
    path = path.lower()
    if _PH_STRONG.search(path):
        return True
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if not stem:
        return False
    return stem in _PH_STEMS or _PH_SIZE.sub("", stem) in _PH_STEMS


# --- identity / title safety net ----------------------------------------------------------------------------------
_TR_FOLD = (("ı", "i"), ("İ", "i"), ("ş", "s"), ("ğ", "g"), ("ç", "c"), ("ö", "o"), ("ü", "u"))


def fold(text) -> str:
    """Lower-case, Turkish letters and diacritics folded to ASCII, everything but letters / digits dropped (``Halef: Köklerin Çağrısı HD``
    -> ``halefkoklerincagrisihd``): the comparison key of two titles (``title_key``)."""
    out = str(text or "")
    for a, b in _TR_FOLD:
        out = out.replace(a, b).replace(a.upper(), b)
    out = unicodedata.normalize("NFKD", out)
    return re.sub(r"[^a-z0-9]+", "", "".join(c for c in out if not unicodedata.combining(c)).lower())


title_key = fold

#: episode / release tails of a series slug, cut in this order and repeated until nothing changes. A bare trailing number is
#: NEVER cut (``daha-17``, ``7-numara``, ``masterchef-2026``): only the numbers that sit in front of ``-bolum`` go.
_SLUG_TAILS = tuple(re.compile(p, re.I) for p in (
    r"-\d+-sezon-\d+-bolum(?:-[a-z0-9-]*)?$",                                  # -2-sezon-5-bolum[...]
    r"-\d+-bolum(?:-[a-z0-9-]*)?$",                                             # -26-bolum, -106-bolum-1-ekim, -4-bolum-full-izle-tek-parca
    r"-son-bolum(?:-[a-z0-9-]*)?$",                                              # -son-bolum, -son-bolum-izle6, -son-bolum-izle-6
    r"-bolum-(?:izle|\d+)(?:-[a-z0-9-]*)?$",                                    # -bolum-izle-1-ekim, -bolum-12
    r"(?:-hd\d*)?-(?:full-)?izle(?:-tek-parca)?(?:-hd\d*)?(?:-\d+)?$",          # -izle, -izle-hd, -hd-izle, -full-izle-tek-parca, -izle-2
    r"-(?:full-)?tek-parca$",
))


def clean_series_slug(slug: str) -> str:
    """The series slug without episode / release tails (``kalbim-sana-emanet-26-bolum`` -> ``kalbim-sana-emanet``,
    ``daha-17-18-bolum`` -> ``daha-17``, ``carpisma-son-bolum-izle6`` -> ``carpisma``); the slug itself when nothing is left."""
    text = str(slug or "")
    cur = text
    for _ in range(4):
        prev = cur
        for tail in _SLUG_TAILS:
            cur = tail.sub("", cur)
        if cur == prev:
            break
    cur = cur.strip("-_ ")
    return cur if len(cur) >= 2 else text


_I = "[iıİI]"
_SITE_SUFFIX = re.compile(r"(?:\s*\|\s*|\s+[-\u2013\u2014]\s+)(?P<site>[^|\-\u2013\u2014]{2,40}?)\s*$")
_TITLE_TAIL = re.compile(rf"(?:\s+(?:{_I}zle|hd|full|tek\s+par[cç]a))+$", re.I)
_SON_BOLUM = re.compile(rf"\s+son\s+b[öo]l[üu]m(?:\s+{_I}zle)?$", re.I)
_EPISODE_TAIL = re.compile(r"(?:\s+\d+\s*\.?\s*sezon)?\s+\d+(?:\s*[-\u2013]\s*\d+)?\s*\.?\s*b[öo]l[üu]m\b.*$", re.I)
_LEAD_IZLE = re.compile(rf"^\s*(?:full\s+)?{_I}zle[\s:\-]+(?=\S)", re.I)
#: a series' season / final tail AFTER other words (`` - 4. Sezon``, `` 2. Sezon Finali``, `` Sezon 3``, `` Season 2``, `` Final``,
#: `` Final Bölümü``): one canonical title per series, not one per season card. A leading word (``Final Destination``) stays.
_FINAL = rf"f{_I}nal(?:{_I}|\s+b[öo]l[üu]m[üu]?)?"
_SEASON_TAIL = re.compile(
    rf"(?<=\S)(?:\s*[-\u2013\u2014:|]\s*|\s+)(?:\d+\s*\.?\s*sezon(?:\s+{_FINAL})?|sezon\s+\d+|season\s+\d+|(?:sezon\s+)?{_FINAL})$",
    re.I)


def site_names(base_url: str, hosts=()) -> list[str]:
    """Folded names the site goes by (host stem of ``base_url`` / extra hosts): ``www.example-tv.com`` -> ``exampletv``."""
    out = []
    for host in [urlparse(base_url or "").hostname or ""] + ([hosts] if isinstance(hosts, str) else list(hosts or [])):
        host = str(host or "").lower()
        parts = [p for p in (host[4:] if host.startswith("www.") else host).split(".") if p]
        stem = fold(parts[0]) if parts else ""
        if len(stem) >= 3 and stem not in out:
            out.append(stem)
    return out


def _is_site(part: str, names) -> bool:
    folded = fold(part)
    return bool(folded) and any(n == folded or n in folded or (len(folded) >= 4 and folded in n) for n in names)


def clean_title(title, *, series: bool = True, site_names=()) -> str:
    """A listing title without site litter: trailing `` | <Site>`` (when the part after the bar IS the site), `` izle``, `` HD``, `` Full``,
    `` Tek Parça``, and for a series `` Son Bölüm``, the season / final tail (``_SEASON_TAIL``) and the episode tail (``Halef 37.Bölüm ...``, ``2. Sezon 5. Bölüm ...``); a leading ``izle``.
    Only these patterns; whitespace collapsed; the title itself when nothing would be left."""
    text = " ".join(str(title or "").split())
    cur = text
    for _ in range(6):
        prev = cur
        found = _SITE_SUFFIX.search(cur)
        if found and _is_site(found.group("site"), site_names):
            cur = cur[:found.start()]
        if series:
            cur = _EPISODE_TAIL.sub("", cur)
        cur = _TITLE_TAIL.sub("", cur)
        if series:
            cur = _SON_BOLUM.sub("", cur)
            cur = _SEASON_TAIL.sub("", cur)
        cur = _LEAD_IZLE.sub("", cur).strip(" -\u2013\u2014|")
        if cur == prev:
            break
    return cur or text


def slugify(text: str) -> str:
    """Turkish-aware ``[a-z0-9-]`` slug (``untitled`` when empty): the provisional canonical id base."""
    text = text or ""
    for a, b in _TR_FOLD:
        text = text.replace(a, b).replace(a.upper(), b)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "untitled"


# --- live-search query of a title ("search elsewhere") ------------------------------------------------------------
#: episode / season phrases a title may carry that a SERIES search must not: cut at the first of them ("Final" only after other
#: words: ``Final Destination`` stays).
_QUERY_CUTS = tuple(re.compile(p, re.I) for p in (
    r"\s*\b\d+\s*\.?\s*b[öo]l[üu]m\b.*$",       # 37.Bölüm, 37. Bölüm Full izle
    r"\s*\bs\d+\s*e\d+\b.*$",                    # S04E10
    r"\s*\b\d+\s*\.?\s*sezon\b.*$",             # 2. Sezon
    rf"\s+f{_I}nal(?:{_I})?\b.*$",               # ... Final / Finali
))
SEARCH_QUERY_MAX = 80


def search_query(title, site_names=()) -> str:
    """The query a title is searched with on another site: the SERIES name without episode / season phrases and site litter
    (``Halef 37.Bölüm`` -> ``Halef``, ``Dizi S04E10`` -> ``Dizi``, ``Dizi - 2. Sezon Finali`` -> ``Dizi``); the title itself when
    less than 3 characters would be left."""
    text = " ".join(str(title or "").split())
    out = clean_title(text, series=True, site_names=site_names)
    for cut in _QUERY_CUTS:
        out = cut.sub("", out)
    out = " ".join(out.split()).strip(" -\u2013\u2014:|,.")
    return (out if len(out) >= 3 else text)[:SEARCH_QUERY_MAX]


def first_words(query: str, n: int = 2) -> str:
    """The first ``n`` words of a query (the fallback when the full query finds nothing on a site)."""
    return " ".join(str(query or "").split()[:n])
