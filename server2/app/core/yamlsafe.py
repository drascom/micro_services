"""Safe yaml loading shared by every place that reads yaml from users / LLMs (recipes, site map import).

``yaml.safe_load`` still expands aliases: a few kB of nested ``*name`` references can grow to gigabytes, so aliases are
refused outright and the text is size-capped before parsing.
"""
from __future__ import annotations

from typing import Any, Optional

import yaml


class NoAliasLoader(yaml.SafeLoader):
    """safe_load without aliases (a few kB of nested aliases can expand to gigabytes)."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.events.AliasEvent):
            raise yaml.YAMLError("yaml aliases (*name) are not allowed")
        return super().compose_node(parent, index)


#: libyaml's safe loader when PyYAML was built with it (~10x faster; site maps are parsed many times per stage run)
_CLoader = getattr(yaml, "CSafeLoader", None)


def load(text: Any, *, max_bytes: Optional[int] = None) -> Any:
    """Parse ``text`` without aliases; ``yaml.YAMLError`` when it is longer than ``max_bytes`` or uses an alias."""
    text = text or ""
    if max_bytes is not None and len(text.encode("utf-8", "replace")) > max_bytes:
        raise yaml.YAMLError(f"yaml is longer than {max_bytes} bytes")
    if _CLoader is None:
        return yaml.load(text, Loader=NoAliasLoader)  # noqa: S506 - safe loader subclass without aliases
    # C parser: the alias check is an event scan first (aliases never reach the composer, so nothing expands)
    if any(isinstance(ev, yaml.events.AliasEvent) for ev in yaml.parse(text, Loader=_CLoader)):
        raise yaml.YAMLError("yaml aliases (*name) are not allowed")
    return yaml.load(text, Loader=_CLoader)  # noqa: S506 - safe C loader, aliases refused above
