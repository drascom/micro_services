"""``script_regex``: URLs hidden in the page's inline scripts (``atob('...')``, ``\\x68..`` hex, p.a.c.k.e.r., ``file:``/
``sources:`` object literals, ``decodeURIComponent``) -> one candidate per match, after optional decode steps."""
from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from . import _decode, _unpack, _util as util
from .data_attr import candidate_of

NAME = "script_regex"
DESCRIPTION = ("Searches the page's inline <script> texts (optionally after p.a.c.k.e.r. unpacking) with a regex; the capture "
               "group (decoded with decode: base64 | hex | urldecode | json | reverse) is a provider or media URL. One candidate "
               "per match (max 12). For URLs written as atob('...'), '\\x68\\x74..', sources:[{file:'...'}] or an encoded string.")
PARAMS = {
    "regex": {"type": "str", "required": True, "default": None,
              "help": "Regex over each inline script (search, all matches); the capture group 'group' is the value."},
    "group": {"type": "int", "required": False, "default": 1, "help": "Capture group holding the value (default 1)."},
    "decode": {"type": "list", "required": False, "default": [],
               "help": "Decode steps applied to the value in order: base64, hex, urldecode, json, reverse."},
    "unpack": {"type": "bool", "required": False, "default": False,
               "help": "true = search the unpacked text of p.a.c.k.e.r. blocks (eval(function(p,a,c,k,e,d)...)) too."},
    "host_regex": {"type": "str", "required": False, "default": None,
                   "help": "Keep only URLs whose whole hostname matches this regex (full match, case-insensitive)."},
    "label": {"type": "str", "required": False, "default": None, "help": "Constant candidate label (default: the host)."},
    "lang": {"type": "str", "required": False, "default": None, "help": "Constant language code, e.g. 'tr'."},
}
MAX_CANDIDATES = 12
MAX_SCRIPT = 400_000


def check(params: dict) -> list[str]:
    errors = _decode.check_steps(params.get("decode"))
    regex = params.get("regex")
    if isinstance(regex, str):
        try:
            compiled = re.compile(regex)
        except re.error as exc:
            return errors + [f"parameter 'regex': invalid regex ({exc})"]
        group = params.get("group") or 1
        if isinstance(group, int) and group > compiled.groups:
            errors.append(f"parameter 'group': the regex has only {compiled.groups} group(s)")
    return errors


def script_texts(html: str, unpack: bool = False) -> list[str]:
    """Every inline script text of the page (+ the unpacked p.a.c.k.e.r. blocks when ``unpack``)."""
    out = []
    for node in HTMLParser(html or "").css("script"):
        if node.attributes.get("src"):
            continue
        text = (node.text() or "")[:MAX_SCRIPT]
        if text.strip():
            out.append(text)
            if unpack:
                out.extend(_unpack.unpack(text))
    return out


@util.guard([])
def discover(ctx, html, page_url, params):
    p = util.with_defaults(PARAMS, params)
    pattern = re.compile(p["regex"])
    group = int(p["group"] or 1)
    seen: set[str] = set()
    found: list[dict] = []
    for text in script_texts(html, bool(p["unpack"])):
        for match in pattern.finditer(text):
            value = _decode.chain(match.group(group) or "", p["decode"])
            url = util.absolute(page_url, value.strip().replace("\\/", "/")) if value else ""
            if not url or url in seen or not util.host_matches(url, p["host_regex"]):
                continue
            seen.add(url)
            cand = candidate_of(url, str(p["label"] or "") or util.host_of(url))
            if p["lang"]:
                cand["lang"] = str(p["lang"]).strip().lower()
            found.append(cand)
            if len(found) >= MAX_CANDIDATES:
                return found
    return found


resolve_candidate = util.passthrough
