"""Pure-python Dean Edwards p.a.c.k.e.r. unpacker (``eval(function(p,a,c,k,e,d){...}('payload',radix,count,'a|b'.split('|'),0,{}))``).

Player pages often ship their JW Player / Clappr setup as packed JavaScript; the media URL only exists after the
dictionary substitution. This never EXECUTES anything: the payload is rebuilt by replacing every base-N word with its
dictionary entry (the same thing the packer's own ``function(p,a,c,k,e,d)`` does), so unpacking untrusted pages is
safe. Bounded input / depth; anything that does not look right returns no text instead of raising.
"""
from __future__ import annotations

import re

MAX_SOURCE = 3_000_000   # characters scanned for packed blocks
MAX_BLOCKS = 6           # packed blocks unpacked per call (and per nesting level)
MAX_DEPTH = 3            # a packed block that unpacks to another packed block

_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"   # base-62 (the packer's own alphabet)
_VALUE = {char: index for index, char in enumerate(_DIGITS)}
_STRING = r"""(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")"""
_START = re.compile(r"eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*[dr]\s*\)")
_ARGS = re.compile(r"\}\s*\(\s*" + _STRING + r"\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*" + _STRING
                   + r"\s*\.\s*split\s*\(\s*['\"]\|['\"]\s*\)", re.S)
_WORD = re.compile(r"\b\w+\b")


def _unescape(text: str) -> str:
    """Undo the JS string escaping of the packer's quoted arguments."""
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "r": "\r", "t": "\t"}.get(m.group(1), m.group(1)), text, flags=re.S)


def _decode(word: str, radix: int):
    """Value of ``word`` written in base ``radix`` (digits 0-9 a-z A-Z); None when it is not such a number."""
    value = 0
    for char in word:
        digit = _VALUE.get(char)
        if digit is None or digit >= radix:
            return None
        value = value * radix + digit
    return value


def _unpack_block(payload: str, radix: int, count: int, words: list[str]) -> str:
    if not 2 <= radix <= 62 or count < 0:
        return ""

    def swap(match):
        word = match.group(0)
        index = _decode(word, radix)
        if index is not None and index < len(words) and words[index]:
            return words[index]
        return word

    return _WORD.sub(swap, payload)


def find_packed(text: str) -> list[str]:
    """Every packed block of ``text`` unpacked once (nested blocks are NOT unpacked here; see :func:`unpack`)."""
    text = (text or "")[:MAX_SOURCE]
    out: list[str] = []
    position = 0
    while len(out) < MAX_BLOCKS:
        start = _START.search(text, position)
        if start is None:
            break
        args = _ARGS.search(text, start.end())
        if args is None:
            break
        position = args.end()
        payload = _unescape(args.group(1) if args.group(1) is not None else args.group(2) or "")
        keys = _unescape(args.group(5) if args.group(5) is not None else args.group(6) or "")
        unpacked = _unpack_block(payload, int(args.group(3)), int(args.group(4)), keys.split("|"))
        if unpacked:
            out.append(unpacked)
    return out


def unpack(text: str) -> list[str]:
    """The unpacked JavaScript of every p.a.c.k.e.r. block in ``text`` (empty list when there is none). A block that
    unpacks to another packed block is unpacked again (up to :data:`MAX_DEPTH` levels, the inner result is returned)."""
    level = find_packed(text)
    for _ in range(MAX_DEPTH - 1):
        deeper = [inner for block in level for inner in find_packed(block)]
        if not deeper:
            break
        level = deeper
    return level


def pack(source: str, words: list[str], radix: int = 62) -> str:
    """Test helper: a p.a.c.k.e.r. block of ``source`` where every ``\\w+`` word that is in ``words`` becomes its
    base-``radix`` index (the real packer's output format, enough to round-trip :func:`unpack`)."""
    def encode(number: int) -> str:
        head = "" if number < radix else encode(number // radix)
        return head + _DIGITS[number % radix]

    index = {word: position for position, word in enumerate(words)}
    payload = _WORD.sub(lambda m: encode(index[m.group(0)]) if m.group(0) in index else m.group(0), source)
    escaped = payload.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    return ("eval(function(p,a,c,k,e,d){while(c--)if(k[c])p=p.replace(new RegExp('\\\\b'+e(c)+'\\\\b','g'),k[c]);return p}"
            "('%s',%d,%d,'%s'.split('|'),0,{}))" % (escaped, radix, len(words), "|".join(words)))
