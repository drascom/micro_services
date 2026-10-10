"""Pull the answer out of an LLM reply: the first valid fenced block (or the whole text). Never raises."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import yaml

MAX_CHARS = 200_000
_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_-]*)[^\n]*\n(.*?)```", re.S)


class _NoAliasLoader(yaml.SafeLoader):
    """SafeLoader that refuses aliases (``*ref``): no billion-laughs expansion."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            event = self.get_event()
            raise yaml.composer.ComposerError(None, None, "aliases are not allowed", event.start_mark)
        return super().compose_node(parent, index)


def _blocks(text: str, langs: tuple[str, ...]) -> list[str]:
    out = [m.group(2) for m in _FENCE.finditer(text) if m.group(1).lower() in langs]
    return out


_PAGES_LINE = re.compile(r"^[ \t]*pages:[ \t]*$", re.M)


def _yaml_candidates(text: str) -> list[str]:
    """Fenced ``yaml``/``yml``/bare blocks, then the first fenced block of any language, then the text from the first
    ``pages:`` line on (prose before / after a missing fence), then the whole text."""
    out = list(_blocks(text, ("yaml", "yml", "")))
    anyblock = next((m.group(2) for m in _FENCE.finditer(text)), None)
    if anyblock is not None and anyblock not in out:
        out.append(anyblock)
    m = _PAGES_LINE.search(text)
    if m:
        tail = text[m.start():].split("```", 1)[0]
        lines = tail.splitlines()
        # stop at the first prose line after the yaml (a non-indented line that is not a key)
        keep = [lines[0]]
        for line in lines[1:]:
            if line.strip() and not line[:1].isspace() and not re.match(r"^[A-Za-z_][\w-]*:", line):
                break
            keep.append(line)
        out.append("\n".join(keep))
    out.append(text)
    return out


_DQ_STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
_YAML_ESCAPES = set("0abt\tnvfre \"/\\N_LPxuU")


def _bad_escape(body: str) -> bool:
    """A backslash followed by a character YAML does not accept in a double-quoted string (``\\d``, ``\\s``, ``\\.``)."""
    i = 0
    while i < len(body):
        if body[i] == "\\":
            if i + 1 >= len(body) or body[i + 1] not in _YAML_ESCAPES:
                return True
            i += 2
            continue
        i += 1
    return False


def repair_escapes(text: str) -> tuple[str, int]:
    """Double-quoted strings holding an invalid YAML escape (an LLM regex ``"^/(.+?)-(\\d+)"``) rewritten as single-quoted
    strings (the backslashes stay literal). ``(text, rewritten count)``."""
    count = 0

    def fix(match: re.Match) -> str:
        nonlocal count
        body = match.group(1)
        if not _bad_escape(body):
            return match.group(0)
        count += 1
        return "'" + body.replace("\\\"", '"').replace("'", "''") + "'"

    return _DQ_STRING.sub(fix, text), count


#: notes of the last ``extract_yaml`` call (e.g. the escape repair was used); read by callers that report it
LAST_NOTES: list[str] = []


def extract_yaml(text: Any) -> tuple[Optional[dict], Optional[str]]:
    """``(mapping, None)`` or ``(None, error)``; first fenced ``yaml``/``yml``/bare block that loads to a mapping,
    else the whole text. A block that fails only because of invalid escapes in double-quoted strings is repaired
    (``repair_escapes``) and loaded again; ``LAST_NOTES`` then says so."""
    LAST_NOTES.clear()
    data, error = _extract_yaml(text)
    if data is None and error == "yaml_error":
        fixed, count = repair_escapes(str(text or ""))
        if count:
            data, err2 = _extract_yaml(fixed)
            if data is not None:
                LAST_NOTES.append(f"yaml kaçış onarımı: {count} çift tırnaklı dize tek tırnağa çevrildi")
                return data, None
    return data, error


def _extract_yaml(text: Any) -> tuple[Optional[dict], Optional[str]]:
    text = str(text or "")
    if not text.strip():
        return None, "empty"
    if len(text) > MAX_CHARS:
        return None, "too_large"
    error = "no_yaml_mapping"
    for candidate in _yaml_candidates(text):
        try:
            data = yaml.load(candidate, Loader=_NoAliasLoader)  # noqa: S506 - safe loader subclass without aliases
        except yaml.YAMLError as exc:
            if error != "alias_rejected":
                error = "alias_rejected" if "aliases are not allowed" in str(exc) else "yaml_error"
            continue
        if isinstance(data, dict):
            return data, None
    return None, error


def extract_json(text: Any) -> tuple[Optional[Any], Optional[str]]:
    """``(object|array, None)`` or ``(None, error)``: fenced ``json`` blocks first, then the first ``{``/``[`` in the text."""
    text = str(text or "")
    if not text.strip():
        return None, "empty"
    if len(text) > MAX_CHARS:
        return None, "too_large"
    for candidate in _blocks(text, ("json", "")):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, (dict, list)):
            return data, None
    decoder = json.JSONDecoder()
    for m in re.finditer(r"[\[{]", text):
        try:
            data = decoder.raw_decode(text[m.start():])[0]
        except ValueError:
            continue
        if isinstance(data, (dict, list)):
            return data, None
    return None, "no_json"
