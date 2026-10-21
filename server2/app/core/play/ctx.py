"""``PlayCtx``: everything ``core.play`` needs to know about a site (instead of the old ``SiteConfig``).

Built by the site map adapter (T07, ``control.sitemap.adapters``) from ``pages.player.resolvers``, ``sources.providers`` and
``gate``. The provider library never sees it (K4): providers get a provider URL + Referer only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class PlayCtx:
    site_id: str
    base_url: str
    resolvers: list = field(default_factory=list)          # [{type, ...params}] (site map pages.player.resolvers)
    providers_allow: Optional[list] = None                 # provider names allowed / ordered (None = all)
    gate: dict = field(default_factory=dict)               # {require, probe, blocked: [...]} (site map gate)
    fetch: Any = None                                      # playback transport (default: core.play.transport)
    mode: str = "auto"                                     # site map fetch.mode for the site's own pages (extra field)
    alternatives: list = field(default_factory=list)       # A6 order: [{key, status}] (site map pages.sources.alternatives)
    cookies: list = field(default_factory=list)            # T32: site cookie specs [{name, value|now_ms|now_s}] (map fetch.cookies)
    jar: Any = None                                        # T32: this operation's session cookie jar (core.fetch.cookies.Jar)

    def __post_init__(self) -> None:
        if self.fetch is None:
            from . import transport
            self.fetch = transport
        self.resolvers = list(self.resolvers or [])
        self.gate = dict(self.gate or {})
        self.alternatives = [a for a in (self.alternatives or []) if isinstance(a, dict) and a.get("key")]
        self.cookies = list(self.cookies or [])
        if self.jar is None:   # one jar per ctx = per resolution / measurement operation (in memory only)
            from urllib.parse import urlsplit
            from ..fetch.cookies import Jar
            self.jar = Jar(self.cookies, (urlsplit(self.base_url or "").hostname or "").lower())


def make_ctx(site_id: str = "", base_url: str = "", resolvers=None, providers_allow=None, gate=None, fetch=None,
             mode: str = "auto", alternatives=None, cookies=None) -> PlayCtx:
    return PlayCtx(site_id=site_id or "", base_url=base_url or "", resolvers=list(resolvers or []),
                   providers_allow=providers_allow, gate=dict(gate or {}), fetch=fetch, mode=mode or "auto",
                   alternatives=list(alternatives or []), cookies=list(cookies or []))
