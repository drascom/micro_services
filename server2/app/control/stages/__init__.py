"""Stage engine (T08/T09, README §5.6): a site is learnt stage by stage (A1 home, A2 listing, A3 title, ...); each stage
discovers LLM-free, measures, asks the LLM at most twice, and stops for a human when it cannot pass.

Modules: ``base`` (contract), ``engine`` (job kind ``stage``), ``actions`` (admin actions), ``status`` (stage cards),
``brief`` (site brief for prompts), ``k0_sources`` (playback rows of one title), ``a1_home`` / ``a2_listing`` / ``a3_title``."""


def register() -> None:
    """Startup (``control.wiring``): job kinds ``stage`` + the crawl's (T11)."""
    from . import engine
    engine.register()
