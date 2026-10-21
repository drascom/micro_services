"""Self-heal via an LLM that rewrites broken selectors — never at parse time.

Default provider is the **codex CLI** (subscription-authed; no SDK, no API key).
Any proposal is validated against the SAME HTML in a sandbox before it can be
saved: parse -> schema validate -> re-check drift against baseline. Only a
proposal that clears the baseline is versioned and (optionally) activated.

Every failure path degrades gracefully: the caller keeps serving the old config.

``SCRAPER_HEAL_PROVIDER=pi_agent`` hands the same drift to an agent instead (``heal_agent.drift_generate``: the onboarding
skill in repair mode, sandbox tools, ``submit_repair``); its answer goes through the SAME gates below. ``heal_site_playback``
is the other entry of the agent: repeating playback failures / a site without episode sources (``playheal.py``), where the
agent may also fix the provider library (``heal_agent.py`` has the gates).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from typing import Any, Optional

import yaml

from . import config as scfg
from . import drift, parse, schema


#: Model used with the ``pi`` provider when SCRAPER_HEAL_MODEL is unset.
PI_DEFAULT_MODEL = "openai-codex/gpt-5.6-terra"


def _env(name: str, default: str) -> str:
    return (os.environ.get(name) or default).strip()


def _enabled() -> bool:
    return _env("SCRAPER_HEAL_ENABLED", "false").lower() in ("1", "true", "yes")


def _autoapply() -> bool:
    """Read per heal (not cached): the admin panel can flip it without a restart.
    ``SCRAPER_HEAL_AUTOAPPLY`` is only the default until an admin value is saved."""
    from .. import settings  # local import: settings imports scraper.config
    return settings.heal_autoapply()


def _int_env(name: str, default: int) -> int:
    """Positive int from env; invalid/non-positive values fall back to ``default``."""
    try:
        v = int(float(_env(name, str(default))))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


# One vocabulary for heal outcomes (runner state history AND ops heal history).
FIXED, NOT_APPLIED, FAILED, SKIPPED_COOLDOWN = "fixed", "not_applied", "failed", "skipped_cooldown"

#: A sandbox result may not fall more than this below the last known-good parse.
FILL_TOLERANCE = 0.2          # aggregate key-field fill ratio
FIELD_FILL_TOLERANCE = 0.3    # any single configured field


def _fail(reason: str, **extra: Any) -> dict:
    return {"status": "heal_failed", "reason": reason, **extra}


def field_fill(raw: list[dict], names: Any) -> dict[str, float]:
    """Fill ratio (0..1) per configured field over raw parsed rows."""
    n = len(raw)
    return {f: (round(sum(1 for it in raw if schema._filled(it.get(f))) / n, 3) if n else 0.0)
            for f in names}


def _check_structure(before: dict, after: Any) -> Optional[str]:
    """Reject proposals that drop fields or break a field spec's shape."""
    if not isinstance(after, dict):
        return "proposal 'fields' is not a mapping"
    dropped = sorted(set(before) - set(after))
    if dropped:
        return f"proposal drops fields: {dropped}"
    for name, spec in after.items():
        if not isinstance(spec, dict) or not (spec.get("selector") or spec.get("fallback") or spec.get("self")):
            return f"field {name!r} has no usable selector"
    for name, old in before.items():
        if not isinstance(old, dict):
            continue
        new = after[name]
        if "fallback" in old or "fallback" in new:
            continue
        for key in ("attr", "cast", "all"):
            if bool(old.get(key)) != bool(new.get(key)):
                return f"field {name!r}: '{key}' changed ({old.get(key)!r} -> {new.get(key)!r})"
        if old.get("cast") != new.get("cast"):
            return f"field {name!r}: cast changed ({old.get('cast')!r} -> {new.get('cast')!r})"
    return None


def _check_fill(baseline: dict, metrics: dict, all_fill: dict[str, float]) -> Optional[str]:
    """Reject a sandbox parse whose fill fell clearly below the last good baseline."""
    good = (baseline or {}).get("last_good") or {}
    if not good:
        return None
    if metrics.get("fill_ratio", 0.0) < float(good.get("fill_ratio", 0.0)) - FILL_TOLERANCE:
        return f"fill_ratio {metrics.get('fill_ratio')} dropped below baseline {good.get('fill_ratio')}"
    for f, v in (good.get("field_fill_all") or {}).items():
        if all_fill.get(f, 0.0) < float(v) - FIELD_FILL_TOLERANCE:
            return f"field {f!r} fill {all_fill.get(f, 0.0)} dropped below baseline {v}"
    return None


def _selector_tokens(cfg: scfg.SiteConfig, page: str) -> list[str]:
    """Class/id tokens from the current (possibly broken) selectors, for anchoring.

    Reuses the existing config only — no DOM parsing. Longer tokens first so the
    most distinctive class is tried before generic ones like ``card``."""
    parts = [cfg.row_selector] if page == "list" else []
    fields = cfg.list_fields if page == "list" else cfg.detail_fields
    for spec in (fields or {}).values():
        if isinstance(spec, dict) and spec.get("selector"):
            parts.append(str(spec["selector"]))
    toks: list[str] = []
    for p in parts:
        toks.extend(re.findall(r"[.#]([\w-]+)", p))
    seen: dict[str, None] = {}
    for t in toks:
        if len(t) >= 4:
            seen.setdefault(t, None)
    return sorted(seen, key=len, reverse=True)


def _clean_html(html: str, limit: int = 60000, anchor_tokens: Optional[list[str]] = None) -> str:
    html = re.sub(r"(?is)<(script|style|svg|path|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<head[^>]*>.*?</head>", " ", html)
    html = re.sub(r"(?is)<!--.*?-->", " ", html)
    html = re.sub(r"\s+", " ", html)
    if len(html) <= limit:
        return html
    # Anchor the window on the first occurrence of a known selector token so the
    # repeated content region (rows/cards) is included, not just the page header.
    start = 0
    for tok in anchor_tokens or []:
        idx = html.find(tok)
        if idx != -1:
            start = max(0, idx - 800)
            break
    return html[start:start + limit]


def _build_prompt(cfg: scfg.SiteConfig, page: str, html: str, reasons: list[str]) -> str:
    fields = cfg.list_fields if page == "list" else cfg.detail_fields
    schema_fields = list(schema.get_schema(cfg.schema).model_fields)
    limit = int(_env("SCRAPER_HEAL_HTML_LIMIT", "60000") or 60000)
    cleaned = _clean_html(html, limit=limit, anchor_tokens=_selector_tokens(cfg, page))
    block = {"row_selector": cfg.row_selector, "fields": fields} if page == "list" else {"fields": fields}
    # NOTE: the same yaml also carries a data-only ``stream_resolver`` block
    # (embed URL -> media URL). If a future drift is traced to the player/API
    # changing, that block is likewise LLM-regeneratable in place; this page-level
    # selector heal does not touch it. Resolver failures are recorded to the site
    # state (``last_resolver``) for the dashboard.
    return (
        "You are fixing a broken web scraper. The site's HTML changed and the CSS "
        "selectors below no longer extract data.\n\n"
        f"Drift reasons: {reasons}\n"
        f"Target schema fields to fill: {schema_fields}\n\n"
        f"Current (broken) {page} selector block (yaml):\n"
        f"{yaml.safe_dump(block, allow_unicode=True, sort_keys=False)}\n"
        "Here is the current page HTML (scripts/styles stripped, truncated):\n"
        f"'''\n{cleaned}\n'''\n\n"
        "Return ONLY a yaml code block with the corrected selector block in the "
        "EXACT same shape (same keys). Use CSS selectors that exist in the HTML "
        "above. Do not add prose."
    )


def _extract_yaml(text: str) -> Optional[dict]:
    m = re.search(r"```(?:yaml|yml)?\s*(.*?)```", text, re.S)
    candidate = m.group(1) if m else text
    for attempt in (candidate, text):
        try:
            data = yaml.safe_load(attempt)
            if isinstance(data, dict) and ("fields" in data or "row_selector" in data):
                return data
        except Exception:
            continue
    return None


def _call_codex(prompt: str, timeout: int) -> dict:
    """Invoke codex CLI non-interactively, text-only (read-only sandbox, no writes)."""
    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False) as tf:
        out_path = tf.name
    cmd = [
        "codex", "exec", "-",
        "-s", "read-only",
        "--skip-git-repo-check",
        "--color", "never",
        "-o", out_path,
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return _fail("codex CLI not found on PATH")
    except subprocess.TimeoutExpired:
        return _fail(f"codex timed out after {timeout}s")
    except Exception as exc:  # pragma: no cover - defensive
        return _fail(f"codex invocation error: {exc}")

    text = ""
    try:
        with open(out_path, "r", encoding="utf-8") as fh:
            text = fh.read().strip()
    except OSError:
        pass
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
    if not text:
        text = (proc.stdout or "").strip()
    if proc.returncode != 0 and not text:
        return _fail(
            "codex returned non-zero and no text",
            returncode=proc.returncode,
            stderr=(proc.stderr or "")[:300],
        )
    return {"status": "ok", "text": text}


def _call_pi(prompt: str, timeout: int) -> dict:
    """Invoke the pi CLI one-shot (single spawn, no warm daemon), text-only.

    Uses stdin for the prompt and an ephemeral session (--no-session) with tools
    disabled (--no-tools) so it only generates text. Model is subscription-authed
    via ~/.pi/agent/auth.json (no API key)."""
    model = _env("SCRAPER_HEAL_MODEL", PI_DEFAULT_MODEL)
    cmd = [
        _env("SCRAPER_HEAL_PI_BIN", "pi"), "-p",
        "--model", model,
        "--no-session", "--no-tools", "--offline",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return _fail("pi CLI not found on PATH")
    except subprocess.TimeoutExpired:
        return _fail(f"pi timed out after {timeout}s")
    except Exception as exc:  # pragma: no cover - defensive
        return _fail(f"pi invocation error: {exc}")

    text = (proc.stdout or "").strip()
    if proc.returncode != 0 and not text:
        return _fail(
            "pi returned non-zero and no text",
            returncode=proc.returncode,
            stderr=(proc.stderr or "")[:300],
        )
    if not text:
        return _fail("pi returned empty text")
    return {"status": "ok", "text": text}


def _call_pi_agent(cfg, page, html, reasons, timeout) -> dict:
    """``SCRAPER_HEAL_PROVIDER=pi_agent``: the drift is handed to the repair agent (``heal_agent``: the onboarding skill in
    repair mode and its sandbox tools) instead of a one-shot prompt. What comes back is the same kind of answer the other
    providers give, a yaml selector block as text, so the proposal runs through the SAME gates below."""
    from . import heal_agent
    return heal_agent.drift_generate(cfg, page, html, reasons, timeout)


def _provider_generate(cfg, page, html, reasons, timeout) -> dict:
    provider = _env("SCRAPER_HEAL_PROVIDER", "codex_cli")
    if provider == "pi_agent":
        return _call_pi_agent(cfg, page, html, reasons, timeout)
    prompt = _build_prompt(cfg, page, html, reasons)
    if provider == "codex_cli":
        return _call_codex(prompt, timeout)
    if provider == "pi":
        return _call_pi(prompt, timeout)
    if provider == "openai_compatible":
        # Future: POST to SCRAPER_HEAL_BASE_URL with SCRAPER_HEAL_API_KEY.
        base = _env("SCRAPER_HEAL_BASE_URL", "")
        if not base:
            return _fail("openai_compatible provider not configured (no base_url)")
        return _fail("openai_compatible provider not implemented yet")
    return _fail(f"unknown heal provider {provider!r}")


def _heal_impl(cfg: scfg.SiteConfig, *, page: str, html: str, reasons: list[str], trace: dict,
               trigger: str = "drift") -> dict:
    """Attempt a self-heal for ``page`` ('list' or 'detail'). Never raises."""
    if not _enabled():
        return _fail("heal disabled (SCRAPER_HEAL_ENABLED)")

    try:
        # Automatic (ingest-blocking) heals get a shorter leash than manual ones.
        timeout = _int_env("SCRAPER_HEAL_TIMEOUT", 120)
        if trigger != "manual":
            timeout = min(timeout, _int_env("SCRAPER_HEAL_AUTO_TIMEOUT", 60))
        if _env("SCRAPER_HEAL_PROVIDER", "codex_cli") == "pi_agent":   # an agent run takes minutes: its own limit, any trigger
            timeout = agent_timeout()
        gen = _provider_generate(cfg, page, html, reasons, timeout)
        agent = gen.pop("agent", None) if isinstance(gen, dict) else None   # the agent log (heal_agent): ops record only
        if agent:
            trace["agent"] = agent
        scope = gen.pop("scope", None) if isinstance(gen, dict) else None   # layers / touched keys of the agent's proposal
        if scope:
            trace.update(scope)
        if gen.get("status") != "ok":
            return gen  # already a heal_failed dict

        proposed = _extract_yaml(gen["text"])
        if not proposed:
            return _fail("could not parse selector yaml from LLM output")
        trace["before"] = (
            {"row_selector": cfg.row_selector, "fields": cfg.list_fields}
            if page == "list" else {"fields": cfg.detail_fields}
        )
        trace["after"] = proposed

        # --- structural validation: field set + attr/cast shape preserved ---
        old_fields = cfg.list_fields if page == "list" else cfg.detail_fields
        new_fields = proposed.get("fields", old_fields)
        bad = _check_structure(old_fields, new_fields)
        if bad:
            return _fail(f"proposal rejected: {bad}")

        # --- sandbox validation on the SAME html ---
        new_data = json.loads(json.dumps(cfg.data))  # deep copy
        all_fill: dict[str, float] = {}
        if page == "list":
            new_data.setdefault("list", {})
            if "row_selector" in proposed:
                new_data["list"]["row_selector"] = proposed["row_selector"]
            new_data["list"]["fields"] = new_fields
            raw = parse.parse_list(html, new_data["list"]["row_selector"], new_data["list"]["fields"])
            all_fill = field_fill(raw, new_fields)
        else:
            new_data.setdefault("detail", {})
            new_data["detail"]["fields"] = new_fields
            raw = [parse.parse_detail(html, new_data["detail"]["fields"])]

        _valid, metrics = schema.validate_items(cfg.schema, raw)
        baseline = cfg.baseline()
        verdict = drift.detect(metrics, baseline)
        if verdict["drift"]:
            return _fail(
                "proposal still drifts in sandbox",
                sandbox_metrics=metrics,
                sandbox_reasons=verdict["reasons"],
            )
        if page == "list":
            bad = _check_fill(baseline, metrics, all_fill)
            if bad:
                return _fail(f"proposal rejected: {bad}", sandbox_metrics=metrics)

        result = {
            "status": "healed",
            "page": page,
            "sandbox_metrics": metrics,
            "applied": False,
        }
        if _autoapply():
            version = scfg.save_new_version(cfg.site_id, new_data)
            result["applied"] = True
            result["new_version"] = version
        return result
    except Exception as exc:  # never crash the runner
        return _fail(f"unexpected heal error: {exc}")


def _flatten(d: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(d, dict):
        out: dict[str, Any] = {}
        for k, v in d.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    return {prefix: d}


def selector_diff(before: Optional[dict], after: Optional[dict]) -> list[dict]:
    """Changed selector paths: [{path, before, after}]."""
    if not before or not after:
        return []
    b, a = _flatten(before), _flatten(after)
    return [
        {"path": k, "before": b.get(k), "after": a.get(k)}
        for k in sorted(set(a) | set(b)) if b.get(k) != a.get(k)
    ]


def heal(cfg: scfg.SiteConfig, *, page: str, html: str, reasons: list[str],
         trigger: str = "drift") -> dict:
    """Run a heal, tracking activity and appending it to the ops heal history.

    Never raises. The result carries ``outcome`` (fixed | not_applied | failed |
    skipped_cooldown). A failed/unapplied automatic heal starts a per-site
    cooldown (``SCRAPER_HEAL_COOLDOWN`` seconds, default 3600) during which
    non-manual triggers are skipped; ``trigger="manual"`` ignores the cooldown.
    """
    try:
        return _heal_tracked(cfg, page=page, html=html, reasons=reasons, trigger=trigger)
    except Exception as exc:
        return {**_fail(f"unexpected heal error: {exc}"), "outcome": FAILED}


def cooldown_skip(site: str, trigger: str, base: dict) -> Optional[dict]:
    """The ``skipped_cooldown`` result when a non-manual heal of ``site`` falls into its cooldown (recorded once per cooldown
    in the ops heal history, ``base`` = the fields every heal record carries), else None. Shared with ``heal_agent``."""
    from . import state
    if trigger != "manual" and _enabled():
        cd = state.get_heal_cooldown(site)
        if cd:
            last = (state.list_ops("heals", site, 1) or [None])[0]
            if not (last and last.get("outcome") == SKIPPED_COOLDOWN
                    and last.get("cooldown_until") == cd["until_at"]):
                state.record_ops_heal({
                    **base, "at": state._now(), "outcome": SKIPPED_COOLDOWN, "applied": False,
                    "new_version": None, "error": f"cooldown until {cd['until_at']}",
                    "cooldown_until": cd["until_at"], "before": None, "after": None,
                    "diff": [], "duration": 0,
                })
            return {"status": SKIPPED_COOLDOWN, "outcome": SKIPPED_COOLDOWN, "applied": False,
                    "reason": f"cooldown until {cd['until_at']}", "cooldown_until": cd["until_at"]}
    return None


def settle(site: str, result: dict) -> str:
    """Set ``result["outcome"]`` (fixed | not_applied | failed) and move the heal cooldown: a failed / unapplied heal starts
    it (``SCRAPER_HEAL_COOLDOWN`` seconds, default 3600), a real fix clears it. A disabled heal is not an attempt, so it
    neither starts nor counts. Returns the outcome. Shared with ``heal_agent``."""
    import time
    from . import state
    if result.get("status") == "healed":
        outcome = FIXED if result.get("applied") else NOT_APPLIED
    else:
        outcome = FAILED
    result["outcome"] = outcome
    if _enabled():
        try:
            if outcome == FIXED:
                state.set_heal_cooldown(site, None)
            else:
                state.set_heal_cooldown(site, time.time() + _int_env("SCRAPER_HEAL_COOLDOWN", 3600))
        except Exception:
            pass
    return outcome


def _heal_tracked(cfg: scfg.SiteConfig, *, page: str, html: str, reasons: list[str], trigger: str) -> dict:
    import time
    from . import state

    site = cfg.site_id
    provider = _env("SCRAPER_HEAL_PROVIDER", "codex_cli")
    base = {"site": site, "trigger": trigger, "reasons": list(reasons), "provider": provider,
            "model": _env("SCRAPER_HEAL_MODEL", "") or None, "page": page}

    skipped = cooldown_skip(site, trigger, base)
    if skipped:
        return skipped

    owned = state.activity_start(site, "heal", trigger)
    started_at, t0 = state._now(), time.monotonic()
    trace: dict = {}
    try:
        result = _heal_impl(cfg, page=page, html=html, reasons=reasons, trace=trace, trigger=trigger)
    finally:
        if owned:
            state.activity_end(site, "heal")
    outcome = settle(site, result)

    state.record_ops_heal({
        **base, "at": started_at, "outcome": outcome, "applied": bool(result.get("applied")),
        "new_version": result.get("new_version"), "error": result.get("reason"),
        "before": trace.get("before"), "after": trace.get("after"),
        "diff": selector_diff(trace.get("before"), trace.get("after")),
        "duration": round(time.monotonic() - t0, 2),
        **({"agent": trace["agent"]} if trace.get("agent") else {}),   # pi_agent provider: {events (<= 60), turns, ...}
        **{k: trace[k] for k in ("layers", "touched_keys", "touched_paths") if trace.get(k)},
    })
    return result


# --- repair-agent support (the pi_agent provider; heal_agent.py is the other half) -------------------------------------

AGENT_TIMEOUT_DEFAULT = 300   # SCRAPER_HEAL_AGENT_TIMEOUT: longest one repair-agent run (s)


def agent_timeout() -> int:
    """Longest one repair-agent run may take (``SCRAPER_HEAL_AGENT_TIMEOUT`` seconds, default 300)."""
    return _int_env("SCRAPER_HEAL_AGENT_TIMEOUT", AGENT_TIMEOUT_DEFAULT)


def _fields_of(data: Any, block: str) -> dict:
    part = data.get(block) if isinstance(data, dict) else None
    fields = part.get("fields") if isinstance(part, dict) else None
    return fields if isinstance(fields, dict) else {}


def _page_urls(data: dict) -> list[tuple[str, str]]:
    """``(where, url)`` of the page addresses a scan fetches from the yaml: ``list_url``, ``detail_pages``, ``collections[].path``."""
    out: list[tuple[str, str]] = []
    if isinstance(data.get("list_url"), str):
        out.append(("list_url", data["list_url"]))
    for i, path in enumerate(data.get("detail_pages") if isinstance(data.get("detail_pages"), list) else []):
        if isinstance(path, str):
            out.append((f"detail_pages[{i}]", path))
    for i, col in enumerate(data.get("collections") if isinstance(data.get("collections"), list) else []):
        if isinstance(col, dict) and isinstance(col.get("path"), str):
            out.append((f"collections[{i}].path", col["path"]))
    return out


def check_repair_proposal(cfg: scfg.SiteConfig, data: Any) -> list[str]:
    """Reasons (``[]`` = fine) why the whole-yaml proposal ``data`` may not replace the ACTIVE config of ``cfg``'s site: the
    gates of the selector heal (``_check_structure``: no field dropped, no attr / cast / all change, a usable selector each)
    applied to the list and detail fields, plus what a repair must never touch: the site id, the schema, the host of
    ``base_url`` (and the pages a scan fetches, ``list_url`` / ``detail_pages`` / collection paths, stay on that host), the ``normalize`` key rules (they key the library: a change would duplicate every title), whether the site
    has a ``normalize:`` block at all (a hand-built site has its own normalizer) and the ``image_hosts`` allow-list."""
    from urllib.parse import urlsplit
    if not isinstance(data, dict):
        return ["proposal is not a mapping"]
    reasons: list[str] = []
    if str(data.get("site_id") or cfg.site_id) != cfg.site_id:
        reasons.append(f"site_id changed ({cfg.site_id!r} -> {data.get('site_id')!r})")
    if str(data.get("schema") or cfg.schema) != cfg.schema:
        reasons.append(f"schema changed ({cfg.schema!r} -> {data.get('schema')!r})")
    old_host = (urlsplit(cfg.base_url).hostname or "").lower()
    new_host = (urlsplit(str(data.get("base_url") or cfg.base_url)).hostname or "").lower()
    if old_host != new_host:
        reasons.append(f"base_url host changed ({old_host!r} -> {new_host!r}): a domain change is for the admin; say it in notes")
    for where, url in _page_urls(data):   # the pages a scan fetches stay on the site's own host
        host = (urlsplit(url).hostname or "").lower() if re.match(r"(?i)^([a-z][a-z0-9+.-]*:)?//|^[a-z][a-z0-9+.-]*:", url) else new_host
        if host and host != new_host:
            reasons.append(f"{where} points at another host ({host!r}): the pages of a site stay on its own host")
    for block in ("list", "detail"):
        old = _fields_of(cfg.data, block)
        if old:
            bad = _check_structure(old, _fields_of(data, block))
            if bad:
                reasons.append(f"{block}: {bad}")
    old_norm, new_norm = cfg.data.get("normalize"), data.get("normalize")
    if bool(old_norm) != bool(new_norm):
        reasons.append("normalize block " + ("added to a site with its own normalizer" if new_norm else "removed"))
    elif isinstance(old_norm, dict) and isinstance(new_norm, dict) and old_norm.get("key") != new_norm.get("key"):
        reasons.append("normalize.key changed (it keys the library titles; a repair never re-keys)")
    added_hosts = set(data.get("image_hosts") or []) - set(cfg.data.get("image_hosts") or []) if isinstance(data.get("image_hosts"), list) else set()
    if added_hosts:
        reasons.append(f"image_hosts gained {sorted(map(str, added_hosts))}: new image hosts are for the admin")
    return reasons


def heal_site_playback(site_id: str, *, evidence: dict, trigger: str = "playback") -> dict:
    """Repair a registered site after repeating PLAYBACK failures (or a site that produces no video sources): the repair
    agent (``heal_agent``) gets the evidence, may change the site yaml and / or the provider recipes, and its proposal passes
    the heal gates (structure, baseline, the failing and the working examples, every other user of a changed recipe) before
    it is applied (``heal_autoapply``). Same result shape as ``heal()``: ``{status: healed | heal_failed | skipped_cooldown,
    outcome: fixed | not_applied | failed | skipped_cooldown, applied, new_version?, reason?, ...}``. Never raises.

    ``evidence`` = ``{site, window: {n, failed}, failing: [{source_id, kind, episode_id, locator, error, stage, host,
    candidates: [...]}], ok_examples: [{source_id, locator}]}`` (``playheal.evaluate``; ``kind: "no_sources"`` for a site whose
    scan produced no episode sources)."""
    try:
        from . import heal_agent
        return heal_agent.heal_site_playback(site_id, evidence=evidence, trigger=trigger)
    except Exception as exc:
        return {**_fail(f"unexpected heal error: {exc}"), "outcome": FAILED, "applied": False}
