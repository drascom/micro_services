"""Selector derivation helpers shared by ``cards``, ``detail`` and ``episodes`` (pure)."""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from . import parse

SAFE = re.compile(r"^[A-Za-z0-9_-]+$")
_HASHY = re.compile(r"^(?:css|jsx|sc|svelte|ember|styled|chakra)[-_]?[A-Za-z0-9]{5,}$|^[A-Za-z]{0,3}[0-9a-f]{8,}$")
_BOLUM_TITLE = re.compile(r"^\s*\d+\s*\.?\s*(?:sezon\s*)?\d*\s*\.?\s*b[öo]l[üu]m\s*$", re.I)


def stable(cls: str) -> bool:
    return bool(SAFE.match(cls)) and not _HASHY.match(cls) and len(cls) <= 32


def sel(node) -> str:
    """``tag.class.class`` of a node (stable classes only, at most 3)."""
    classes = [c for c in (node.attributes.get("class") or "").split() if stable(c)][:3]
    return node.tag + "".join("." + c for c in classes)


def unique_selector(tree, node) -> Optional[str]:
    """A selector whose FIRST match on the page is ``node``."""
    ident = node.attributes.get("id") or ""
    own = sel(node)
    candidates = ([f"#{ident}"] if ident and SAFE.match(ident) else []) + [own]
    parent, depth = node.parent, 0
    while parent is not None and depth < 4 and parent.tag not in ("html", "body", "-undef"):
        pid = parent.attributes.get("id") or ""
        candidates.append(f"{'#' + pid if pid and SAFE.match(pid) else sel(parent)} {own}")
        parent, depth = parent.parent, depth + 1
    for selector in candidates:
        try:
            first = tree.css_first(selector)
        except Exception:
            continue
        if first is not None and first.mem_id == node.mem_id:
            return selector
    return None


def rel_candidates(card, node, extra: tuple = ()) -> list[str]:
    own = sel(node)
    out = [own]
    if own != node.tag:
        out.append(node.tag)
    parent, depth = node.parent, 0
    while parent is not None and parent.mem_id != card.mem_id and depth < 4:
        label = sel(parent)
        if label != parent.tag:
            out.append(f"{label} {own}")
        parent, depth = parent.parent, depth + 1
    out.extend(extra)
    return list(dict.fromkeys(out))


def find_selector(cards: list, target_of: Callable, key: Callable = lambda n: n.mem_id, extra: tuple = ()) -> Optional[str]:
    """The simplest selector that, evaluated inside each card, yields the same ``key`` as ``target_of(card)`` for >= 90% of the
    cards that have a target (targets exist in at least half of the cards)."""
    have = [(c, t) for c, t in ((c, target_of(c)) for c in cards) if t is not None]
    if not have or len(have) < max(1, len(cards) * 0.5):
        return None
    first_card, first_target = have[0]
    for selector in rel_candidates(first_card, first_target, extra):
        agree = 0
        for card, target in have:
            try:
                got = card.css_first(selector)
            except Exception:
                got = None
            if got is not None and key(got) == key(target):
                agree += 1
        if agree >= 0.9 * len(have):
            return selector
    return None


def class_key(node) -> tuple:
    return (node.tag, tuple(sorted((node.attributes.get("class") or "").split())))


def pick_like(card, model_card, model_node):
    """The element of ``card`` that sits where ``model_node`` sits in ``model_card`` (same tag + classes at every level)."""
    path = []
    cur = model_node
    while cur is not None and cur.mem_id != model_card.mem_id:
        path.append(class_key(cur))
        cur = cur.parent
    path.reverse()
    nodes = [card]
    for key in path:
        nxt = []
        for node in nodes:
            child = node.child
            while child is not None:
                if class_key(child) == key:
                    nxt.append(child)
                child = child.next
        nodes = nxt
        if not nodes:
            return None
    return nodes[0]


def title_like(value: Any) -> bool:
    text = " ".join(str(value or "").split())
    return 2 <= len(text) <= 140 and not re.fullmatch(r"[\d\s.:/-]+", text) and not _BOLUM_TITLE.match(text)


def is_leaf(node) -> bool:
    child = node.child
    while child is not None:
        if not child.tag.startswith("-"):
            return False
        child = child.next
    return True


def _filled(value: Any) -> bool:
    return value not in (None, "", [])


def fill(cards: list, spec: dict, ok: Callable = _filled) -> float:
    return sum(1 for card in cards if ok(parse.apply_field(card, spec))) / len(cards) if cards else 0.0


def inside(node) -> list:
    """The elements below ``node`` (selectolax ``css`` also returns the node itself)."""
    return [n for n in node.css("*") if n.mem_id != node.mem_id]
