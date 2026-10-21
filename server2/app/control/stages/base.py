"""Stage contract (README §5.6): ``Stage`` with ``discover`` (LLM-free), ``prompt`` (one page type) and ``verify``
(LLM-free measurement), plus the value objects the engine persists between job steps."""
from __future__ import annotations

import copy
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from ...core.fetch import FetchError, Page, SiteCtx
from ..sitemap import meta as map_meta

MAX_EVIDENCE_ITEMS = 12


@dataclass
class Finding:
    """A candidate patch (map-shaped: ``{pages: {<page_type>: ...}}``) + what discovery looked at / found / missed."""
    patch: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    confidence: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Finding":
        data = data or {}
        return cls(patch=data.get("patch") or {}, evidence=data.get("evidence") or {},
                   confidence=float(data.get("confidence") or 0.0))


@dataclass
class Measurement:
    """``checks = [{name, value, bound, op, ok, optional}]``, ``samples = [{url, ok, note}]``. ``ok`` = every required
    check passed; ``warn`` = an optional one failed."""
    checks: list = field(default_factory=list)
    samples: list = field(default_factory=list)
    ok: bool = False
    warn: bool = False
    notes: list = field(default_factory=list)   # short Turkish lines for the stage card / message history

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Measurement":
        data = data or {}
        return cls(checks=list(data.get("checks") or []), samples=list(data.get("samples") or []),
                   ok=bool(data.get("ok")), warn=bool(data.get("warn")), notes=list(data.get("notes") or []))

    def failed(self, *, optional: Optional[bool] = None) -> list[dict]:
        return [c for c in self.checks if not c.get("ok") and (optional is None or bool(c.get("optional")) == optional)]

    def brief(self) -> str:
        """One line: failed checks (``name=value (bound)``) or ``tüm denetimler geçti``."""
        bad = self.failed()
        if not bad:
            return "tüm denetimler geçti"
        return "; ".join(f"{c['name']}={c.get('value')} ({'>=' if c.get('op') == 'min' else '<='}{c.get('bound')})"
                         for c in bad[:8])


@dataclass
class StageCtx:
    """What a stage sees: the site's draft map (a copy), fetch settings, the user's hint and sample addresses, a fetch
    function (``fetch(url, page_type) -> Page``, raises ``FetchError``) and a deadline (epoch seconds)."""
    site_id: str
    map: dict
    site_ctx: SiteCtx
    hint: str = ""
    samples: list = field(default_factory=list)
    fetch: Optional[Callable[..., Page]] = None
    deadline: float = 0.0
    errors: list = field(default_factory=list)   # [{url, code}] of failed fetches (evidence)
    repair: bool = False                          # T14 repair mode: nothing global (provider library) is written while measuring
    excluded: list = field(default_factory=list)  # T18: sample addresses the admin removed (never measured / proposed)
    hints: list = field(default_factory=list)     # T18: this and the earlier admin hints of the stage (discovery reads them)
    pinned: list = field(default_factory=list)    # T18: the admin's own sample addresses (``samples`` = these + the handoff)
    handoff: dict = field(default_factory=dict)   # T18: what the previous stage of the chain handed over ({from, urls, ...})
    note: str = ""                                # the admin's site note (sitemap.site_notes; discovery reads it LLM-free)
    reading: dict = field(default_factory=dict)   # T23: the LLM's first read of this run ({interpretation, source})
    own_only: bool = False                        # T25: "Yalnız benim örneklerim": only ``pinned`` addresses are samples
    downstream: dict = field(default_factory=dict)  # a hand-back: the later stage's result ("A6 sonucu"), not an admin hint

    def allowed(self, url: str, own: bool = True) -> bool:
        """Not a sample the admin excluded (trailing slash ignored); with ``own_only`` only the admin's own samples
        (``own=False``: an address DERIVED from an allowed sample, e.g. the series page of a given episode)."""
        norm = (url or "").rstrip("/")
        if not all(norm != (u or "").rstrip("/") for u in self.excluded):
            return False
        return not (own and self.own_only) or any(norm == (u or "").rstrip("/") for u in self.pinned)

    def pool(self, urls) -> list:
        """``urls`` without duplicates and without the addresses ``allowed`` refuses (every sample source goes through it:
        the handoff, ``meta`` samples re-derived from earlier stages, discovery)."""
        return [u for u in dict.fromkeys(u for u in urls if u) if self.allowed(u)]

    @property
    def base_url(self) -> str:
        return str(self.map.get("base_url") or self.site_ctx.base_url or "")

    def expired(self) -> bool:
        return bool(self.deadline) and time.time() >= self.deadline

    def get(self, url: str, page_type: str = "page") -> Optional[Page]:
        """The page or ``None`` (the error is kept in ``errors``). Only the site's own host is fetched (a card link or an
        LLM patch pointing elsewhere -> ``foreign_host``; a redirect off the site -> ``foreign_redirect``)."""
        if self.fetch is None:
            return None
        from ..sitemap.classify import same_site
        base = self.base_url
        if base and not same_site(url, base):
            self.errors.append({"url": url, "code": "foreign_host"})
            return None
        try:
            page = self.fetch(url, page_type)
            if base and page is not None and page.final_url and not same_site(page.final_url, base):
                self.errors.append({"url": url, "code": "foreign_redirect"})
                return None
            return page
        except FetchError as exc:
            self.errors.append({"url": url, "code": exc.code})
        except Exception as exc:   # a broken page never breaks the stage
            self.errors.append({"url": url, "code": f"error:{type(exc).__name__}"})
        return None

    def page(self, page_type: str) -> dict:
        return copy.deepcopy((self.map.get("pages") or {}).get(page_type) or {})

    def merged(self, patch: Optional[dict]) -> dict:
        out = copy.deepcopy(self.map)
        merge(out, patch or {})
        return out


def merge(target: dict, patch: dict) -> None:
    """Map merge (same rules as ``sitemap.store.apply_patch``): ``None`` deletes, dicts merge, the rest replaces."""
    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def judge(site_map: dict, page_type: str, values: dict[str, Any], *, extra: Optional[dict] = None) -> list[dict]:
    """Checks from the map's effective thresholds (``sitemap.meta.expect``: defaults + ``meta.<type>.expect`` - skipped
    fields) applied to the measured ``values``; a threshold without a value is reported as failed (value ``None``)."""
    rules = map_meta.expect(site_map, page_type)
    if extra:
        rules = {**extra, **rules}
    checks = []
    for name, rule in rules.items():
        value = values.get(name)
        bound = rule.get("bound")
        op = rule.get("op", "min")
        ok = value is not None and (value >= bound if op == "min" else value <= bound)
        checks.append({"name": name, "value": value, "bound": bound, "op": op, "ok": bool(ok),
                       "optional": bool(rule.get("optional")), **({"field": rule["field"]} if rule.get("field") else {}),
                       **({"coverage": True} if rule.get("coverage") else {})})
    return checks


REPAIR_NOTE = "{n} örnekten {good}'i çalıştı; kalanlar onarımda ele alınır"


def one_sample_enough(checks: list[dict], samples: list[dict], notes: list[str]) -> list[str]:
    """User decision ("1 çalışan örnek yeter"): with >= 1 working sample a failed ``coverage`` check (sample count, a
    per-sample ratio) only warns; the failing samples are left to the repair (auto heal). 0 working -> nothing changes.
    Returns the addresses of the failing samples when a check was relaxed."""
    rows = [s for s in samples or [] if isinstance(s, dict)]
    good = sum(1 for s in rows if s.get("ok"))
    relaxed = [c for c in checks if c.get("coverage") and not c["ok"] and not c.get("optional")]
    if not good or not relaxed:
        return []
    for c in relaxed:
        c.update(optional=True, relaxed=True)
    notes.append(REPAIR_NOTE.format(n=max(len(rows), good), good=good))
    return [str(s["url"]) for s in rows if not s.get("ok") and s.get("url")]


def measurement(checks: list[dict], samples: list[dict], notes: list[str]) -> Measurement:
    one_sample_enough(checks, samples, notes)
    required_ok = all(c["ok"] for c in checks if not c.get("optional"))
    warn = any(not c["ok"] for c in checks if c.get("optional"))
    return Measurement(checks=checks, samples=samples[:MAX_EVIDENCE_ITEMS], ok=required_ok, warn=warn and required_ok,
                       notes=notes)


class Stage:
    """One onboarding stage for ONE page type. Subclasses set the class attributes and implement the three methods."""
    id: str = ""
    page_type: str = ""
    title: str = ""
    needs: tuple = ()
    optional: bool = False
    #: fields that can never be skipped on this stage (``meta.NEVER_SKIPPED`` + stage specific)
    never_skip: tuple = ("url", "title")

    def discover(self, ctx: StageCtx) -> Finding:
        raise NotImplementedError

    def prompt(self, ctx: StageCtx, finding: Finding, hint: str, last: Optional[Measurement]) -> str:
        raise NotImplementedError

    def verify(self, ctx: StageCtx, patch: dict) -> Measurement:
        raise NotImplementedError

    # --- optional hooks (T09; the defaults keep the A1-A3 behaviour) ---------------------------------------------
    #: top-level map keys the stage may write besides ``pages.<page_type>`` (A7: ``gate``)
    extra_keys: tuple = ()
    #: LLM rounds of one run (A6: one recipe call)
    max_llm_rounds: int = 2

    def escalate(self, ctx: StageCtx, finding: Finding, m: Measurement) -> Optional[tuple]:
        """After a failed discovery measurement: ``None`` = ask the LLM; ``("needs_human", "")`` / ``("engine_gap", why)``
        = stop without an LLM call."""
        return None

    def parse_reply(self, ctx: StageCtx, data: Any) -> Optional[tuple]:
        """``(patch, error)`` of a parsed LLM yaml answer, or ``None`` = the engine's own scope check (``pages.<type>``)."""
        return None

    def on_passed(self, ctx: StageCtx, patch: dict, m: Measurement) -> None:
        """Side work after the stage passed and its patch is in the draft (queue follow-up jobs ...)."""

    def review(self, ctx: StageCtx, patch: dict, ask: bool) -> tuple:
        """``(patch, review)`` before a passing patch is written: ``review`` (A1) = items the admin must label first (the stage
        waits in ``review``); ``None`` = write now."""
        return patch, None

    def apply_review(self, patch: dict, labelled: dict, site: dict) -> dict:
        """The held patch with the admin's review labels applied (``ValueError`` = refused input)."""
        return patch

    def apply_extras(self, site_id: str, patch: dict) -> None:
        """Repair mode (T14): what the run measured outside the map (A6: provider recipes) is saved only here, when the engine
        applies the repair (or the admin approves it)."""

    #: T18 chain: the previous stage's handoff ``urls`` are this stage's first samples (after the admin's own); stages whose
    #: input pages are derived differently (A5-A7 read the episode / player samples from ``meta``) switch it off
    consumes_handoff: bool = True

    def handoff(self, ctx: StageCtx, patch: dict, m: Measurement) -> dict:
        """What this passed stage hands the next one (``urls`` = the next stage's sample addresses). Default: the samples
        that measured fine."""
        return {"urls": [s["url"] for s in m.samples if s.get("ok") and s.get("url")][:map_meta.MAX_SAMPLES]}

    # --- LLM-first (T23) --------------------------------------------------------------------------------------------------
    #: the run starts with the LLM's own read of the stage's pages (purpose + page map), measured against discovery
    reads_first: bool = False

    def read_pages(self, ctx: StageCtx, finding: Finding, last: Optional[Measurement] = None) -> list:
        """The sample pages the LLM reads (first read and the fix rounds; at most 2 are used)."""
        return []

    #: handoff keys that say WHAT a rule set found (the referee compares them: same = one result, else two candidates)
    OUTCOME_KEYS = ("urls", "types", "listings", "title_cards", "episode_cards", "title_urls")

    def outcome(self, ctx: StageCtx, patch: dict, m: Measurement) -> Optional[dict]:
        """What ``patch`` finds, order-free (default: its handoff's address lists); ``None`` = not comparable."""
        handoff = self.handoff(ctx, patch, m) or {}
        out = {}
        for key in self.OUTCOME_KEYS:
            value = handoff.get(key)
            if isinstance(value, list):
                out[key] = sorted(str(v) for v in value)
            elif isinstance(value, dict):
                out[key] = {str(k): str(v) for k, v in sorted(value.items())}
        return out

    def meta_for(self, ctx: StageCtx, patch: dict, m: Measurement) -> dict:
        """``meta.<page_type>`` written with a passing patch (default: the samples that measured fine)."""
        good = [s["url"] for s in m.samples if s.get("ok") and s.get("url")]
        return {"samples": good[:map_meta.MAX_SAMPLES]} if good else {}
