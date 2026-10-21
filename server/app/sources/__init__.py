"""Catalogue source adapters.

Adding a new source = drop one module in this package that exposes a
``SourceAdapter`` subclass, then set ``SOURCE=<name>`` in ``.env``.
"""
from __future__ import annotations

import importlib
import inspect

from .base import SourceAdapter

_cache: dict[str, SourceAdapter] = {}


def get_source(name: str) -> SourceAdapter:
    """Instantiate (and memoise) the adapter module called ``name``."""
    if name in _cache:
        return _cache[name]
    try:
        module = importlib.import_module(f"{__name__}.{name}")
    except ModuleNotFoundError as exc:  # pragma: no cover - config error path
        raise ValueError(f"unknown SOURCE '{name}'") from exc

    for _, obj in vars(module).items():
        if (
            inspect.isclass(obj)
            and issubclass(obj, SourceAdapter)
            and obj is not SourceAdapter
            and obj.__module__ == module.__name__
        ):
            adapter = obj()
            _cache[name] = adapter
            return adapter
    raise ValueError(f"source module '{name}' defines no SourceAdapter subclass")
