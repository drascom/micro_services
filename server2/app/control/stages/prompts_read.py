"""The LLM's FIRST READ of a stage (LLM-first onboarding, T23). One prompt per stage run: the stage's PURPOSE + the page map
of its sample pages (``core.extract.pagemap``, LLM-free) + the previous stage's handoff + the site summary (admin labels,
earlier stages) + the admin hint + the system's own guess (LLM-free discovery) + the exact reply schema (generated from
the validator below) + one short example. The site note heads the prompt (``site_notes.with_note`` in the engine).

The reply has two parts::

    interpretation:            # every block / section / menu link the stage cares about
      - {ref: b3, meaning: "trend kullanıcılar", take: false, why: "kullanıcı profili adresleri"}
    rules:                     # the stage's map patch (current schema), e.g.
      pages: {listing: {...}}

``split`` turns a parsed reply into ``(interpretation, rules, error)``; the engine measures ``rules`` like any patch."""
from __future__ import annotations

import json
from typing import Any, Optional

from ...core.extract import htmlclip, pagemap
from ...core.llm import prompts
from . import brief as site_brief

MAX_PROMPT = 32_000
MAP_LIMIT = pagemap.LIMIT      # characters of one page map
HTML_CLIP = 5_000              # characters of the clipped HTML of the first sample page
GUESS_CLIP = 2_500
MAX_ITEMS = 30
TEXT_CLIP = 160

PURPOSE = {
    "A1": "Understand this site's HOME page. Find the sections (a heading + a row of repeated cards) and say what each one "
          "opens: title pages (series / film detail pages) or episode pages; sections of users, comments, news, ads or "
          "site chrome are not taken. Find the menu links and say which ones are title LISTS (series / film archive, "
          "genre, category), which are episode lists and which are something else (login, contact, forum). Sections built "
          "from the same HTML can hold different things: decide from their headings and their sample rows. For each taken "
          "section / title list also propose home_target (where it goes on our home screen).",
    "A2": "Find this site's series / film LIST pages and, on them, the blocks whose rows are TITLES (series or films). "
          "Separate user / member / comment / ad blocks and site chrome (menus, footer genre lists): they are not taken. "
          "Blocks with the same HTML but different content are told apart by their headings and sample rows; write each "
          "one into rules.pages.listing.blocks (type series / movie / mixed, or ignore: true). Give the row selector, the "
          "fields and the pagination of EACH list: lists may differ (one archive is a single page, another is paged) -> "
          "listings[].pagination; on a page with several blocks often only ONE block is paged (the page map shows "
          "pages=<kind> next to it) -> blocks[].pagination on that block, {kind: none} on the others. Kinds: none | "
          "next_link | page_param | path_number. For each taken block also propose home_target.",
    "A3": "Understand a TITLE (series / film detail) page of this site: its address shape and key, whether it is a series or "
          "a film, and where the title, poster, year, genres and overview are. Blocks of other titles (similar titles, "
          "comments, menus) are not taken. A site may hold BOTH series and films: the type comes first from where the title "
          "was listed (a film list / block = movie), then from the page (an episode list = series, a player on the title "
          "page = movie); say the type of each sample in the interpretation.",
    "A4": "Understand a SERIES page of this site: which link group is THIS series' episode list (not other series, not the "
          "A-Z menu, not a favourite button), how seasons are reached, and how season / episode numbers are read.",
    "A5": "Understand an EPISODE / FILM page of this site: where the video player is and EVERY source alternative (iframes, "
          "tabs, buttons, selects, AJAX loaders, encoded scripts), and the link back to the series page. On a site with "
          "films too, a film's title page is its player page; when the film player differs from the episode player write "
          "pages.player.by_type: {episode: [...], movie: [...]} (resolvers stays the shared list).",
}

#: the interpretation item schema: the prompt's schema text and the validator are both generated from this table
INTERPRETATION = {
    "ref": ("text", "the ref from the page map: b<N> = a block, m<N> = a menu link (another short name if it has none)"),
    "meaning": ("text", "what it is, in a few words (e.g. 'trend diziler', 'kullanıcı listesi', 'dizi arşivi')"),
    "take": ("true|false", "true = this stage must read it for its purpose; false = leave it out"),
    "why": ("text", "short reason: heading, link type, sample rows"),
    "home_target": ("text, optional (A1/A2 only)", "where the taken row goes on OUR home screen: an existing row / "
                    "category name, 'slider', 'yok', or a new category name; several rows may share one; follow the "
                    "site note when it says where rows go"),
}
#: interpretation keys a reply may leave out
OPTIONAL_ITEMS = ("home_target",)

EXAMPLE = {
    "A2": """interpretation:
  - {ref: b2, meaning: "trend diziler", take: true, why: "başlık 'Diziler', satırlar /dizi/ adresleri"}
  - {ref: b3, meaning: "trend filmler", take: true, why: "başlık 'Filmler', satırlar /film/ adresleri"}
  - {ref: b4, meaning: "trend kullanıcılar", take: false, why: "kullanıcı profili adresleri (/profil/)"}
rules:
  pages:
    listing:
      url_pattern: '^/trends/?$'
      row_selector: "div.list > div.item"
      fields: {url: {selector: a, attr: href}, title: {selector: a}, poster: {selector: img, attr: data-src}}
      pagination: {kind: none}
      blocks:
        - {url: /trends, index: 1, heading: "Diziler", type: series}
        - {url: /trends, index: 2, heading: "Filmler", type: movie}
        - {url: /trends, index: 3, heading: "Kullanıcılar", ignore: true}""",
    "A1": """interpretation:
  - {ref: b1, meaning: "son eklenen diziler", take: true, why: "kartlar dizi sayfasına gidiyor"}
  - {ref: b2, meaning: "son bölümler", take: true, why: "kartlar bölüm sayfasına gidiyor (target: episode)"}
  - {ref: m3, meaning: "dizi arşivi", take: true, why: "menüde tüm diziler listesi"}
  - {ref: m4, meaning: "üye ol", take: false, why: "hesap sayfası"}
rules:
  pages:
    home:
      url: /
      sections:
        - {id: s1, title: "Son Eklenen Diziler", row_selector: "div.series > div.card", target: title,
           fields: {url: {selector: a, attr: href}, title: {selector: h3}, poster: {selector: img, attr: data-src}}}
      nav: {links: [{name: "Dizi Arşivi", url: /arsiv/, kind: archive, content: title}]}""",
}
_GENERIC_EXAMPLE = """interpretation:
  - {ref: b1, meaning: "...", take: true, why: "..."}
  - {ref: b2, meaning: "...", take: false, why: "..."}
rules:
  pages:
    <page type>: {...}"""


#: T30: URLs may follow no standard (user observation); the page type comes from where a link sits + the page content
URL_HINT = ("URL'ler standart olmayabilir; sayfa türünü bağlantının nerede bulunduğundan ve sayfa içeriğinden belirle; "
            "url kalıbı yalnız ipucu. Several address shapes of one page type -> url_patterns: [..] (a list); season / "
            "episode numbers may come from the row text or the list order (numbering.from: [url, text, position]).")


def url_variety(maps: list) -> str:
    """The address shapes of the links on the sample pages (``urlpatterns.cluster`` per page map), biggest first."""
    lines = []
    for _url, pm in maps or []:
        for shape in (pm.get("url_shapes") or [])[:8]:
            line = f'{shape["shape"]} x{shape["count"]} (e.g. {shape["example"]})'
            if line not in lines:
                lines.append(line)
    return "\n".join(lines[:12])


def schema_text(stage) -> str:
    """The exact reply schema (interpretation items from :data:`INTERPRETATION`, rules = the stage's map fragment)."""
    from .prompts_a1_a3 import shape_of
    items = "\n".join(f"    {name}: <{kind}>   # {what}" for name, (kind, what) in INTERPRETATION.items())
    shape = shape_of(stage.page_type)
    rules = "\n".join("  " + line for line in shape.splitlines())
    return (f"Reply with ONLY one ```yaml block with exactly two top-level keys:\ninterpretation:   # list, at most {MAX_ITEMS} "
            f"items, one per block / section / menu link of the page map that matters for the purpose\n  - \n{items}\n"
            f"rules:            # the '{stage.page_type}' part of the site map (only pages.{stage.page_type})\n{rules}")


def _bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in ("true", "yes", "evet", "1", "al", "alınır", "take"):
        return True
    if text in ("false", "no", "hayır", "hayir", "0", "alma", "alınmaz", "skip", "ignore"):
        return False
    return None


def validate(items: Any) -> tuple[list[dict], list[str]]:
    """Clean interpretation items (``ref``/``meaning`` text, ``take`` bool, ``why`` text) + notes about dropped ones."""
    notes: list[str] = []
    if items is None:
        return [], ["LLM yorum vermedi (interpretation yok)"]
    if isinstance(items, dict):
        items = [{"ref": k, **(v if isinstance(v, dict) else {"meaning": v})} for k, v in items.items()]
    if not isinstance(items, list):
        return [], ["interpretation bir liste değil"]
    out = []
    for i, item in enumerate(items[:MAX_ITEMS]):
        if not isinstance(item, dict) or not str(item.get("ref") or "").strip():
            notes.append(f"interpretation[{i}]: ref yok, atlandı")
            continue
        take = _bool(item.get("take"))
        clean = {"ref": str(item["ref"]).strip()[:40], "meaning": str(item.get("meaning") or "").strip()[:TEXT_CLIP],
                 "take": True if take is None else take, "why": str(item.get("why") or "").strip()[:TEXT_CLIP]}
        if str(item.get("home_target") or "").strip():
            clean["home_target"] = str(item["home_target"]).strip()[:60]
        if take is None:
            notes.append(f"interpretation[{i}]: take true/false değil, true sayıldı")
        out.append(clean)
    return out, notes[:4]


def split(data: Any) -> tuple[Optional[list], Any, list[str]]:
    """``(interpretation | None, rules data, notes)`` of a parsed first-read reply. A reply without ``rules`` but with a map
    fragment (``pages`` / ``result`` / the page type) is taken as the rules (lenient)."""
    if not isinstance(data, dict):
        return None, data, []
    if "interpretation" not in data and "rules" not in data:
        return None, data, []
    items, notes = validate(data.get("interpretation"))
    rules = data.get("rules")
    if rules is None:
        rules = {k: v for k, v in data.items() if k != "interpretation"} or None
    return items, rules, notes


def resolve(items: list[dict], refs: dict) -> list[dict]:
    """Interpretation items with what their ref points at (block heading / menu link address) from the page maps."""
    out = []
    for item in items or []:
        target = refs.get(item.get("ref")) or {}
        extra = {}
        if target.get("kind") == "block":
            extra = {"heading": target.get("heading") or "", "page": target.get("url"), "selector": target.get("selector")}
        elif target.get("kind") == "menu":
            extra = {"text": target.get("text") or "", "href": target.get("href")}
        out.append({**item, **{k: v for k, v in extra.items() if v not in (None,)}})
    return out


def page_maps(pages: list) -> list[tuple[str, dict]]:
    """``[(url, page map)]`` of the sample pages; refs are made unique across pages (``p2.b3``)."""
    out = []
    for i, page in enumerate(pages[:2]):
        url = getattr(page, "final_url", None) or page.url
        pm = pagemap.build(page.html, url)
        if i:
            for b in pm.get("blocks") or []:
                b["ref"] = f"p{i + 1}.{b['ref']}"
            for links in (pm.get("menu") or {}).values():
                for link in links:
                    link["ref"] = f"p{i + 1}.{link['ref']}"
        out.append((url, pm))
    return out


def refs_of(maps: list[tuple[str, dict]]) -> dict:
    out: dict = {}
    for _url, pm in maps:
        out.update(pagemap.ref_index(pm))
    return out


def _handoff(ctx) -> str:
    h = ctx.handoff or {}
    if not h:
        return ""
    body = {k: v for k, v in h.items() if k in ("from", "urls", "listings", "title_cards", "episode_cards", "types",
                                                  "url_pattern", "title_urls")}
    return json.dumps(body, ensure_ascii=False, default=str)[:1500]


def _guess(finding) -> str:
    body = {"patch": finding.patch or {}, "summary": (finding.evidence or {}).get("summary")}
    for key in ("blocks", "found", "link_groups", "alternatives"):
        if (finding.evidence or {}).get(key):
            body[key] = finding.evidence[key]
    return json.dumps(body, ensure_ascii=False, default=str)[:GUESS_CLIP]


ERROR_CLIP = 2_000   # characters of the error a format fix shows (the full validator list, not a 400-character cut)


def downstream_text(ctx) -> str:
    """A hand-back's "A6 sonucu" (the later stage's measurement: failing rule, stage reached, error, per-sample trace)."""
    d = getattr(ctx, "downstream", None) or {}
    return json.dumps(d, ensure_ascii=False, default=str)[:3000] if d else ""


def read_prompt(stage, ctx, finding, hint: str, pages: list, maps: Optional[list] = None) -> str:
    """The first-read prompt of ``stage`` (``pages`` = its sample pages; ``maps`` = their page maps when already built)."""
    from .prompts_a1_a3 import FIELD_GRAMMAR, _rules
    maps = maps if maps is not None else page_maps(pages)
    player = stage.page_type == "player"
    evidence: dict[str, str] = {}
    if player:   # stable prefix (cache): the schema block right after the site note, the same block as the fix prompts
        from ...core.play import resolvers as play_resolvers
        evidence[play_resolvers.SCHEMA_TITLE] = play_resolvers.prompt_block()
    evidence["Site summary (earlier stages, the admin's labels and hints)"] = site_brief.brief(ctx.site_id)
    handoff = _handoff(ctx)
    if handoff and not player:   # A5 does not consume A4's handoff (its samples are episode pages it picks itself)
        evidence["Handed over by the previous stage"] = handoff
    down = downstream_text(ctx)
    if down:
        evidence["A6 sonucu (the next stage measured the current rule and it failed there; fix it)"] = down
    if hint:
        evidence["Admin hint (follow it)"] = hint
    for url, pm in maps:
        evidence[f"Page map of {url}"] = pagemap.render(pm, MAP_LIMIT)
    variety = url_variety(maps)
    if variety:
        evidence["URL variety (address shapes of the links on these pages, with counts)"] = variety
    evidence["The system's guess (LLM-free discovery; confirm or correct it)"] = _guess(finding)
    if pages:
        evidence[f"Clipped HTML of {pages[0].url}"] = htmlclip.clean_html(pages[0].html, HTML_CLIP, [])
    task = (f"PURPOSE ({stage.id} {stage.title}, site {ctx.base_url}): {PURPOSE.get(stage.id, stage.title)}\n"
            "First READ the page map(s) below: every section has a heading, every block shows its row selector, row count, "
            "link type and sample rows. Interpret each block / section / menu link for the purpose (take it or not, and "
            "why), then write the deterministic rules. The system will MEASURE your rules and its own guess with the same "
            "measurement and keep the better one.")
    rules = _rules(stage.page_type)
    if player:   # field rules (poster / fallback) do not apply to resolvers
        rules = [r for r in rules if not r.startswith(("Field rule", "If you are not sure about a field"))]
        rules.append("Resolver items carry only the parameters of the schema block above (player_page extract rules included).")
    rules = [r for r in rules if not r.startswith("URL'ler standart")]
    rules += [URL_HINT,
              "Use the refs of the page map (b<N>, m<N>) in the interpretation.",
              "Selectors must come from the page map / HTML of THIS site; a row_selector must match the rows of the blocks you take."]
    if not player:
        rules.append(FIELD_GRAMMAR)
    if stage.extra_keys:
        rules.append(f"rules may also hold the top-level key(s): {', '.join(stage.extra_keys)}.")
    answer = schema_text(stage) + "\n\nShort example:\n```yaml\n" + EXAMPLE.get(stage.id, _GENERIC_EXAMPLE) + "\n```"
    if getattr(stage, "investigates", False):
        from .investigate import ANSWER_HELP
        answer += ("\n\nIf the page map is not enough you may first ask for investigation steps instead (the rules then "
                   "come in your last answer, `rules:` may hold `result: {resolvers: [...]}`):\n" + ANSWER_HELP)
    text = prompts.build(task, rules, evidence, answer, evidence_limit=MAP_LIMIT + 200)
    if len(text) > MAX_PROMPT:   # the second page map goes first, then the HTML is clipped harder
        second = [k for k in evidence if k.startswith("Page map of")][1:]
        for key in second:
            evidence.pop(key, None)
        text = prompts.build(task, rules, evidence, answer, evidence_limit=MAP_LIMIT)
    return text[:MAX_PROMPT]


def format_fix(stage, error: str, types: Optional[list] = None) -> str:
    """The one format-fix request of a first read (same session): the FULL error (up to :data:`ERROR_CLIP`) + the reply
    schema; a player answer also gets the resolver schema block with the exact schema of the types concerned (the types
    of the reply + the ones the error names) - the same block as the first read."""
    parts = [f"Your last answer could not be used:\n{error_text(error)}", schema_text(stage)]
    if stage.page_type == "player":
        from ...core.play import resolvers as play_resolvers
        kinds = [t for t in (types or []) if t in play_resolvers.TYPES] + play_resolvers.types_in(error)
        parts += [play_resolvers.SCHEMA_TITLE + ":", play_resolvers.prompt_block(kinds)]
    parts += ["Short example:", "```yaml", EXAMPLE.get(stage.id, _GENERIC_EXAMPLE), "```"]
    return "\n".join(parts)


def error_text(error: str) -> str:
    """The validator's error list one per line, not cut mid-message (at most :data:`ERROR_CLIP` characters)."""
    items = [e.strip() for e in str(error or "-").split("; ") if e.strip()]
    out, used = [], 0
    for item in items:
        if used + len(item) > ERROR_CLIP:
            out.append(f"- (+{len(items) - len(out)} more)")
            break
        out.append("- " + item)
        used += len(item) + 3
    return "\n".join(out) or "-"


def match(items: list, *, heading: Optional[str] = None, href: Optional[str] = None) -> Optional[dict]:
    """The interpretation item about a review row: same block heading (folded), else the same menu link path."""
    from ...core.extract._util import fold, path_of
    key = fold(heading)
    if key:
        for item in items or []:
            if fold(item.get("heading")) == key:
                return item
    if href:
        want = path_of(href).rstrip("/") or "/"
        for item in items or []:
            if item.get("href") and (path_of(item["href"]).rstrip("/") or "/") == want:
                return item
    return None


def card_note(item: Optional[dict]) -> Optional[dict]:
    """The ``llm`` entry of a review row: ``{meaning, take, why, ref}``."""
    if not item:
        return None
    return {k: item.get(k) for k in ("ref", "meaning", "take", "why", "home_target") if k != "home_target" or item.get(k)}
