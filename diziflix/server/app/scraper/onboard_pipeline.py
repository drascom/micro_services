"""Site onboarding "aşama ilerlemesi" (pipeline): the sandbox report of a draft turned into six plain-language steps.

``build(report, events, status, *, draft)`` is a PURE function (no I/O); the admin page shows its answer under the key
``pipeline`` of ``GET /api/ops/onboard/{id}`` (and ``overall`` in the list rows). All user-facing text is Turkish and lives in
this module (``STEP_TEXT``, ``ROLE_ROWS``, ``ROLE_SIGNALS``, ``CRITERIA_TEXT``, ``translate``): the sandbox report itself stays English / technical.

    {"overall": {"state": idle|running|ok|warn|fail, "headline": str, "problem_step": <step id> | None},
     "steps":   [{"id": home|links|info|player|stream|search, "title", "question",
                  "state": pending|running|ok|warn|fail|skipped, "summary", "numbers": [{label, value}],
                  "problem": str | None (warn / fail only), "details": [{label, value}],
                  "actions": [{"id": "fix"|"skip", "label", "message"}]}],   # always six, in this order
     "app":     {"rows": [{"key", "title", "count", "from": "collection" | "list"}],       # rows of the home screen
                 "signals": [{"key", "title", "count"}],   # collections that only rank titles (slider + trend rows), no row of their own
                 "totals": {"series", "movies", "episodes", "ingest_per_list", "playable"}, "note": str}}

``actions`` (only on a ``warn`` / ``fail`` step; the page shows them as one-click buttons, a click sends ``message`` to the agent
through the ordinary message endpoint): ``fix`` = "Ajan düzeltsin" (the problem + the matching ``report.diagnostics`` + what to do),
``skip`` = "Varsa al, yoksa atla" for information fields (+ ``hint``: the field stays, taken where found, empty elsewhere), else
"Sitede yok, atla" (the message is always ``Sitede yok, atla: <field>``; only for information fields the site may simply not have: the
detail fields, the vertical poster, a home section, the search; never for the player / stream / list). The vertical poster is
called "poster (dikey)" everywhere: the wide image (backdrop) comes from TMDB and is never expected from the site.
``report.blocked`` (``{count, rules, samples}``) and ``playable.samples[].blocked`` (copyright / access placeholders the engine
does not take) are information on steps 4 / 5, never a problem and never a failed sample.

The steps follow the flow of the app: ``home`` (home page sections = yaml ``collections``), ``links`` (the list: series / film
page links), ``info`` (series page: summary, cast, seasons, episodes), ``player`` (episode / film page opened, player candidate
found), ``stream`` (the video stream resolved), ``search`` (the site's own search). Criterion -> step: ``valid_count`` /
``title_fill`` / ``detail_url_fill`` / ``poster_url_fill`` / ``normalize_ok_ratio`` / ``duplicate_key_ratio`` -> ``links``;
``collections_*`` -> ``home``; ``series_have_episode_sources`` / ``series_inventory_ok`` -> ``info``; ``playable_ratio`` ->
``player`` + ``stream``; ``search_ok`` -> ``search``; ``config_errors`` -> ``overall`` (the errors are routed to the step they
belong to by their prefix, else to ``links``).

Where the data comes from: ``draft["report"]`` (the final sandbox report of ``submit``) and ``draft["live"]`` (the latest
``test_config`` / ``test_resolvers`` / ``test_search`` / ``test_provider`` answers the agent got while it works,
``onboard_store.save_live``). While the agent runs, the live answers are laid over the (older) report; a finished draft shows
its report (``search`` is taken from the live answer when the report has none). While ``status == "running"`` the events say
which tool is in flight and the step(s) it works on are ``running``.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from urllib.parse import urlsplit

import yaml

log = logging.getLogger("scraper.onboard_pipeline")

STEP_IDS = ("home", "links", "info", "player", "stream", "search")
STATES = ("pending", "running", "ok", "warn", "fail", "skipped")
OVERALL_STATES = ("idle", "running", "ok", "warn", "fail")
ROW_KEYS = ("trending", "series", "movies", "noteworthy_movies")           # app.rows[].key (home rows this site feeds)
SIGNAL_KEYS = ("featured", "latest_episodes", "latest_series", "latest_movies")   # app.signals[].key (= the collection role)
FINAL_STATUSES = ("ready", "needs_input", "failed", "saved")   # the agent's run is over
DEFAULT_ITEM_LIMIT = 30                                         # ``library.ingest.COLLECTION_LIMIT``
YAML_MAX = 100_000

STEP_TEXT = {
    "home": ("Ana sayfa bölümleri", "Ana sayfada bölümler bulundu mu?"),
    "links": ("Dizi/film sayfası bağlantıları", "Dizi ve film sayfalarının bağlantıları bulundu mu?"),
    "info": ("Dizi bilgisi: özet, oyuncu, sezon, bölüm", "Dizi bilgisi, sezonlar, bölümler ve oyuncular alındı mı?"),
    "player": ("Bölüm sayfası ve oynatıcı", "Bölüm sayfası açılıp oynatıcı bilgisi alındı mı?"),
    "stream": ("Video akışı", "Oynatıcıdan video akışı çözüldü mü?"),
    "search": ("Site araması", "Sitenin kendi araması çalışıyor mu?"),
}

# The home screen (``homelayout``): slider, continue, "Haftanın Trendleri · Diziler", "Tüm Diziler", "Haftanın Trendleri · Filmler",
# "Dikkate Değer Filmler", "Tüm Filmler", Listem. Collection role -> (app row key, title) of the rows a collection FEEDS; the order is
# the order of the rows in the app (the main list rows "Tüm Diziler" / "Tüm Filmler" follow them in ``_app``)
ROLE_ROWS = {
    "trending": ("trending", "Haftanın Trendleri"),
    "noteworthy_movies": ("noteworthy_movies", "Dikkate Değer Filmler"),
}
# roles with NO row of their own: ``homelayout.trend_score`` signals for the slider and the trend rows (role -> (key, title))
ROLE_SIGNALS = {
    "featured": ("featured", "Öne çıkanlar"),
    "latest_episodes": ("latest_episodes", "Yeni bölümler"),
    "latest_series": ("latest_series", "Yeni diziler"),
    "latest_movies": ("latest_movies", "Yeni filmler"),
}
# the name of a site section as the "ana sayfa bölümleri" step shows it (the site's own sections, not app rows)
ROLE_TITLES = {
    "trending": "Trendler", "featured": "Öne çıkanlar", "latest_episodes": "Yeni Eklenen Bölümler",
    "latest_series": "Yeni Eklenen Diziler", "latest_movies": "En Son Filmler", "noteworthy_movies": "Dikkate Değer Filmler",
    "upcoming": "Yakında",
}
OTHER_ROLES = {"new": "Yeni liste", "catalog": "Katalog listesi", "genre": "Tür listesi"}

CRITERIA_TEXT = {   # criterion -> (what it measures, short reason for the headline)
    "valid_count": ("geçerli öğe sayısı", "listede yeterli dizi/film bulunamadı"),
    "title_fill": ("başlık", "başlıklar okunamadı"),
    "detail_url_fill": ("sayfa bağlantısı", "dizi/film sayfası bağlantıları bulunamadı"),
    "poster_url_fill": ("poster (dikey)", "dikey posterler eksik"),
    "normalize_ok_ratio": ("kimliklendirme", "öğeler tanınamadı (kimlik kuralları)"),
    "duplicate_key_ratio": ("tekrar", "tekrarlayan kayıtlar var"),
}
LINK_CRITERIA = tuple(CRITERIA_TEXT)

# The hardening criteria of a new site (``onboard_sandbox._hardening``): criterion -> (step they show on, what they measure, short reason for
# the headline, the "Sitede yok, atla" field or None = never skippable). They are NOT in CRITERIA_TEXT (that table is the ``links`` step's).
HARDEN_TEXT = {
    "availability_gate_defined": ("player", "telif / erişim kapısı", "oynatıcısı bulunamayan diziler elenmiyor (kapı tanımlı değil)", None),
    "series_signal_collection": ("home", "ana sayfada dizi bölümü", "ana sayfada dizi bölümü (Son Eklenen Diziler / Trendler) alınmıyor",
                                 "home_series_section"),
    "series_full_inventory": ("info", "dizi bölüm listesi", "diziler yalnız kartın tek bölümüyle kalıyor", "series_inventory"),
    "ingest_sample_ok": ("info", "dizi sayfası çözümleme", "bölüm kartlarından gelen diziler dizi sayfasına çözülemiyor", None),
    "home_path_is_canonical": ("home", "ana sayfa adresi", "ana sayfa başka bir adrese yönleniyor", None),
    "collection_poster_fill": ("home", "ana sayfa bölümü posteri", "ana sayfa bölümlerinde poster (dikey) alınmıyor", "collection_poster"),
    # skip fields: the info groups that are missing (``report.detail_info.missing``), one "Sitede yok" answer names them
    "detail_info_defined": ("info", "dizi / film bilgisi", "dizi / film sayfasından yeterli bilgi alanı alınmıyor", None),
}


def criterion_label(name: str) -> str:
    """The plain-Turkish name of a criterion (``Otomatik düzeltme turu`` event); the criterion itself when it has none."""
    if name in CRITERIA_TEXT:
        return CRITERIA_TEXT[name][0]
    return HARDEN_TEXT[name][1] if name in HARDEN_TEXT else name


FIELD_NAMES = {"synopsis": "özet", "overview": "özet", "description": "özet", "cast": "oyuncular", "actors": "oyuncular",
               "genres": "tür", "genre": "tür", "year": "yıl", "director": "yönetmen", "duration": "süre", "rating": "puan",
               "title": "başlık", "poster_url": "poster (dikey)", "poster": "poster (dikey)", "trailer": "fragman", "trailer_url": "fragman",
               "original_title": "özgün ad", "country": "ülke", "imdb": "IMDb", "release_date": "yayın tarihi",
               "backdrop": "yatay görsel", "backdrop_url": "yatay görsel", "landscape": "yatay görsel", "banner": "yatay görsel"}
PLAYER_FIELD_HINTS = ("player", "iframe", "video", "stream", "embed")   # detail fields that are not "info" fields
# The wide image of a title (backdrop / slider picture) comes from TMDB, never from the site: a detail field of that kind is neither
# required nor a warning when it is missing or empty ("afiş" in the app = the vertical poster, ``poster_url``)
OPTIONAL_FIELD_HINTS = ("backdrop", "landscape", "banner", "horizontal", "wide", "yatay", "still", "thumb")

RESOLVER_NAMES = {"iframe": "gömülü oynatıcı (iframe)", "anchor_host": "oynatıcı sitesine bağlantı",
                  "data_attr_token": "sayfadaki oynatıcı anahtarı", "ajax_handoff": "sayfa içi istekle oynatıcı (ajax)",
                  "json_api": "sitenin JSON arayüzü", "player_page": "sitenin kendi oynatıcı sayfası"}
STREAM_TYPES = {"hls": "HLS", "mp4": "MP4", "dash": "DASH", "webm": "WebM"}

# tool -> steps it works on while it runs (computing tools: only while in flight)
_EXPLORE_TOOLS = frozenset({"fetch_page", "outline_page", "query_html", "grep_page"})
_TOOL_STEPS = {"test_config": ("home", "links", "info", "player", "stream"), "submit_draft": STEP_IDS,
               "test_resolvers": ("player", "stream"), "test_provider": ("player", "stream"), "test_search": ("search",),
               "discover_site": ("home", "links", "info", "player"), "match_providers": ("player", "stream")}

# --- English sandbox message -> Turkish (best effort; the original text is kept when nothing matches) ----------------------

_TRANSLATIONS: list[tuple[re.Pattern, Any]] = [(re.compile(p, re.I | re.S), t) for p, t in (
    (r"row_selector .*? matched 0 elements", "satır seçicisi sayfada hiçbir öğe bulamadı (sayfa yapısı beklenenden farklı olabilir)"),
    (r"fields\.title: selector found nothing", "başlık seçicisi hiçbir satırda sonuç vermedi"),
    (r"fields\.detail_url: selector found nothing", "bağlantı seçicisi hiçbir satırda sonuç vermedi"),
    (r"fields\.poster_url: .*?in(?:\s+only)?\s+(\d+)%", lambda m: f"poster (dikey) yalnızca %{m.group(1)} satırda dolu"),
    (r"list: page not available \((.*)\)", lambda m: f"liste sayfası açılamadı ({m.group(1)})"),
    (r"list: parsing failed", "liste sayfası ayrıştırılamadı"),
    (r"(\d+) of (\d+) rows rejected by schema", lambda m: f"{m.group(2)} satırdan {m.group(1)} tanesi şemaya uymadı"),
    (r"detail: page not available \((.*)\)", lambda m: f"detay sayfası açılamadı ({m.group(1)})"),
    (r"detail: no list item with a detail_url", "detay sayfası denenemedi: listede bağlantısı olan öğe yok"),
    (r"only (\d+) usable item", lambda m: f"yalnızca {m.group(1)} kullanılabilir öğe var (en az 3 gerekir)"),
    (r"page not available \((.*?)\)", lambda m: f"sayfa açılamadı ({m.group(1)})"),
    (r"parsing failed", "sayfa ayrıştırılamadı"),
    (r"must be <role>_<site_id>|id: must be", "koleksiyon kimliği <rol>_<site> biçiminde olmalı"),
    (r"unknown key", "tanımsız anahtar kullanılmış"),
    (r"no collections:", "ana sayfa bölümleri tanımlanmadı"),
    (r"no candidates found on", "sayfada oynatıcı adayı bulunamadı (oynatıcı kuralları bu sayfaya uymuyor)"),
    (r"no normalized item with a playback address", "oynatma adresi olan örnek öğe bulunamadı"),
    (r"no stream: (.*)", lambda m: f"akış bulunamadı ({m.group(1)})"),
    (r"no stream", "akış bulunamadı"),
    (r"^page: (.*)", lambda m: f"sayfa açılamadı ({m.group(1)})"),
    (r"series items carry no episode sources", "dizi öğelerinin bölüm kaynağı yok (bölüm listesi alınamıyor)"),
    (r"series_page: no episode found on (\S+)", lambda m: f"dizi sayfasında bölüm bulunamadı ({_path(m.group(1))})"),
    (r"series_page: series page not available", "dizi sayfası açılamadı"),
    (r"series_page: row_selector matched no episode row", "dizi sayfasındaki bölüm satırları seçiciyle bulunamadı"),
    (r"no episode found", "bölüm bulunamadı"),
    (r"the yaml has no resolvers", "oynatıcı kuralları (resolvers) tanımlı değil"),
    (r"no valid resolver item", "geçerli oynatıcı kuralı yok"),
    (r"search: query .*? failed \((.*)\)", lambda m: f"arama denemesi başarısız oldu ({m.group(1)})"),
    (r"search: .*? gave no result", "arama hiç sonuç döndürmedi"),
    (r"no search", "arama tanımlı değil"),
    (r"yaml: (.*)", lambda m: f"taslak dosyası okunamadı ({m.group(1)[:120]})"),
    (r"base_url: must be", "site adresi (base_url) geçersiz"),
    (r"normalize: missing", "kimlik (normalize) kuralları yok"),
    (r"^normalize", "kimlik (normalize) kuralları hatalı"),
    (r"not checked: the call ran out of time", "süre yetmediği için denenmedi"),
)]


def _path(url: str) -> str:
    return urlsplit(url).path or url


def translate(message: Any, limit: int = 220) -> str:
    """Turkish rendering of one sandbox error / warning (``collections[x]: `` style prefixes dropped); the original text
    (clipped) when no pattern knows it."""
    text = " ".join(str(message or "").split())
    text = re.sub(r"^(?:collections|provider_recipes)\[[^\]]*\]:\s*", "", text)
    for pattern, answer in _TRANSLATIONS:
        found = pattern.search(text)
        if found:
            out = answer(found) if callable(answer) else answer
            return out[:limit]
    return text[:limit]


# --- small helpers ----------------------------------------------------------------------------------------------------

def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(value)


def _num(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _pct(value: Any) -> str:
    return f"%{round(_num(value) * 100)}"


def _join(items: list, limit: int = 4) -> str:
    items = [str(i) for i in items if str(i)]
    more = len(items) - limit
    return ", ".join(items[:limit]) + (f" (+{more})" if more > 0 else "")


def _field_name(name: str) -> str:
    return FIELD_NAMES.get(name, name)


def _crit(rep: dict, name: str) -> Optional[dict]:
    value = _dict(rep.get("criteria")).get(name)
    return value if isinstance(value, dict) and "ok" in value else None


def _crit_ok(rep: dict, name: str) -> Optional[bool]:
    crit = _crit(rep, name)
    return None if crit is None else bool(crit.get("ok"))


def _grade(value: Any) -> str:
    """A failed criterion is ``fail`` when it measured nothing at all, else ``warn`` (partly there)."""
    return "fail" if _num(value) <= 0 else "warn"


def _step(step_id: str, state: str, summary: str, *, numbers: Optional[list] = None, problem: Optional[str] = None,
          details: Optional[list] = None, short: str = "", skip: Optional[list] = None) -> dict:
    title, question = STEP_TEXT[step_id]
    return {"id": step_id, "title": title, "question": question, "state": state, "summary": summary,
            "numbers": numbers or [], "problem": problem if state in ("warn", "fail") else None,
            "details": details or [], "actions": [], "_short": short, "_skip": list(skip or [])}


def _pending(step_id: str, summary: str = "Henüz denenmedi.") -> dict:
    return _step(step_id, "pending", summary)


def _n(label: str, value: Any) -> dict:
    return {"label": label, "value": str(value)}


def diag_text(value: Any, limit: int = 500) -> str:
    """One short line of a ``report.diagnostics`` member (any shape: text, list of rows, dict); "" when it is empty. The first three
    rows of a list only."""
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value[:3] if isinstance(value, list) else value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def diagnostics_for(rep: Any, key: str) -> Any:
    """``report.diagnostics[key]``; for ``series`` (absent there) the ``diagnostics`` of the series samples; None when there is none."""
    rep = rep if isinstance(rep, dict) else {}
    found = _dict(rep.get("diagnostics")).get(key)
    if found not in (None, "", [], {}):
        return found
    if key == "series":
        rows = [s.get("diagnostics") for s in _list(_dict(rep.get("series")).get("samples")) if isinstance(s, dict) and s.get("diagnostics")]
        return rows or None
    return None


def _blocked_count(rep: dict) -> int:
    """Copyright / access blocked items the engine will not take (``report.blocked.count``, else the blocked playable samples);
    tolerant: the sandbox may not write the key yet."""
    count = _int(_dict(rep.get("blocked")).get("count"))
    playable = _dict(rep.get("playable"))
    samples = _list(playable.get("samples"))
    return max(count, _int(playable.get("blocked")), sum(1 for s in samples if isinstance(s, dict) and s.get("blocked")))


def _blocked_note(count: int) -> str:
    return f" {count} bölüm/dizi telif ya da erişim engelli, alınmayacak." if count > 0 else ""


class _NoAliasLoader(yaml.SafeLoader):
    """safe_load without aliases (the draft yaml comes from an LLM)."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.events.AliasEvent):
            raise yaml.YAMLError("aliases are not allowed")
        return super().compose_node(parent, index)


def _yaml_data(draft: dict) -> dict:
    text = draft.get("yaml_text")
    if not isinstance(text, str) or not text.strip() or len(text) > YAML_MAX:
        return {}
    try:
        data = yaml.load(text, Loader=_NoAliasLoader)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


# --- merge of report + live answers ------------------------------------------------------------------------------------

def merge(report: Any, live: Any, status: str) -> dict:
    """One report-shaped dict out of the draft's final ``report`` and its ``live`` tool answers (see the module docstring).
    Private keys: ``_resolvers`` (test_resolvers answer), ``_provider`` (test_provider answer), ``_live`` (any live answer)."""
    rep = dict(report) if isinstance(report, dict) else {}
    live = _dict(live)
    running = status == "running"
    config = _dict(live.get("test_config"))
    if config and (running or not rep):
        criteria = {**_dict(rep.get("criteria")), **_dict(config.get("criteria"))}
        rep.update({k: v for k, v in config.items() if not str(k).startswith("_")})
        rep["criteria"] = criteria
    resolvers = _dict(live.get("test_resolvers"))
    if resolvers and (running or not rep.get("playable")):
        rep["_resolvers"] = resolvers
    provider = _dict(live.get("test_provider"))
    if provider and (running or not rep.get("playable")):
        rep["_provider"] = provider
    search = _dict(live.get("test_search"))
    if search and (running or not _dict(rep.get("search"))):
        rep["search"] = {k: v for k, v in search.items() if not str(k).startswith("_")}
    rep["_live"] = bool(config or resolvers or provider or search)
    return rep


# --- errors of the report -> the step they belong to ---------------------------------------------------------------------

def _route(error: str) -> str:
    head = error.split(":", 1)[0].strip().lower()
    if head.startswith("collections"):
        return "home"
    if head.startswith(("series_page", "detail", "series")):
        return "info"
    if head.startswith(("resolvers", "providers", "provider_recipes", "playback", "stream_resolver", "recipe")):
        return "player"
    if head.startswith("search"):
        return "search"
    return "links"


def _routed_errors(rep: dict) -> dict:
    out: dict[str, list] = {step_id: [] for step_id in STEP_IDS}
    for error in _list(rep.get("errors")):
        out[_route(str(error))].append(str(error))
    return out


# --- steps --------------------------------------------------------------------------------------------------------------

def _category_title(entry: dict) -> str:
    """The admin's name of a category collection's category ("Kore Dizileri"; the slug itself when it is not registered any more)."""
    slug = str(entry.get("category") or "")
    try:
        from . import collections as site_collections
        for cat in site_collections.known_categories():
            if isinstance(cat, dict) and cat.get("slug") == slug and str(cat.get("title") or "").strip():
                return str(cat["title"]).strip()
    except Exception:   # a view helper: never takes the pipeline down
        pass
    return slug or "Kategori"


def _entry_title(entry: dict) -> str:
    role = str(entry.get("role") or "")
    if role == "category":
        return _category_title(entry)
    if role in ROLE_TITLES:
        return ROLE_TITLES[role]
    return OTHER_ROLES.get(role) or str(entry.get("id") or role or "bölüm")


def _step_home(rep: dict, errors: list) -> dict:
    cols = rep.get("collections")
    if not isinstance(cols, list):
        return _pending("home")
    entries = [c for c in cols if isinstance(c, dict)]
    if not entries:
        if errors:
            reason = translate(errors[0])
            return _step("home", "fail", "Ana sayfa bölümleri okunamadı.", problem=f"Ana sayfa bölümleri tanımlanamadı: {reason}.", short="ana sayfa bölümleri okunamadı")
        return _step("home", "warn", "Ana sayfadan okunacak bölüm tanımlanmadı.",
                     problem="Bu siteden ana ekran satırları (Haftanın Trendleri, Dikkate Değer Filmler) beslenmeyecek, slider ve trend "
                             "sıralaması da bu siteden sinyal almayacak. Ana sayfada böyle bölümler varsa ajanın eklemesi gerekir.", short="ana sayfa bölümleri tanımlanmadı",
                     skip=["collections"])
    ok = [e for e in entries if e.get("status") == "ok"]
    bad = [e for e in entries if e.get("status") == "error"]
    details = []
    for entry in entries:
        if entry.get("status") == "ok":
            value = f"{_int(entry.get('would_ingest'))} öğe"
        elif entry.get("status") == "error":
            reasons = [translate(e) for e in _list(entry.get("errors"))[:2]]
            value = "okunamadı: " + ("; ".join(reasons) or "bilinmeyen sorun")
        else:
            value = "denenmedi (süre yetmedi)"
        details.append(_n(_entry_title(entry), value))
    numbers = [_n("Bölüm", len(entries)), _n("Çalışan", len(ok)), _n("Alınacak öğe", sum(_int(e.get("would_ingest")) for e in ok))]
    if bad and not ok:
        state, summary = "fail", f"Tanımlanan {len(entries)} bölümün hiçbiri okunamadı."
    elif bad:
        state, summary = "warn", f"{len(entries)} bölümden {len(ok)} tanesi okunuyor, {len(bad)} tanesi okunamadı."
    elif not ok:
        state, summary = "warn", "Bölümler süre yetmediği için denenemedi."
    else:
        state, summary = "ok", f"Ana sayfada {len(ok)} bölüm bulundu: {_join([_entry_title(e) for e in ok])}."
    problem = short = None
    if state in ("warn", "fail"):
        lines = [f"{_entry_title(e)}: " + "; ".join(translate(x) for x in _list(e.get("errors"))[:2] or ["okunamadı"]) for e in bad[:3]]
        problem = ((" ".join(f"{line}." for line in lines) + " Ana ekranda bu bölümlerin beslediği satırlar ya da sıralama sinyali eksik kalır.") if lines
                   else "Bölümler süre yetmediği için denenemedi; ajanın testi yeniden çalıştırması gerekir.")
        short = "ana sayfa bölümleri okunamadı" if state == "fail" else "bazı ana sayfa bölümleri okunamadı"
    skip = [f"collection:{e.get('role')}" for e in bad if e.get("role")] if state in ("warn", "fail") else []
    return _step("home", state, summary, numbers=numbers, problem=problem, details=details, short=short or "", skip=skip)


def _types(rep: dict) -> tuple[int, int]:
    types = _dict(_dict(rep.get("normalize")).get("types"))
    return _int(types.get("series")), _int(types.get("movie"))


def _step_links(rep: dict, errors: list) -> dict:
    lst = rep.get("list")
    if not isinstance(lst, dict):
        if errors:
            reason = translate(errors[0])
            return _step("links", "fail", "Listeden dizi/film bağlantıları alınamadı.",
                         problem=f"Liste okunamadı: {reason}.", short="liste okunamadı: " + reason.split("(")[0].strip())
        return _pending("links")
    valid, count = _int(lst.get("valid_count")), _int(lst.get("count"))
    fill = _dict(lst.get("field_fill"))
    norm = _dict(rep.get("normalize"))
    ingest = _dict(rep.get("ingest"))
    series, movies = _types(rep)
    bad = [(name, _crit(rep, name)) for name in LINK_CRITERIA if _crit_ok(rep, name) is False]
    zero = (valid == 0 or _num(fill.get("title")) == 0 or _num(fill.get("detail_url")) == 0
            or (bool(norm) and _int(norm.get("ok")) == 0))
    state = "fail" if (zero or any(_num(c.get("value")) <= 0 for name, c in bad if name != "duplicate_key_ratio")) else ("warn" if bad else "ok")
    kinds = f" ({series} dizi, {movies} film)" if norm else ""
    summary = (f"Listede {count} satır bulundu, {valid} tanesi geçerli{kinds}; başlık {_pct(fill.get('title'))}, "
               f"bağlantı {_pct(fill.get('detail_url'))}, poster (dikey) {_pct(fill.get('poster_url'))} dolu.")
    numbers = [_n("Satır", count), _n("Geçerli", valid)]
    if norm:
        numbers += [_n("Dizi", series), _n("Film", movies)]
    if ingest:
        numbers.append(_n("Alınacak", _int(ingest.get("would_ingest"))))
    details = [_n("Başlık dolu", _pct(fill.get("title"))), _n("Sayfa bağlantısı dolu", _pct(fill.get("detail_url"))),
               _n("Poster (dikey) dolu", _pct(fill.get("poster_url")))]
    if norm:
        details.append(_n("Kimliklendirilen", f"{_int(norm.get('ok'))}/{_int(norm.get('total'))}"))
        dup = _list(norm.get("duplicate_keys"))
        details.append(_n("Tekrar eden kayıt", len(dup)))
    if ingest:
        details.append(_n("Her listeden alınan en çok", f"{_int(ingest.get('item_limit'))} öğe"))
    problem = short = None
    if state != "ok":
        lines = []
        for name, crit in bad:
            value, bound = crit.get("value"), crit.get("min", crit.get("max"))
            label = CRITERIA_TEXT[name][0]
            if name == "valid_count":
                lines.append(f"Listede yalnızca {_int(value)} geçerli öğe var (en az {_int(bound)} gerekir)")
            elif name == "duplicate_key_ratio":
                lines.append(f"Öğelerin {_pct(value)} kadarı aynı kimliği taşıyor (en çok {_pct(bound)})")
            else:
                lines.append(f"{label.capitalize()} yalnızca {_pct(value)} oranında bulunabildi (en az {_pct(bound)} gerekir)")
        for error in errors[:2]:
            lines.append(translate(error))
        problem = ". ".join(lines) + "."
        short = CRITERIA_TEXT[bad[0][0]][1] if bad else "liste okunamadı"
    skip = ["poster_url"] if any(name == "poster_url_fill" for name, _c in bad) else []   # the only list field the site may not have
    return _step("links", state, summary, numbers=numbers, problem=problem, details=details, short=short or "", skip=skip)


def _series_blocks(rep: dict) -> tuple[list, int, int]:
    """(read sample entries of the series inventory, episodes, seasons) over the samples that were really read."""
    samples = [s for s in _list(_dict(rep.get("series")).get("samples")) if isinstance(s, dict) and not s.get("skipped")]
    return samples, sum(_int(s.get("episodes")) for s in samples), sum(_int(s.get("seasons")) for s in samples)


def _detail_fill(rep: dict) -> tuple[dict, list]:
    """(fill of the detail fields, the information fields among them that stayed empty). The wide image fields (``OPTIONAL_FIELD_HINTS``)
    are left out of both: they are never expected from the site."""
    detail = _dict(rep.get("detail"))
    fill = {k: v for k, v in _dict(detail.get("fill")).items() if not any(h in str(k).lower() for h in OPTIONAL_FIELD_HINTS)}
    info = {k: v for k, v in fill.items() if not any(h in str(k).lower() for h in PLAYER_FIELD_HINTS)}
    return fill, [k for k, v in info.items() if _num(v) <= 0]


#: (public names for ``onboard.fixable_warnings``)
def path_of(url: str) -> str:
    return _path(url)


def detail_fill(rep: dict) -> tuple[dict, list]:
    return _detail_fill(rep)


def _step_info(rep: dict, errors: list) -> dict:
    norm = _dict(rep.get("normalize"))
    series_block = rep.get("series")
    inv_samples, episodes, seasons = _series_blocks(rep)
    fill, empty = _detail_fill(rep)
    series_items = _int(norm.get("episode_items"))
    without = _int(norm.get("series_without_sources"))
    if not norm and not errors:
        return _pending("info")
    details: list = []
    if fill:
        details.append(_n("Detay sayfası alanları", _join([f"{_field_name(k)} {'dolu' if _num(v) > 0 else 'boş'}" for k, v in fill.items()], 8)))
    else:
        details.append(_n("Detay sayfası alanları", "tanımlı değil"))
    if inv_samples:
        details.append(_n("Dizi sayfası örnekleri", f"{len(inv_samples)} dizi: {episodes} bölüm, {seasons} sezon"))
        notes = [translate(w) for s in inv_samples for w in _list(s.get("warnings"))[:1]]
        if notes:
            details.append(_n("Uyarı", _join(notes, 2)))
    sources = _crit(rep, "series_have_episode_sources")
    inventory = _crit(rep, "series_inventory_ok")
    hint = _dict(series_block).get("hint")
    if series_items:
        # series that have episodes: the criterion value is the better of the list cards' own sources and the series pages' inventory
        # (``onboard_sandbox._playable_criteria``); without the criterion (not judged yet) the same two readings are taken from the report
        list_ratio = (series_items - without) / series_items
        read = [s for s in inv_samples if not s.get("error")]
        inv_ratio = (sum(1 for s in read if _int(s.get("episodes")) > 0) / len(inv_samples)) if inv_samples else 0.0
        ratio = _num(sources.get("value")) if sources else max(list_ratio, inv_ratio)
        with_sources = min(series_items, max(0, round(ratio * series_items)))
        bound = _num(sources.get("min"), 0.9) if sources else 0.9
        covered = sources.get("ok") if sources else (ratio >= bound)
        if sources is None and not covered and not isinstance(series_block, dict):
            return _step("info", "pending", "Bölüm listesi henüz denenmedi.", details=details)
        state = "ok"
        short = problem = None
        skip: list = []
        if covered is False or (inventory and inventory.get("ok") is False):
            worst = min(ratio, _num(inventory.get("value"), 1.0) if inventory else 1.0)
            state = _grade(worst)
            reasons = [translate(e) for e in errors[:2]]
            for sample in inv_samples:
                if sample.get("error"):
                    reasons.append("dizi sayfası açılamadı: " + translate(sample["error"]))
                elif not _int(sample.get("episodes")):
                    reasons.append(f"dizi sayfasında bölüm bulunamadı ({_path(str(sample.get('series_url') or ''))})")
            if hint:
                reasons.append("liste kartları dizi sayfalarına gidiyor ama dizi sayfası kuralı (series_page) yok")
            reasons = list(dict.fromkeys(r for r in reasons if r))[:3]
            short = "dizi sayfasından bölüm listesi alınamadı" if state == "fail" else "bazı dizilerin bölümleri alınamadı"
            problem = ("Dizi sayfasından bölüm listesi alınamadı" if state == "fail" else "Bazı dizilerin bölüm listesi alınamadı")
            problem += (": " + "; ".join(reasons) if reasons else "") + "."
        elif empty:
            state, short = "warn", "dizi bilgisi alanları boş: " + _join([_field_name(k) for k in empty], 3)
            problem = f"Dizi sayfasında şu bilgiler alınamadı: {_join([_field_name(k) for k in empty], 5)}."
            skip = list(empty)
        details.append(_n("Bölüm kaynağı olan dizi", f"{with_sources}/{series_items}"))
        if inv_samples:
            summary = f"{series_items} dizi bulundu; örnek dizi sayfalarından {episodes} bölüm ({seasons} sezon) alındı."
        else:
            summary = (f"{series_items} dizinin {with_sources} tanesinde bölüm kaynağı var (bölümler liste kartlarından "
                       "üretiliyor).")
        numbers = [_n("Dizi", series_items), _n("Bölüm kaynağı olan", with_sources)]
        if inv_samples:
            numbers += [_n("Okunan dizi", len(inv_samples)), _n("Bölüm", episodes), _n("Sezon", seasons)]
        return _step("info", state, summary, numbers=numbers, problem=problem, details=details, short=short or "", skip=skip)
    # no series item: films only
    if not fill:
        if errors and not norm:
            reason = translate(errors[0])
            return _step("info", "fail", "Bilgi alanları okunamadı.", problem=f"{reason}.", short=reason)
        return _step("info", "skipped", "Listede dizi yok; bölüm bilgisi gerekmiyor (detay alanı da tanımlı değil).", details=details)
    if empty:
        summary = f"Film detay sayfasında şu bilgiler alınamadı: {_join([_field_name(k) for k in empty], 5)}."
        return _step("info", "warn", summary, details=details, problem=summary, skip=list(empty),
                     short="film bilgisi alanları boş: " + _join([_field_name(k) for k in empty], 3))
    return _step("info", "ok", "Listede dizi yok; film detay sayfasından bilgiler alındı.", details=details,
                 numbers=[_n("Alınan alan", len([v for v in fill.values() if _num(v) > 0]))])


def _samples(rep: dict) -> tuple[list, str]:
    """Normalized trial samples ``{locator, skipped, page_ok, candidates, ok, streams, error, providers}`` of the playback
    check and where they came from (``playable`` | ``resolvers`` | ``provider`` | "")."""
    playable = rep.get("playable")
    if isinstance(playable, dict):
        out = []
        for s in _list(playable.get("samples")):
            if not isinstance(s, dict):
                continue
            error = str(s.get("error") or "")
            out.append({"locator": str(s.get("locator") or ""), "skipped": bool(s.get("skipped")), "blocked": bool(s.get("blocked")),
                        "page_ok": not error.startswith("page:"), "candidates": _int(s.get("candidates")),
                        "ok": bool(s.get("ok")), "streams": _list(s.get("streams")), "error": error,
                        "providers": _list(s.get("providers"))})
        return out, "playable"
    resolvers = rep.get("_resolvers")
    if isinstance(resolvers, dict) and _list(resolvers.get("pages")):
        out = []
        for p in _list(resolvers.get("pages")):
            if not isinstance(p, dict):
                continue
            status = str(p.get("status") or "")
            out.append({"locator": str(p.get("detail_url") or ""), "skipped": status == "skipped", "blocked": bool(p.get("blocked")),
                        "page_ok": status != "error",
                        "candidates": _int(p.get("candidates")), "ok": status == "resolved", "streams": _list(p.get("streams")),
                        "error": str(p.get("error") or ""), "providers": []})
        return out, "resolvers"
    provider = rep.get("_provider")
    if isinstance(provider, dict) and provider.get("status") in ("resolved", "no_stream", "no_match"):
        return [{"locator": "", "skipped": False, "blocked": False, "page_ok": True, "candidates": 1 if provider.get("matched") else 0,
                 "ok": provider.get("status") == "resolved", "streams": _list(provider.get("streams")),
                 "error": str(provider.get("error") or ""), "providers": [str(provider.get("name") or "")]}], "provider"
    return [], ""


def _step_player(rep: dict, errors: list, yaml_data: dict) -> dict:
    samples, source = _samples(rep)
    if not source:
        if errors:
            reason = translate(errors[0])
            return _step("player", "fail", "Oynatıcı kuralları çalıştırılamadı.", problem=f"{reason}.",
                         short="oynatıcı kuralları hatalı")
        return _pending("player")
    blocked = _blocked_count(rep)
    note = _blocked_note(blocked)
    checked = [s for s in samples if not s["skipped"] and not s["blocked"]]   # a blocked sample is no failure: it is not tried
    details = _player_details(rep, yaml_data, samples)
    if blocked:
        details.append(_n("Telif/erişim engelli", f"{blocked} (alınmayacak)"))
    if not checked:
        if any(s["blocked"] for s in samples):
            return _step("player", "warn", "Denenen örneklerin hepsi telif/erişim engelli; oynatıcı başka bir örnekle doğrulanmadı." + note,
                         details=details, problem="Örneklerin hepsi engelli çıktı, oynatıcı çalıştığı doğrulanamadı. Engelli olmayan bir "
                         "dizi/film örneğiyle denenmesi gerekir.", short="oynatıcı doğrulanamadı (örnekler engelli)")
        warns = [translate(w) for w in _list(rep.get("warnings")) if str(w).startswith("playable")]
        return _step("player", "warn", "Örnek bölüm/film sayfası bulunamadı ya da süre yetmedi.", details=details,
                     problem=(_join(warns, 2) or "Denenecek örnek sayfa yok") + ".", short="örnek sayfa denenemedi")
    with_player = [s for s in checked if s["candidates"] > 0]
    page_fail = [s for s in checked if not s["page_ok"]]
    numbers = [_n("Denenen sayfa", len(checked)), _n("Oynatıcı bulunan", len(with_player))]
    if blocked:
        numbers.append(_n("Engelli", blocked))
    if len(with_player) == len(checked):
        return _step("player", "ok", f"{len(checked)} örnek sayfanın hepsinde oynatıcı bulundu." + note, numbers=numbers, details=details)
    if with_player:
        lost = _join([_path(s["locator"]) or "?" for s in checked if s not in with_player], 3)
        return _step("player", "warn", f"{len(checked)} örnek sayfanın {len(with_player)} tanesinde oynatıcı bulundu." + note, numbers=numbers,
                     details=details, problem=f"Şu sayfalarda oynatıcı bulunamadı: {lost}. Site her sayfada aynı oynatıcıyı kullanmıyor olabilir.",
                     short="bazı sayfalarda oynatıcı bulunamadı")
    if len(page_fail) == len(checked):
        reason = translate(page_fail[0]["error"]) if page_fail[0]["error"] else "sayfa açılamadı"
        return _step("player", "fail", "Örnek bölüm/film sayfaları açılamadı.", numbers=numbers, details=details,
                     problem=f"Sayfalar açılamadı: {reason}. Sayfa adresleri (kimlik/bölüm kuralları) ya da siteye erişim kontrol edilmeli.",
                     short="bölüm sayfaları açılamadı")
    return _step("player", "fail", f"{len(checked)} örnek sayfanın hiçbirinde oynatıcı bulunamadı." + note, numbers=numbers, details=details,
                 problem="Örnek sayfalarda oynatıcı bulunamadı: oynatıcıyı bulan kurallar bu sitenin sayfa yapısına uymuyor.",
                 short="bölüm sayfasında oynatıcı bulunamadı")


def _player_details(rep: dict, yaml_data: dict, samples: list) -> list:
    details = []
    kinds = []
    for item in _list(yaml_data.get("resolvers")):
        kind = str(_dict(item).get("type") or "")
        if kind and kind not in kinds:
            kinds.append(kind)
    if kinds:
        details.append(_n("Oynatıcıyı bulma yolu", _join([RESOLVER_NAMES.get(k, k) for k in kinds], 4)))
        details.append(_n("Sitenin kendi oynatıcısı mı", "Evet" if "player_page" in kinds else "Hayır"))
    names = [str(p) for s in samples for p in s["providers"] if p]
    names += [str(p) for p in _list(yaml_data.get("providers"))]
    names = list(dict.fromkeys(n for n in names if n))
    if names:
        details.append(_n("Sağlayıcı / tarif", _join(names, 4)))
    recipes = [str(_dict(r).get("name")) for r in _list(rep.get("provider_recipes")) if _dict(r).get("name")]
    if recipes:
        details.append(_n("Bu site için yazılan sağlayıcı tarifi", _join(recipes, 3)))
    for sample in samples[:3]:
        if sample["locator"]:
            state = ("engelli (alınmayacak)" if sample.get("blocked") else "atlandı" if sample["skipped"]
                     else (f"{sample['candidates']} oynatıcı adayı" if sample["page_ok"] else "açılamadı"))
            details.append(_n("Sayfa " + _path(sample["locator"]), state))
    return details


def _step_stream(rep: dict, player: dict) -> dict:
    samples, source = _samples(rep)
    if not source:
        return _pending("stream")
    blocked = _blocked_count(rep)
    note = _blocked_note(blocked)
    checked = [s for s in samples if not s["skipped"] and not s["blocked"]]
    if not checked:
        if any(s["blocked"] for s in samples):
            return _step("stream", "skipped", "Örnekler telif/erişim engelli olduğu için video akışı denenmedi." + note)
        return _step("stream", "pending" if player["state"] == "pending" else "skipped", "Akış denenemedi (örnek sayfa yok).")
    with_player = [s for s in checked if s["candidates"] > 0]
    if not with_player:
        return _step("stream", "skipped", "Oynatıcı bulunamadığı için video akışı denenmedi." + note)
    resolved = [s for s in checked if s["ok"]]
    streams = [st for s in resolved for st in s["streams"] if isinstance(st, dict)]
    hosts = list(dict.fromkeys(str(st.get("host") or "") for st in streams if st.get("host")))
    kinds = list(dict.fromkeys(STREAM_TYPES.get(str(st.get("type") or "").lower(), str(st.get("type") or "")) for st in streams if st.get("type")))
    qualities = list(dict.fromkeys(str(st.get("quality") or "") for st in streams if st.get("quality")))
    numbers = [_n("Çözülen", f"{len(resolved)}/{len(checked)}")]
    if kinds:
        numbers.append(_n("Tür", _join(kinds, 3)))
    if qualities:
        numbers.append(_n("Kalite", _join(qualities, 3)))
    if blocked:
        numbers.append(_n("Engelli", blocked))
    details = [_n("Akış sunucuları", _join(hosts, 4) or "-"), _n("Akış türü", _join(kinds, 3) or "-")]
    if blocked:
        details.append(_n("Telif/erişim engelli", f"{blocked} (alınmayacak)"))
    failing = [s for s in checked if not s["ok"]]
    if failing:
        details.append(_n("Çözülemeyen sayfalar", _join([_path(s["locator"]) or "?" for s in failing], 3)))
    if len(resolved) == len(checked):
        return _step("stream", "ok", f"{len(checked)} örneğin hepsinde video akışı çözüldü ({_join(kinds, 3) or 'akış'})." + note, numbers=numbers,
                     details=details)
    if resolved:
        lost = _join([_path(s["locator"]) or "?" for s in failing], 3)
        reason = next((translate(s["error"]) for s in failing if s["error"]), "")
        return _step("stream", "warn", f"{len(checked)} örneğin {len(resolved)} tanesinde video akışı çözüldü." + note, numbers=numbers,
                     details=details, problem=f"Şu sayfalarda akış çözülemedi: {lost}" + (f" ({reason})" if reason else "")
                     + ". Oynatıcı tarifi yalnız bazı sayfalara uyuyor olabilir.", short="bazı sayfalarda video akışı çözülemedi")
    reason = next((translate(s["error"]) for s in checked if s["error"]), "")
    return _step("stream", "fail", f"{len(checked)} örneğin hiçbirinde video akışı çözülemedi." + note, numbers=numbers, details=details,
                 problem="Oynatıcı bulundu ama video akışı çıkarılamadı" + (f": {reason}" if reason else "")
                 + ". Oynatıcı tarifi ya da sağlayıcı bu oynatıcıya uymuyor olabilir.", short="video akışı çözülemedi")


def _step_search(rep: dict, errors: list, final: bool) -> dict:
    step = _step_search_raw(rep, errors, final)
    if step["state"] in ("warn", "fail"):
        step["_skip"] = ["search"]   # a site may simply have no (working) search: the admin can say so
    return step


def _step_search_raw(rep: dict, errors: list, final: bool) -> dict:
    block = rep.get("search")
    if not isinstance(block, dict) or not block:
        if errors:
            reason = translate(errors[0])
            return _step("search", "fail", "Arama kuralları hatalı.", problem=f"{reason}.", short="arama kuralları hatalı")
        if "search" in rep or (final and isinstance(rep.get("playable"), dict)):   # measured: the yaml has no ``search:`` block
            return _step("search", "skipped", "Bu sitede arama tanımlı değil; bu siteden canlı arama yapılmayacak.")
        return _pending("search")
    skipped = block.get("skipped")
    if skipped:
        why = ("arama süre yetmediği için denenemedi" if "time" in str(skipped) else "aramayı denemek için listede örnek başlık bulunamadı")
        return _step("search", "warn" if final else "pending", why[0].upper() + why[1:] + ".",
                     problem=why[0].upper() + why[1:] + ".", short="arama denenemedi")
    count = _int(block.get("count"))
    raw_problems = [str(e) for e in _list(block.get("errors"))[:2]] + ([str(block["error"])] if block.get("error") else [])
    problems = [translate(e) for e in raw_problems] or [translate(e) for e in errors[:1]]
    found = block.get("found_known")
    ratio = block.get("normalize_ok_ratio")
    numbers = [_n("Sonuç", count)]
    details = []
    if block.get("query"):
        details.append(_n("Denenen arama", str(block["query"])[:80]))
    if found is not None:
        details.append(_n("Bilinen başlık sonuçlarda", "evet" if found else "hayır"))
    if ratio is not None:
        details.append(_n("Sonuçlar tanındı", _pct(ratio)))
    if block.get("ms") is not None:
        details.append(_n("Süre", f"{_int(block.get('ms'))} ms"))
    for sample in _list(block.get("samples"))[:3]:
        if isinstance(sample, dict) and sample.get("title"):
            details.append(_n("Örnek sonuç", str(sample["title"])[:80]))
    if problems or block.get("valid") is False or count == 0:
        reason = _join(problems, 2) or "arama hiç sonuç döndürmedi"
        return _step("search", "fail", "Sitenin araması çalışmadı.", numbers=numbers, details=details,
                     problem=f"Arama sonuç vermedi: {reason}.", short="site araması çalışmıyor")
    if found is False or _crit_ok(rep, "search_ok") is False:
        return _step("search", "warn", f"Arama {count} sonuç döndürdü ama aranan başlık sonuçlarda yok.", numbers=numbers, details=details,
                     problem="Arama sonuç veriyor ama bilinen bir başlığı bulamadı; sonuç satırı ya da adres kuralı yanlış olabilir.", short="arama sonuçları eksik")
    return _step("search", "ok", f"Arama çalışıyor: {count} sonuç döndü.", numbers=numbers, details=details)


# --- running overlay ------------------------------------------------------------------------------------------------------

def _active_steps(events: list, steps: list) -> list[str]:
    """Step ids the agent works on right now, from the last tool of the events (``kind`` tool / tool_result)."""
    last = None
    for index, event in enumerate(events or []):
        if isinstance(event, dict) and (event.get("kind") or event.get("type")) == "tool" and event.get("name"):
            last = (index, str(event["name"]))
    if last is None:
        return []
    index, name = last
    pending = [s["id"] for s in steps if s["state"] == "pending"]
    if name in _EXPLORE_TOOLS:
        return pending[:1]
    if name not in _TOOL_STEPS:
        return []
    finished = any(isinstance(e, dict) and (e.get("kind") or e.get("type")) == "tool_result" and e.get("name") == name
                   for e in (events or [])[index + 1:])
    return [] if finished else [sid for sid in _TOOL_STEPS[name] if sid in pending]


# --- app view -------------------------------------------------------------------------------------------------------------

def _app(rep: dict) -> dict:
    """What the app will show from this draft: ``rows`` (home rows the collections / main list feed), ``signals``
    (collections that only rank titles for the slider and the trend rows), ``totals`` and ``note``."""
    ingest = _dict(rep.get("ingest"))
    limit = _int(ingest.get("item_limit")) or DEFAULT_ITEM_LIMIT
    rows: list[dict] = []
    signals: list[dict] = []
    by_role: dict[str, int] = {}
    for entry in _list(rep.get("collections")):
        if isinstance(entry, dict) and (entry.get("role") in ROLE_ROWS or entry.get("role") in ROLE_SIGNALS):
            by_role[entry["role"]] = by_role.get(entry["role"], 0) + (_int(entry.get("would_ingest")) if entry.get("status") == "ok" else 0)
    series, movies = _types(rep)
    for role, (key, title) in ROLE_ROWS.items():
        if role in by_role:
            if role == "trending" and (series > 0) != (movies > 0):   # a one-type site feeds only that type's trend row
                title += " · Diziler" if series else " · Filmler"
            rows.append({"key": key, "title": title, "count": by_role[role], "from": "collection"})
    for role, (key, title) in ROLE_SIGNALS.items():
        if role in by_role:
            signals.append({"key": key, "title": title, "count": by_role[role]})
    categories = [e for e in _list(rep.get("collections")) if isinstance(e, dict) and e.get("role") == "category"]
    lst = _dict(rep.get("list"))
    valid = _int(lst.get("valid_count"))
    take = _int(ingest.get("would_ingest")) if ingest and "would_ingest" in ingest else min(valid, limit)
    if _dict(rep.get("normalize")) and valid:
        share = (series + movies) or 1
        pick = (lambda n: n if valid <= limit else round(take * n / share))
        if series:
            rows.append({"key": "series", "title": "Tüm Diziler", "count": pick(series), "from": "list"})
        if movies:
            rows.append({"key": "movies", "title": "Tüm Filmler", "count": pick(movies), "from": "list"})
    for entry in categories:   # an admin-managed category: its own row ("Kore Dizileri · N"), the titles belong to that category
        if entry.get("status") == "ok":
            rows.append({"key": f"category_{entry.get('category') or ''}", "title": _category_title(entry),
                         "count": _int(entry.get("would_ingest")), "from": "collection"})
    playable = _dict(rep.get("playable"))
    _s, episodes, _seasons = _series_blocks(rep)
    checked = _int(playable.get("checked"))
    totals = {"series": series, "movies": movies, "episodes": episodes, "ingest_per_list": limit,
              "playable": f"{_int(playable.get('resolved'))}/{checked}" if checked else None}
    return {"rows": rows, "signals": signals, "totals": totals,
            "note": f"Her liste en çok {limit} öğe alınır; sitenin tüm kataloğu taranmaz (tümünü bulmak sitenin araması içindir). "
                    "Ana ekran yalnız oynatılabilir başlıkları gösterir. Sinyal listeleri kendi satırını oluşturmaz, "
                    "yalnız slider ve trend sıralamasını etkiler."}


# --- hardening criteria (a new site's ``availability_gate_defined`` / ``series_signal_collection`` / ``series_full_inventory`` /
# ``home_path_is_canonical``): laid over the step they belong to ---------------------------------------------------------------

def _harden_problem(name: str, rep: dict) -> str:
    """The plain-Turkish problem text of a failed hardening criterion."""
    if name == "availability_gate_defined":
        return ("Telif / erişim engelli ya da oynatıcısı bulunamayan diziler ve filmler elenmiyor: sitede kapı (availability_gate) "
                "tanımlı değil. Bu atlanamaz; ajanın kapıyı eklemesi gerekir.")
    if name == "series_signal_collection":
        return ("Ana sayfadan dizi bölümü alınmıyor ('Son Eklenen Diziler' ya da 'Trendler' tanımlı değil ya da en az 3 öğe vermiyor): "
                "ana ekranda dizi trend satırı ve slider bu siteden beslenmez. Sitede böyle bir bölüm yoksa 'Sitede yok, atla' diyebilirsin.")
    if name == "series_full_inventory":
        return ("Liste / bölüm kartları tek bir bölümün sayfasına gidiyor ve dizi sayfalarından bölüm listesi okunmuyor: her dizi "
                "kütüphanede yalnız kartın tek bölümüyle kalır. Dizi arşivi / dizi sayfası bulunmalı. Dizi sayfalarında bölümler "
                "gerçekten listelenmiyorsa 'Sitede yok, atla' diyebilirsin.")
    if name == "ingest_sample_ok":
        sample = _dict(_dict(rep.get("series")).get("ingest_sample"))
        return (f"Sitenin dizi kartlarının {_int(sample.get('ok'), 0)} / {_int(sample.get('judged'), 0)} tanesi okunabilir bir dizi sayfasına "
                "çözülüp bölüm listesi veriyor (en az %80 olmalı): 'Son Eklenen Bölümler' gibi bölüm kartları dizinin sayfasına değil bölüm "
                "sayfasına gidiyor ve sitenin dizi listesinde karşılığı bulunamıyor ya da dizi sayfası okunamıyor. Ajan dizi arşivini liste ya da "
                "koleksiyon olarak eklemeli, anahtar / başlık kurallarını düzeltmeli. Bu atlanamaz.")
    if name == "collection_poster_fill":
        return ("Ana sayfa bölümlerinin kartlarında poster (dikey) alınmıyor ya da çok seyrek (en az %80 dolu olmalı): kartın içindeki "
                "görsel (img data-src / src) poster olarak yazılmalı. Kartta gerçekten poster yoksa 'Sitede yok, atla' diyebilirsin.")
    if name == "detail_info_defined":
        info = _dict(rep.get("detail_info"))
        missing = _join([_field_name(g) for g in _list(info.get("missing"))], 7)
        return (f"Dizi / film sayfasından yeterli bilgi alınmıyor: {len(_list(info.get('good')))} / {_int(info.get('required'), 3)} alan "
                f"dolu{(' (eksik: ' + missing + ')') if missing else ''}. Özet, yıl, oyuncu, tür, puan, fragman ve poster alanlarından en az "
                "3 tanesi tanımlı ve sayfalarda dolu olmalı; sayfada etiketi varsa ajan oradan almalı. Sitede gerçekten yoksa 'Sitede yok, "
                "atla' diyebilirsin.")
    hint = _dict(rep.get("redirect_hint"))
    target = hint.get("target")
    return (f"Site ana sayfası aslında {target} adresine yönleniyor ama liste ve bölümler hâlâ eski adresten okunuyor; adresler "
            "güncellenmeli." if target else "Site ana sayfası başka bir adrese yönleniyor ama adresler güncellenmedi.")


def _apply_hardening(steps: list, rep: dict) -> None:
    """A failed hardening criterion turns its step (``HARDEN_TEXT``) into at least a ``warn`` with the problem text (appended to the
    step's own problem), the "Sitede yok, atla" field where the criterion may be skipped, and a ``details`` line for what the admin
    already skipped (``report.exempt``)."""
    for name, (step_id, _label, short, skip_field) in HARDEN_TEXT.items():
        if _crit_ok(rep, name) is not False:
            continue
        step = steps[STEP_IDS.index(step_id)]
        text = _harden_problem(name, rep)
        if step["state"] in ("pending", "ok", "skipped"):
            step["state"] = "warn"
        step["problem"] = f"{step['problem']} {text}" if step.get("problem") else text
        if not step.get("_short"):
            step["_short"] = short
        if skip_field and skip_field not in step["_skip"]:
            step["_skip"].append(skip_field)
        if name == "detail_info_defined":   # each missing group may be answered "not on the site" (never skipped by the agent itself)
            for group in _list(_dict(rep.get("detail_info")).get("missing")):
                if group not in step["_skip"]:
                    step["_skip"].append(group)
    removed = [x for x in _list(rep.get("removed_fields")) if isinstance(x, dict) and x.get("field")]
    if removed:   # a field that gave values in the previous submission is gone: the admin decides ("yok" or "geri koy")
        step = steps[STEP_IDS.index("home" if any(str(x.get("where")).startswith("collections") for x in removed) else "info")]
        names = _join([_field_name(str(x["field"])) for x in removed], 5)
        text = (f"Önceki denemede değer üreten alan(lar) kaldırılmış: {names}. Sitede gerçekten yoksa 'Sitede yok, atla' de, değilse ajan "
                "alanı geri koysun.")
        if step["state"] in ("pending", "ok", "skipped"):
            step["state"] = "warn"
        step["problem"] = f"{step['problem']} {text}" if step.get("problem") else text
        if not step.get("_short"):
            step["_short"] = "önceki denemede dolan alan kaldırılmış"
        for x in removed:
            if str(x["field"]) not in step["_skip"]:
                step["_skip"].append(str(x["field"]))
    for item in _list(rep.get("exempt")):
        entry = HARDEN_TEXT.get(str(_dict(item).get("criterion")))
        if entry:
            optional = bool(_dict(item).get("optional"))
            steps[STEP_IDS.index(entry[0])]["details"].append(
                _n("Opsiyonel (varsa alınır)" if optional else "Atlandı (sitede yok)", entry[1]))


# --- one-click actions of a problem box -------------------------------------------------------------------------------------

#: step -> the ``report.diagnostics`` members that explain it (the sandbox writes them; missing = nothing to add)
STEP_DIAG = {"home": ("collections",), "links": ("list",), "info": ("detail", "series"), "player": ("player",), "stream": ("player",),
             "search": ("search",)}
MESSAGE_MAX = 1900   # the message endpoint takes 2000 characters


def fix_message(step: dict, rep: dict) -> str:
    """The message of "Ajan düzeltsin": the problem of the step, the matching diagnostics and what to do (the admin writes nothing)."""
    number = STEP_IDS.index(step["id"]) + 1
    text = f"Şu sorunu kendin düzelt ({number}. adım, {step['title']}): {step.get('problem') or step.get('summary') or ''}"
    shown = [f"{key}: {t}" for key in STEP_DIAG.get(step["id"], ()) for t in [diag_text(diagnostics_for(rep, key), 400)] if t]
    if shown:
        text += " Ayrıntı: " + " | ".join(shown) + "."
    text += (" Nedenini sayfada kanıtla bul (query_html / grep_page), düzelt, test_config(playable: true, collections: true) ve submit_draft'i "
             "tekrar çalıştır; çözemezsen ask_user ile ne denediğini ve önerini yaz.")
    return text if len(text) <= MESSAGE_MAX else text[:MESSAGE_MAX - 1] + "…"


SKIP_OPTIONAL_HINT = "Alan korunur: bulunan sayfalarda alınır, bulunamayanlarda boş kalır."


def _optional_field(name: Any) -> bool:
    """Is ``name`` an information field (detail field / vertical poster) that stays in the yaml when the admin skips it?"""
    low = re.sub(r"[^a-z0-9_]+", "_", str(name or "").strip().lower()).strip("_")
    return low == "collection_poster" or low in FIELD_NAMES


def _attach_actions(steps: list, rep: dict) -> None:
    """``actions`` of every ``warn`` / ``fail`` step, and drop the private ``_skip`` of all of them."""
    for step in steps:
        skip = step.pop("_skip", [])
        if step["state"] not in ("warn", "fail") or not step.get("problem"):
            continue
        step["actions"].append({"id": "fix", "label": "Ajan düzeltsin", "message": fix_message(step, rep)})
        if skip:
            # information fields keep their yaml extraction ("Varsa al, yoksa atla": taken where found, empty elsewhere); the answer text
            # is the same pattern for all (``onboard.ANSWER_ABSENT``)
            optional = all(_optional_field(f) for f in skip)
            step["actions"].append({"id": "skip", "label": "Varsa al, yoksa atla" if optional else "Sitede yok, atla",
                                    "message": "Sitede yok, atla: " + ", ".join(skip),
                                    **({"hint": SKIP_OPTIONAL_HINT} if optional else {})})


# --- overall --------------------------------------------------------------------------------------------------------------

def _overall(steps: list, rep: dict, status: str, draft: dict, has_data: bool) -> dict:
    problem = next((s for s in steps if s["state"] == "fail"), None) or next((s for s in steps if s["state"] == "warn"), None)
    problem_step = problem["id"] if problem else None
    index = (STEP_IDS.index(problem["id"]) + 1) if problem else 0
    running = next((s for s in steps if s["state"] == "running"), None)
    if status == "running":
        headline = f"Ajan çalışıyor: {running['title']}" if running else "Ajan çalışıyor…"
        if _int(draft.get("auto_round")) > 0:
            headline += f" (otomatik düzeltme {_int(draft.get('auto_round'))}/{_int(draft.get('auto_rounds')) or '?'})"
        return {"state": "running", "headline": headline, "problem_step": problem_step}
    if status == "needs_input" and isinstance(draft.get("question_data"), dict):
        kind = draft["question_data"].get("kind")
        headline = ("Ajan yaml ile çözemediği bir eksiği bildirdi." if kind == "engine_gap"
                    else "Ajan sana bir soru sordu; yanıtını bekliyor.")
        return {"state": "warn", "headline": headline, "problem_step": problem_step}
    if problem is not None and problem["state"] == "fail":
        what = problem["_short"] or problem["summary"]
        return {"state": "fail", "headline": f"Sorun {index}. adımda: {what}", "problem_step": problem_step}
    if status == "failed":
        error = str(draft.get("error") or "bilinmeyen hata")
        return {"state": "fail", "headline": "Ajan hata ile durdu: " + error[:160], "problem_step": problem_step}
    if _list(rep.get("errors")) and rep.get("valid") is False:
        return {"state": "fail", "headline": "Taslak yapılandırmasında hata var: " + translate(rep["errors"][0], 160),
                "problem_step": problem_step}
    if problem is not None:
        what = problem["_short"] or problem["summary"]
        return {"state": "warn", "headline": f"Dikkat, {index}. adımda eksik var: {what}", "problem_step": problem_step}
    waiting = next((s for s in steps if s["state"] == "pending"), None)
    if status == "needs_input" and not has_data:
        return {"state": "warn", "headline": "Ajan taslak teslim etmeden durdu; yanıtını okuyup geri bildirim yazın.", "problem_step": None}
    if waiting is not None:
        if not has_data:
            return {"state": "idle", "headline": "Henüz sonuç yok.", "problem_step": None}
        number = STEP_IDS.index(waiting["id"]) + 1
        return {"state": "warn", "headline": f"Taslak yarım kaldı: {number}. adım ({waiting['title']}) henüz denenmedi.", "problem_step": None}
    if rep.get("passed") is False and rep.get("criteria"):
        return {"state": "warn", "headline": "Adımlar çalışıyor ama kabul ölçütleri tam karşılanmadı.", "problem_step": None}
    return {"state": "ok", "headline": "Tüm adımlar çalışıyor", "problem_step": None}


# --- public ---------------------------------------------------------------------------------------------------------------

def build(report: Optional[dict], events: Optional[list], status: str, *, draft: Optional[dict] = None) -> dict:
    """The pipeline view of one draft (see the module docstring). ``report`` = ``draft["report"]``; ``draft`` carries ``live``,
    ``yaml_text`` and ``error``. Pure: nothing is read or written."""
    draft = draft if isinstance(draft, dict) else {}
    rep = merge(report, draft.get("live"), status)
    routed = _routed_errors(rep)
    final = status in FINAL_STATUSES
    yaml_data = _yaml_data(draft)
    home = _step_home(rep, routed["home"])
    links = _step_links(rep, routed["links"])
    info = _step_info(rep, routed["info"])
    player = _step_player(rep, routed["player"], yaml_data)
    stream = _step_stream(rep, player)
    search = _step_search(rep, routed["search"], final)
    steps = [home, links, info, player, stream, search]
    _apply_hardening(steps, rep)
    if status == "running":
        for sid in _active_steps(events or [], steps):
            step = steps[STEP_IDS.index(sid)]
            step["state"], step["summary"] = "running", "Ajan şu an bu adımı deniyor."
    has_data = bool(report) or bool(rep.get("_live"))
    overall = _overall(steps, rep, status, draft, has_data)
    _attach_actions(steps, rep)
    for step in steps:
        step.pop("_short", None)
    return {"overall": overall, "steps": steps, "app": _app(rep)}


def for_draft(draft: dict) -> dict:
    """``build`` of a stored draft."""
    return build(draft.get("report"), draft.get("events"), str(draft.get("status") or ""), draft=draft)


def empty(headline: str = "Henüz sonuç yok.") -> dict:
    """The answer of a draft nothing is known about (also what ``safe`` returns when ``build`` fails)."""
    steps = [_pending(step_id) for step_id in STEP_IDS]
    for step in steps:
        step.pop("_short", None)
        step.pop("_skip", None)
    return {"overall": {"state": "idle", "headline": headline, "problem_step": None}, "steps": steps,
            "app": {"rows": [], "signals": [], "totals": {"series": 0, "movies": 0, "episodes": 0, "ingest_per_list": DEFAULT_ITEM_LIMIT,
                                           "playable": None}, "note": ""}}


def safe(draft: dict) -> dict:
    """``for_draft`` that never raises (a bug here must not break the admin page)."""
    try:
        return for_draft(draft)
    except Exception:
        log.exception("onboarding pipeline of %s failed", draft.get("id") if isinstance(draft, dict) else "?")
        return empty("Aşama bilgisi hesaplanamadı.")
