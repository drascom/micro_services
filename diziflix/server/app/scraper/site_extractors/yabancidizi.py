"""Player URL discovery for yabancidizi.news detail pages.

The site renders its content player as an iframe below ``#video-area``.  The
iframe can point at the site's short-lived ``/api/drives/...`` hand-off rather
than VidMolly itself; that is still a provider *candidate*.  Following the
hand-off and parsing the eventual provider page are generic responsibilities.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urljoin
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from .. import parse
from ..providers import trace


PLAYER_IFRAMES = "#video-area .player iframe[src], #video-area iframe[src]"
# Language tabs of an episode page ("Türkçe Altyazı" / "İngilizce Altyazı"). The markup carries TWO ``class``
# attributes (``class="ui pointing item active" ... class="item"``); an HTML parser keeps the last one and
# loses ``active``, so the tab is read from the raw tag where the first attribute is the one browsers use.
_LANG_TAB = re.compile(r"<a\b(?P<attrs>[^>]*\bdata-lango\b[^>]*)>(?P<body>.*?)</a>", re.S | re.I)
_TAB_TYPES = {"1": ("tr", "Türkçe altyazı"), "3": ("en", "İngilizce altyazı")}


def _language(text: str = "", tab_type: str = "") -> tuple[str, str] | None:
    """``(code, label)`` of a site language tab / download link ("Türkçe Altyazı", "İngilizce Altyazılı İndir");
    None when the text does not say (the caller then leaves the language out instead of guessing)."""
    folded = " ".join((text or "").split()).casefold()
    kind = "dublaj" if "dublaj" in folded else "altyazı"
    if "ngilizce" in folded:
        return "en", f"İngilizce {kind}"
    if "rkçe" in folded or "rkce" in folded:
        return "tr", f"Türkçe {kind}"
    return _TAB_TYPES.get((tab_type or "").strip())


def _active_language(html: str) -> tuple[str, str] | None:
    """Language of the ACTIVE tab. The site renders ``.alternatives-for-this`` and the default player for the
    active tab only (the other tab is fetched by its own AJAX call), so every candidate found in the page
    belongs to that tab. Falls back to the first tab when none is marked; None without tabs."""
    tabs: list[tuple[tuple[str, str] | None, bool]] = []
    for match in _LANG_TAB.finditer(html):
        attrs = match.group("attrs")
        classes = re.findall(r'\bclass\s*=\s*"([^"]*)"', attrs)
        kind = re.search(r'\bdata-type\s*=\s*"([^"]*)"', attrs)
        text = re.sub(r"<[^>]+>", " ", match.group("body"))
        tabs.append((_language(text, kind.group(1) if kind else ""), bool(classes) and "active" in classes[0].split()))
    active = next((language for language, is_active in tabs if is_active and language), None)
    return active or next((language for language, _ in tabs if language), None)


def _with_language(candidate: dict, language: tuple[str, str] | None) -> dict:
    if language:
        candidate["lang"], candidate["language"] = language
    return candidate


def _safe_token(value: str) -> str:
    """Use the URL-safe alphabet used by the site's player endpoints."""
    return value.strip().replace("/", "_").replace("+", "-")


def _detail_metadata(tree: HTMLParser, page_url: str) -> dict:
    """Read film/series facts owned by a YabancıDizi detail page."""
    parsed_page = urlparse(page_url)
    origin = f"{parsed_page.scheme}://{parsed_page.netloc}/"

    def image_url(selector: str, attribute: str) -> str | None:
        node = tree.css_first(selector)
        value = (node.attributes.get(attribute) or "").strip() if node else ""
        value = urljoin(origin, value) if value else ""
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or parsed.hostname != parsed_page.hostname:
            return None
        return value

    metadata: dict = {}
    poster = image_url("img.series-profile-thumb", "src")
    backdrop = (image_url('meta[property="og:image"]', "content")
                or image_url('img[src*="uploads/series/cover/"]', "src"))
    if poster:
        metadata["poster_url"] = poster
    if backdrop:
        metadata["backdrop_url"] = backdrop

    heading = tree.css_first("h1.page-title")
    heading_text = " ".join(heading.text(separator=" ", strip=True).split()) if heading else ""
    # Season / episode pages append " - 4. Sezon" / " 4. Sezon 10. Bölüm" to the series title.
    heading_text = re.sub(r"\s*(?:-\s*)?\d+\.\s*Sezon(?:\s+\d+\.\s*Bölüm)?\s*$", "", heading_text).strip()
    year_match = re.search(r"\(((?:19|20)\d{2})\)", heading_text)
    if year_match:
        metadata["year"] = int(year_match.group(1))
    title = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", heading_text).strip()
    if title:
        metadata["title"] = title
    portrait = tree.css_first("img.series-profile-thumb")
    original_title = (portrait.attributes.get("alt") or "").strip() if portrait else ""
    if original_title and original_title != title:
        metadata["original_title"] = original_title

    summary = tree.css_first("#tv-series-desc")
    overview = " ".join(summary.text(separator=" ", strip=True).split()) if summary else ""
    overview = re.sub(r"\s*(?:\.\.\.)?\s*Devamını Göster\s*$", "", overview, flags=re.I)
    overview = re.split(r"\s*-{4,}\s*", overview, maxsplit=1)[0].strip()
    if overview:
        metadata["overview"] = overview

    genres = []
    # The site emits root-relative-looking links without a leading slash (``dizi/tur/...``).
    genre_nodes = tree.css('#series-profile-content-wrapper a[href*="dizi/tur/"]')
    genre_nodes += tree.css('#series-profile-content-wrapper a[href*="film/tur/"]')
    for anchor in genre_nodes:
        genre = " ".join(anchor.text(strip=True).split())
        if genre and genre not in genres:
            genres.append(genre)
    if genres:
        metadata["genres"] = genres

    cast = []
    # Series pages list the cast in the "Öne Çıkan Oyuncular" box, film pages inside the profile wrapper.
    for anchor in tree.css('#series-profile-content-wrapper a[href*="oyuncu/"], #common-cast-list a[href*="oyuncu/"]'):
        heading_node = anchor.css_first("h5")
        name = " ".join((heading_node or anchor).text(strip=True).split())
        if name and name not in cast:
            cast.append(name)
    if cast:
        metadata["cast"] = cast

    rating = tree.css_first(".media-meta .color-imdb")
    try:
        metadata["rating"] = float((rating.text(strip=True) if rating else "").replace(",", "."))
    except ValueError:
        pass

    for cell in tree.css(".media-meta td"):
        parts = [" ".join(node.text(separator=" ", strip=True).split())
                 for node in cell.css("div")]
        if len(parts) < 2:
            continue
        label, value = parts[0].casefold(), parts[-1]
        if "ülke" in label:
            metadata["country"] = value
        elif "süre" in label:
            runtime = re.search(r"\b(\d{1,3})\s*dk\b", value, re.I)
            if runtime:
                metadata["runtime"] = int(runtime.group(1))
        elif "takip" in label:
            followers = re.search(r"\d+", value.replace(".", ""))
            if followers:
                metadata["followers"] = int(followers.group(0))
        elif "puan" in label:
            try:
                metadata["rating"] = float(value.replace(",", "."))
            except ValueError:
                pass
        elif "yılı" in label or "yili" in label:
            year = re.search(r"(?:19|20)\d{2}", value)
            if year:
                metadata["year"] = int(year.group(0))

    trailer = tree.css_first(".media-trailer[data-yt]")
    trailer_id = (trailer.attributes.get("data-yt") or "").strip() if trailer else ""
    if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", trailer_id):
        metadata["trailer_url"] = "https://www.youtube.com/embed/" + trailer_id
    return metadata


def detail_metadata(html: str, page_url: str) -> dict:
    """Extract all reusable metadata without opening the content player."""
    return _detail_metadata(HTMLParser(html), page_url)


def discover(html: str, page_url: str) -> list[dict]:
    seen: set[str] = set()
    candidates: list[dict] = []
    tree = HTMLParser(html)
    tab_language = _active_language(html)  # the tab every hand-off / default player of this page belongs to

    # The page also exposes VidMolly download URLs. Their file code can be
    # normalized by the shared VidMolly provider without touching the site's
    # Cloudflare-protected /api/moly hand-off, so prefer these stable hints.
    for anchor in tree.css('a[href*="/dl/"]'):
        url = urljoin(page_url, (anchor.attributes.get("href") or "").strip())
        host = (urlparse(url).hostname or "").lower()
        if not re.fullmatch(r"(?:.+\.)?vidmol{1,2}y\.[a-z0-9.-]+", host) or url in seen:
            continue
        label = " ".join(anchor.text(strip=True).split())
        seen.add(url)
        # Both tabs' files are linked here; the link text says which one ("Türkçe/İngilizce Altyazılı İndir").
        candidates.append(_with_language({"url": url, "label": label or "VidMolly"}, _language(label)))

    # Alternatives are site-owned opaque hand-offs. VidMolly still has a
    # legacy GET mapping; OK.ru requires the site's session-bound AJAX call.
    for item in tree.css(".alternatives-for-this [data-link]"):
        label = " ".join(item.text(strip=True).split())
        raw_link = item.attributes.get("data-link") or ""
        token = _safe_token(raw_link)
        if label.casefold() == "vidmoly" and token:
            url = urljoin(page_url, f"/api/moly/{token}")
            if url not in seen:
                seen.add(url)
                candidates.append(_with_language({"url": url, "label": "VidMolly"}, tab_language))
            continue
        if label.casefold() not in ("okru", "ok.ru") or not raw_link:
            continue
        data_hash = item.attributes.get("data-hash") or ""
        if not data_hash:
            continue
        marker = "okru:" + raw_link
        if marker in seen:
            continue
        seen.add(marker)
        candidates.append(_with_language({
            "url": page_url,
            "label": "OK.ru",
            "handoff": {
                "provider": "okru",
                "url": urljoin(page_url, "/ajax/service"),
                "link": raw_link,
                "hash": data_hash,
                "querytype": item.attributes.get("data-querytype") or "alternate",
            },
        }, tab_language))

    for iframe in tree.css(PLAYER_IFRAMES):
        src = (iframe.attributes.get("src") or "").strip()
        url = urljoin(page_url, src)
        if not src or url in seen:
            continue
        seen.add(url)
        candidates.append(_with_language({"url": url, "label": "YabancıDizi"}, tab_language))
    return candidates


_VIDMOLLY_HOST = re.compile(r"(?:.+\.)?vidmol{1,2}y\.[a-z0-9.-]+")
_OKRU_HOST = re.compile(r"(?:.+\.)?ok\.ru")
_BLOCKED = (401, 403, 429, 503, 520, 521, 522, 523, 524, 525, 526)


def _light_cookies(hostname: str) -> list[dict]:
    """The cookie the site's own script sets before any hand-off works: ``comolokko("udys", Date.now(), ...)``
    in main.min.js, a millisecond timestamp. ``ci_session`` is issued by the server on the first call and kept
    by the HTTP session. So no browser is needed to open a hand-off (a browser session costs 10+ seconds)."""
    return [{"name": "udys", "value": str(int(time.time() * 1000)), "domain": hostname, "path": "/"}]


def _handoff(candidate: dict, page_url: str, cookies: list[dict], stage: str) -> tuple[dict | None, bool]:
    """One complete hand-off attempt. Returns ``(result, retry_with_browser)``; ``retry_with_browser`` is set only
    when the answer looks cookie/Cloudflare related (HTTP block, non-JSON body), never for a definite "no"."""
    from curl_cffi import requests

    handoff = candidate.get("handoff") or {}
    page = urlparse(page_url)
    candidate_url = urlparse(urljoin(page_url, candidate.get("url") or ""))
    okru = handoff.get("provider") == "okru"
    started = time.monotonic()
    session = requests.Session(impersonate="chrome")
    try:
        for cookie in cookies:
            name, value = cookie.get("name"), cookie.get("value")
            if not name or value is None:
                continue
            session.cookies.set(
                name, value, domain=cookie.get("domain") or page.hostname,
                path=cookie.get("path") or "/")
        if okru:
            response = session.post(
                handoff["url"],
                data={
                    "link": handoff["link"], "hash": handoff["hash"],
                    "querytype": handoff["querytype"], "type": "videoGet",
                },
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Origin": f"{page.scheme}://{page.netloc}",
                    "Referer": page_url,
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=10,
            )
            if response.status_code != 200:
                trace.note(stage, page.hostname or "", False, started, f"ajax/service HTTP {response.status_code}")
                return None, response.status_code in _BLOCKED
            try:
                answer = response.json()
            except ValueError:
                trace.note(stage, page.hostname or "", False, started, "ajax/service answered with a non-JSON page")
                return None, True
            if not isinstance(answer, dict):
                trace.note(stage, page.hostname or "", False, started, "ajax/service answered with unexpected JSON")
                return None, True
            iframe_url = urljoin(page_url, answer.get("api_iframe") or "")
            if not answer.get("api_iframe") or urlparse(iframe_url).hostname != page.hostname:
                reason = answer.get("error") or "no api_iframe in the answer"
                trace.note(stage, page.hostname or "", False, started, f"ajax/service: {reason}")
                return None, False
        else:
            iframe_url = candidate_url.geturl()
        iframe_response = session.get(iframe_url, headers={"Referer": page_url}, timeout=10)
        if iframe_response.status_code != 200:
            trace.note(stage, page.hostname or "", False, started, f"hand-off page HTTP {iframe_response.status_code}")
            return None, iframe_response.status_code in _BLOCKED
        iframe = HTMLParser(iframe_response.text).css_first("iframe[src]")
        provider_url = urljoin(iframe_url, iframe.attributes.get("src") if iframe is not None else "")
        host = (urlparse(provider_url).hostname or "").lower()
        wanted, label = (_OKRU_HOST, "OK.ru") if okru else (_VIDMOLLY_HOST, "VidMolly")
        if iframe is None or not wanted.fullmatch(host):
            trace.note(stage, page.hostname or "", False, started,
                       f"hand-off page has no {label} iframe (host {host or '-'})")
            return None, iframe is None   # a page without any iframe is the challenge/interstitial page
        trace.note(stage, page.hostname or "", True, started)
        return {"url": provider_url, "label": label}, False
    except Exception as exc:
        trace.note(stage, page.hostname or "", False, started, exc)
        return None, True
    finally:
        session.close()


def resolve_candidate(candidate: dict, page_url: str, load_cookies) -> dict | None:
    """Exchange YabancıDizi's session hand-offs for real provider URLs.

    Cheap first: the hand-off is opened with the one cookie the site's script would set (:func:`_light_cookies`),
    a couple of seconds. Only when the site refuses that (HTTP block / challenge page) the browser session's
    cookie jar (``load_cookies``: an Obscura run, 10+ seconds) is used as the fallback. Each attempt leaves a
    stage in the provider trace, so a failed hand-off shows its reason in the admin resolver trail.
    """
    handoff = candidate.get("handoff") or {}
    page = urlparse(page_url)
    candidate_url = urlparse(urljoin(page_url, candidate.get("url") or ""))
    provider = handoff.get("provider")
    is_vidmolly_handoff = (
        candidate_url.hostname == page.hostname
        and candidate_url.path.startswith("/api/moly/")
    )
    if provider != "okru" and not is_vidmolly_handoff:
        return candidate
    endpoint = urlparse(handoff.get("url") or "") if provider == "okru" else candidate_url
    if endpoint.scheme not in ("http", "https") or endpoint.hostname != page.hostname:
        trace.note("yabancidizi.handoff", page.hostname or "", False, time.monotonic(),
                   f"hand-off address is not on {page.hostname}")
        return None

    result, need_browser = _handoff(candidate, page_url, _light_cookies(page.hostname or ""), "yabancidizi.handoff")
    if result or not need_browser:
        return result
    started = time.monotonic()
    try:
        cookies = list(load_cookies())
    except Exception as exc:
        trace.note("yabancidizi.handoff.browser", page.hostname or "", False, started, exc)
        return None
    known = {cookie.get("name") for cookie in cookies}
    cookies += [cookie for cookie in _light_cookies(page.hostname or "") if cookie["name"] not in known]
    result, _ = _handoff(candidate, page_url, cookies, "yabancidizi.handoff.browser")
    return result


def _series_slug(page_url: str) -> str | None:
    current = re.fullmatch(r"/dizi/([^/]+)(?:/sezon-\d+(?:/bolum-\d+)?)?/?", urlparse(page_url).path)
    return current.group(1) if current else None


def _anchor_scan(tree: HTMLParser, page_url: str, slug: str) -> tuple[set[str], dict[tuple[int, int], dict]]:
    """Season-page URLs and episodes found by URL pattern alone (``/dizi/<slug>/sezon-N[/bolum-M]``)."""
    pattern = re.compile(rf"/dizi/{re.escape(slug)}/sezon-(\d+)(?:/bolum-(\d+))?/?$")
    seasons: set[str] = set()
    episodes: dict[tuple[int, int], dict] = {}
    # The site emits root-relative-looking links without a leading slash
    # (``dizi/foo/...``). Resolve those against the origin, not the current
    # season path.
    origin = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}/"
    for anchor in tree.css("a[href]"):
        url = urljoin(origin, anchor.attributes.get("href") or "")
        match = pattern.fullmatch(urlparse(url).path)
        if not match:
            continue
        season = int(match.group(1))
        episode_text = match.group(2)
        if not episode_text:
            seasons.add(url)
            continue
        episode = int(episode_text)
        item = episodes.setdefault((season, episode), _episode_entry(season, episode, url))
        text = " ".join(anchor.text(strip=True).split())
        if text and not re.fullmatch(r"Bölüm\s+\d+", text, re.I) and not text.startswith("Dizinin "):
            item["title"] = text
    return seasons, episodes


def _episode_entry(season: int, episode: int, url: str, title: str = "", air_date: str | None = None) -> dict:
    entry = {
        "key": f"s{season}e{episode}", "url": url, "kind": "episode",
        "resolver": "page", "season": season, "episode": episode,
        "label": f"{season}. Sezon {episode}. Bölüm",
        "title": title or f"{episode}. Bölüm", "overview": "", "runtime": 0,
    }
    if air_date:
        entry["air_date"] = air_date
    return entry


def series_catalog(html: str, page_url: str) -> dict:
    """Extract season navigation and episode records without opening players."""
    slug = _series_slug(page_url)
    if not slug:
        return {"season_pages": [], "video_sources": []}
    tree = HTMLParser(html)
    seasons, episodes = _anchor_scan(tree, page_url, slug)
    return {
        "metadata": _detail_metadata(tree, page_url),
        "season_pages": sorted(seasons),
        "video_sources": [episodes[key] for key in sorted(episodes)],
    }


def _extra_metadata(tree: HTMLParser, page_url: str) -> dict:
    """Series-page facts the library keeps in ``normalized`` only (no API field yet): the English half of
    the summary and the cast photos (names/photos of record come from TMDB credits later)."""
    origin = urlparse(page_url)
    extra: dict = {}
    summary = tree.css_first("#tv-series-desc")
    text = " ".join(summary.text(separator=" ", strip=True).split()) if summary else ""
    text = re.sub(r"\s*(?:\.\.\.)?\s*Devamını Göster\s*$", "", text, flags=re.I)
    parts = re.split(r"\s*-{4,}\s*", text, maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        extra["overview_en"] = parts[1].strip()
    photos = []
    for anchor in tree.css('#common-cast-list a[href*="oyuncu/"]'):
        image = anchor.css_first("img.artist-photo, img")
        src = (image.attributes.get("src") or image.attributes.get("data-src") or "").strip() if image else ""
        url = urljoin(f"{origin.scheme}://{origin.netloc}/", src) if src else ""
        heading = anchor.css_first("h5")
        name = " ".join((heading or anchor).text(strip=True).split())
        role = anchor.css_first(".description")
        role_text = " ".join(role.text(strip=True).split()) if role else ""
        if not name or urlparse(url).hostname != origin.hostname or urlparse(url).scheme not in ("http", "https"):
            continue
        photo = {"name": name, "photo_url": url}
        if role_text:
            photo["character"] = role_text
        if photo not in photos:
            photos.append(photo)
    if photos:
        extra["cast_photos"] = photos
    return extra


def _episode_ref(tree: HTMLParser, selector: str, page_url: str, slug: str) -> tuple[int, int] | None:
    node = tree.css_first(selector) if selector else None
    if node is None:
        return None
    url = urljoin(f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}/", node.attributes.get("href") or "")
    match = re.fullmatch(rf"/dizi/{re.escape(slug)}/sezon-(\d+)/bolum-(\d+)/?", urlparse(url).path)
    return (int(match.group(1)), int(match.group(2))) if match else None


def series_inventory(html: str, page_url: str, spec: dict | None = None) -> dict:
    """Full season/episode inventory of one series (or season) page plus the series-level facts.

    The site renders EVERY season of the series as a tab on each of its series/season pages, so one page
    normally holds the whole inventory. ``spec`` is the yaml ``series_page`` block (all selectors live there):
    ``season_menu`` (season links), ``tab_selector`` + ``tab_season_attr`` (one panel per season),
    ``row_selector`` (episode rows in a panel), ``fields`` (``url``/``title``/``air_date`` field specs of the
    shared parse engine), ``unaired_classes`` (row classes of episodes that have not aired: no video yet, not
    written), ``first_episode`` / ``last_episode`` (the "watch first/last episode" links used to verify the list).
    Season/episode numbers always come from the episode URL. When the structured rows are missing (site
    markup drift) the URL-pattern scan still finds the episodes (no dates, ``structured`` False).
    """
    spec = spec or {}
    slug = _series_slug(page_url)
    empty = {"metadata": {}, "declared_seasons": [], "tab_seasons": [], "video_sources": [], "unaired": [],
             "first": None, "last": None, "structured": False, "warnings": [],
             "metrics": {"valid_count": 0, "fill_ratio": 0.0, "field_fill": {}, "anchor_count": 0}}
    if not slug:
        return empty
    tree = HTMLParser(html)
    origin = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}/"
    pattern = re.compile(rf"/dizi/{re.escape(slug)}/sezon-(\d+)/bolum-(\d+)/?$")
    season_pattern = re.compile(rf"/dizi/{re.escape(slug)}/sezon-(\d+)/?$")
    warnings: list[str] = []

    anchor_seasons, anchor_eps = _anchor_scan(tree, page_url, slug)
    declared: set[int] = set()
    for node in (tree.css(spec["season_menu"]) if spec.get("season_menu") else []):
        match = season_pattern.fullmatch(urlparse(urljoin(origin, node.attributes.get("href") or "")).path)
        if match:
            declared.add(int(match.group(1)))
    if not declared:  # no (working) menu selector: every season link on the page
        declared = {int(season_pattern.fullmatch(urlparse(url).path).group(1)) for url in anchor_seasons}

    fields = spec.get("fields") or {}
    aired: dict[tuple[int, int], dict] = {}
    unaired: dict[tuple[int, int], str | None] = {}
    tab_seasons: set[int] = set()
    rows_total = rows_valid = 0
    fill = {name: 0 for name in fields}
    unaired_classes = set(spec.get("unaired_classes") or [])
    tab_attr = spec.get("tab_season_attr") or "data-season"
    if spec.get("tab_selector") and spec.get("row_selector") and fields:
        for tab in tree.css(spec["tab_selector"]):
            try:
                tab_season = int(tab.attributes.get(tab_attr) or "")
            except ValueError:
                tab_season = None
            for row in tab.css(spec["row_selector"]):
                rows_total += 1
                values = {name: parse.apply_field(row, field_spec) for name, field_spec in fields.items()}
                for name, value in values.items():
                    if value not in (None, "", []):
                        fill[name] += 1
                match = pattern.fullmatch(urlparse(urljoin(origin, str(values.get("url") or ""))).path)
                if not match:
                    continue
                season, episode = int(match.group(1)), int(match.group(2))
                if tab_season is not None and tab_season != season:
                    warnings.append(f"sezon sekmesi {tab_season} != bölüm URL'si s{season}e{episode}")
                rows_valid += 1
                tab_seasons.add(season)
                classes = set((row.attributes.get("class") or "").split())
                title = " ".join(str(values.get("title") or "").split())
                air_date = values.get("air_date") if isinstance(values.get("air_date"), str) else None
                if classes & unaired_classes:
                    unaired[(season, episode)] = air_date
                    continue
                url = urljoin(origin, str(values["url"]))
                aired[(season, episode)] = _episode_entry(season, episode, url, title, air_date)
    metrics = {
        "valid_count": rows_valid,
        "fill_ratio": round(rows_valid / rows_total, 3) if rows_total else 0.0,
        "field_fill": {name: (round(count / rows_total, 3) if rows_total else 0.0) for name, count in fill.items()},
        "anchor_count": len(anchor_eps),
    }
    first = _episode_ref(tree, spec.get("first_episode", ""), page_url, slug)
    last = _episode_ref(tree, spec.get("last_episode", ""), page_url, slug)
    structured = bool(rows_valid)
    if not structured:
        # Selector drift or an old page: fall back to the URL scan. Episodes after the site's own "last
        # episode" link have not aired yet (the structured path knows this from the row class).
        for key, entry in anchor_eps.items():
            if last is not None and key > last:
                unaired[key] = None
                continue
            aired[key] = entry
    else:
        missed = [key for key in anchor_eps if key not in aired and key not in unaired]
        if missed:  # links the row selectors did not cover: not trusted, but visible in the crawl report
            warnings.append(f"{len(missed)} bölüm bağlantısı satır seçicilerinin dışında kaldı")
    return {
        "metadata": {**_detail_metadata(tree, page_url), **_extra_metadata(tree, page_url)},
        "declared_seasons": sorted(declared),
        "tab_seasons": sorted(tab_seasons | {s for s, _ in aired} | {s for s, _ in unaired}),
        "video_sources": [aired[key] for key in sorted(aired)],
        "unaired": [{"season": s, "episode": e, "air_date": d} for (s, e), d in sorted(unaired.items())],
        "first": first, "last": last, "structured": structured, "warnings": warnings, "metrics": metrics,
    }
