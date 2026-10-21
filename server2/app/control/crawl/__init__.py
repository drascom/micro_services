"""Tiered crawl (T11): ``frontier`` (the page queue), ``process`` (one page -> catalogue), ``plan`` (K0 / K1 quota / K2),
``drift`` (structure change rule), ``run`` (jobs ``crawl``, ``refresh``, ``crawl_tick``, ``inventory``), ``state``
(per-site crawl state + clock), ``fetcher``. LLM-free: nothing here imports ``core.llm``."""
from __future__ import annotations


def register() -> None:
    """Job kinds of the crawl (called by ``control.stages.engine.register``: the crawl replaces K0)."""
    from . import run
    run.register()
