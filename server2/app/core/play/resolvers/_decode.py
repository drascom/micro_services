"""Decode steps shared by the resolver types, the first-layer source finder and the LLM investigation (``decode`` action).

A step never EXECUTES anything: base64 / hex / percent decoding, JSON string unescaping, p.a.c.k.e.r. dictionary substitution
(:mod:`._unpack`), string reversal. A chain is a list of step names applied in order; any step that does not fit returns
``""`` (the caller then has no URL, never an exception).
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from urllib.parse import unquote

from . import _unpack

STEPS = ("base64", "hex", "urldecode", "json", "packed", "reverse")
MAX_TEXT = 200_000
_HEX_ESC = re.compile(r"\\x([0-9a-fA-F]{2})")
_UNI_ESC = re.compile(r"\\u([0-9a-fA-F]{4})")
_URL = re.compile(r"""https?:(?:\\?/){2}[^\s"'<>\\]+""")
_MEDIA = re.compile(r"\.(m3u8|mp4|webm)(?:[?#]|$)", re.I)


def b64(text: str) -> str:
    raw = re.sub(r"\s+", "", text or "")
    if not raw or len(raw) > MAX_TEXT:
        return ""
    raw = raw.replace("-", "+").replace("_", "/")
    raw += "=" * (-len(raw) % 4)
    try:
        return base64.b64decode(raw, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return ""


def hexdecode(text: str) -> str:
    text = (text or "")[:MAX_TEXT]
    if "\\x" in text:
        return _HEX_ESC.sub(lambda m: chr(int(m.group(1), 16)), text)
    if "\\u" in text:
        return _UNI_ESC.sub(lambda m: chr(int(m.group(1), 16)), text)
    plain = re.sub(r"\s+", "", text)
    if not plain or len(plain) % 2 or not re.fullmatch(r"[0-9a-fA-F]+", plain):
        return ""
    try:
        return bytes.fromhex(plain).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""


def jsonstr(text: str) -> str:
    """A JSON string literal (with or without its quotes) unescaped: ``https:\\/\\/x`` -> ``https://x``."""
    text = (text or "").strip()[:MAX_TEXT]
    body = text if text.startswith('"') and text.endswith('"') else '"' + text.replace('"', '\\"') + '"'
    try:
        value = json.loads(body)
    except ValueError:
        return text.replace("\\/", "/")
    return value if isinstance(value, str) else ""


def step(name: str, text: str) -> str:
    """One decode step; ``""`` when it does not apply."""
    if name == "base64":
        return b64(text)
    if name == "hex":
        return hexdecode(text)
    if name == "urldecode":
        return unquote(text or "")
    if name == "json":
        return jsonstr(text)
    if name == "packed":
        return "\n".join(_unpack.unpack(text or ""))
    if name == "reverse":
        return (text or "")[::-1]
    return ""


def chain(text: str, steps) -> str:
    out = text or ""
    for name in steps or []:
        out = step(str(name), out)
        if not out:
            return ""
    return out


def check_steps(steps) -> list[str]:
    if steps is None:
        return []
    if not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
        return ["parameter 'decode' must be a list of step names"]
    bad = [s for s in steps if s not in STEPS]
    return [f"parameter 'decode': unknown step '{s}' (allowed: {', '.join(STEPS)})" for s in bad[:2]]


def first_url(text: str) -> str:
    """The first http(s) URL inside ``text`` (escaped ``\\/`` undone); ``""`` when there is none."""
    hit = _URL.search(text or "")
    return hit.group(0).replace("\\/", "/") if hit else ""


def media_type(url: str) -> str:
    """``hls`` / ``mp4`` for a URL that is a media file by its extension, else ``""``."""
    hit = _MEDIA.search(url or "")
    if not hit:
        return ""
    return "hls" if hit.group(1).lower() == "m3u8" else "mp4"
