"""Site quality summary of an onboarding report (``report.site_quality``): is the new site's playback STANDARD (the same known video
hosts everywhere, resolved by the provider library) or MIXED (many hosts / site-specific heuristics: it will keep needing heals)?

Pure functions, no network: the input is what the report already collected, ``report.playable.samples[]`` (``ok``, ``error``,
``streams``, ``providers`` and, written by ``onboard_sandbox._follow_playback`` through :func:`source_rows`, ``sources[]`` =
``{host, resolver_type, provider, known, ok, challenge}`` per candidate) plus the yaml's ``resolvers:`` list. It is NOT a criterion:
it never blocks an acceptance, it only informs the admin ("Site ekle" card, the onboarding event) and writes a warning note.

``report.site_quality = {grade, label, reasons[], note, hosts{}, known_ratio, resolved_ratio, mixed_ratio, challenge_ratio, ...}``
(``grade``: ``standart | karışık | zayıf | bilinmiyor``; the thresholds are the constants below). An optional per-sample
``duration_match`` (the stream probe of the playback heal) is used when present and ignored otherwise.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urlsplit

# --- thresholds (change here) -------------------------------------------------------------------------------------
MIN_SAMPLES = 3                # fewer judged samples -> "bilinmiyor"
STANDARD_RESOLVED = 0.8        # standart: at least this share of the samples resolved ...
STANDARD_KNOWN = 0.8           # ... at least this share of the resolved sources came from a known provider ...
STANDARD_MIXED = 0.34          # ... at most this share of the samples has another host set than the usual one ...
STANDARD_CHALLENGE = 0.0       # ... and at most this share shows a Cloudflare / challenge sign
WEAK_RESOLVED = 0.5            # zayıf: fewer samples resolved than this, or
WEAK_KNOWN = 0.25              # a smaller share of the resolved sources is known, or
WEAK_CHALLENGE = 0.5           # at least this share is behind a challenge
DURATION_MIN = 0.5             # a stream probe (``duration_match``) that matches fewer samples than this downgrades "standart"
MAX_HOSTS = 8
MAX_ROWS = 12                  # sources kept per sample
GRADES = ("standart", "karışık", "zayıf", "bilinmiyor")
NOTE_MIXED = "Bu site karışık kaynak kullanıyor; sık heal gerekebilir."
NOTE_WEAK = "Bu sitenin oynatma kaynakları zayıf (çözülmüyor ya da koruma arkasında); sık heal gerekebilir."

_CHALLENGE = re.compile(r"cloudflare|challenge|just a moment|cf[-_]chl|cf_clearance|captcha|attention required", re.I)


# --- collecting (called while the report is built) ----------------------------------------------------------------
def provider_pool(extra: Optional[list] = None) -> list:
    """The providers a host can belong to (code + recipes of the library + the draft's ``extra`` recipes); ``[]`` on any error."""
    try:
        from .providers import registry
        return list(registry.providers(extra))
    except Exception:
        return []


def host_known(url: str, pool: Iterable) -> bool:
    """Does a provider of ``pool`` recognise ``url`` by its host (the library matched it, no heuristic needed)?"""
    for provider in pool:
        try:
            if provider.matches(url):
                return True
        except Exception:
            continue
    return False


def source_rows(candidates: list, outcomes: list, pool: Iterable, base: str = "") -> list[dict]:
    """``[{host, resolver_type, provider, known, ok, challenge}]`` of one playback page: ``candidates`` (resolver output) with their
    ``outcomes`` (same order, ``_resolve_one`` dicts). ``known`` = the candidate's host belongs to a library provider, or the
    stream came through a provider of the pool by name (a hand-off page that led to ok.ru is still ok.ru); a stream produced by a
    site-specific resolver (``player_page`` / ``json_api`` rules in the yaml) is NOT known."""
    pool = list(pool)
    names = {str(getattr(p, "name", "")).lower() for p in pool}
    rows: list[dict] = []
    for index, cand in enumerate(candidates or []):
        if not isinstance(cand, dict) or len(rows) >= MAX_ROWS:
            continue
        outcome = outcomes[index] if index < len(outcomes or []) and isinstance(outcomes[index], dict) else {}
        url = urljoin(base, str(cand.get("url") or "")) if base else str(cand.get("url") or "")
        host = (urlsplit(url).hostname or "").lower()
        if not host:
            continue
        ok = bool(outcome.get("streams"))
        provider = str(outcome.get("provider") or "")
        by_name = ok and bool(provider) and provider.lower() in names
        error = str(outcome.get("error") or "") + " " + " ".join(str(e.get("error") or "") for e in outcome.get("events") or []
                                                                  if isinstance(e, dict) and not e.get("ok"))
        rows.append({"host": host, "resolver_type": str(outcome.get("resolver_type") or cand.get("resolver_type") or ""),
                     "provider": provider if ok else "", "known": host_known(url, pool) or by_name, "ok": ok,
                     "challenge": (not ok) and bool(_CHALLENGE.search(error))})
    return rows


# --- judging ------------------------------------------------------------------------------------------------------
def _judged(playable: Any) -> list[dict]:
    """The samples that count: checked (not skipped by time, not blocked content)."""
    samples = (playable or {}).get("samples") if isinstance(playable, dict) else None
    return [s for s in samples or [] if isinstance(s, dict) and not s.get("skipped") and not s.get("blocked")]


def _family(host: str) -> str:
    parts = [p for p in (host or "").lower().split(".") if p]
    return ".".join(parts[-2:]) if len(parts) > 2 else ".".join(parts)


def _sources_of(sample: dict) -> list[dict]:
    """``sources`` of a sample; an older sample without them falls back to its stream hosts (known = a provider named it)."""
    rows = [r for r in sample.get("sources") or [] if isinstance(r, dict) and r.get("host")]
    if rows:
        return rows
    named = bool(sample.get("providers"))
    return [{"host": str(s.get("host") or ""), "resolver_type": "", "provider": "", "known": named, "ok": bool(sample.get("ok")),
             "challenge": False} for s in sample.get("streams") or [] if isinstance(s, dict) and s.get("host")]


def _sample_challenge(sample: dict, warnings: list) -> bool:
    if any(r.get("challenge") for r in _sources_of(sample)):
        return True
    if sample.get("ok"):
        return False
    text = str(sample.get("error") or "")
    key = str(sample.get("locator") or "")
    text += " " + " ".join(w for w in warnings if isinstance(w, str) and key and key in w)
    return bool(_CHALLENGE.search(text))


def _duration_ratio(samples: list[dict]) -> Optional[float]:
    """Share of the probed samples whose stream matches the expected duration (``duration_match``: bool or ``{ok}``); None = no probe."""
    marks = []
    for sample in samples:
        value = sample.get("duration_match")
        if isinstance(value, dict):
            value = value.get("ok")
        if isinstance(value, bool):
            marks.append(value)
    return round(sum(marks) / len(marks), 2) if marks else None


def _pct(value: float) -> str:
    return f"%{round(value * 100)}"


def _yaml_resolvers(data: Any) -> list[str]:
    items = (data or {}).get("resolvers") if isinstance(data, dict) else None
    return list(dict.fromkeys(str(i.get("type")) for i in items or [] if isinstance(i, dict) and i.get("type")))


def assess(playable: Any, data: Any = None, warnings: Optional[list] = None) -> dict:
    """The ``site_quality`` block of a report. ``playable`` = ``report.playable``; ``data`` = the parsed yaml (its ``resolvers:``
    types are listed); ``warnings`` = the report's warnings (a challenge mentioned for a sample counts for it)."""
    warnings = [w for w in warnings or [] if isinstance(w, str)]
    samples = _judged(playable)
    n = len(samples)
    rows = [_sources_of(s) for s in samples]
    resolved = [s for s in samples if s.get("ok")]
    good = [r for sources in rows for r in sources if r.get("ok")]
    hosts = Counter(r["host"] for sources in rows for r in sources)
    sets = [frozenset(_family(r["host"]) for r in sources) for sources in rows]
    usual = Counter(sets).most_common(1)[0][1] if sets else 0
    challenged = sum(1 for s in samples if _sample_challenge(s, warnings))
    out: dict[str, Any] = {
        "grade": "bilinmiyor", "label": "bilinmiyor", "reasons": [], "note": "", "n": n,
        "hosts": dict(hosts.most_common(MAX_HOSTS)),
        "known_ratio": round(sum(1 for r in good if r.get("known")) / len(good), 2) if good else 0.0,
        "resolved_ratio": round(len(resolved) / n, 2) if n else 0.0,
        "mixed_ratio": round(1 - usual / n, 2) if n else 0.0,
        "challenge_ratio": round(challenged / n, 2) if n else 0.0,
        "avg_sources": round(sum(1 for r in good) / n, 1) if n else 0.0,
        "resolvers": _yaml_resolvers(data),
        "methods": dict(Counter(r.get("resolver_type") or "?" for r in good)),
        "known_hosts": sorted({r["host"] for r in good if r.get("known")})[:MAX_HOSTS],
        "unknown_hosts": sorted({r["host"] for r in rows_flat(rows) if not r.get("known")})[:MAX_HOSTS]}
    duration = _duration_ratio(samples)
    if duration is not None:
        out["duration_ratio"] = duration
    if n < MIN_SAMPLES:
        out["reasons"] = [f"Yalnız {n} örnek bölüm denendi (en az {MIN_SAMPLES} gerekir); kalite değerlendirilemedi."]
        return out

    weak = out["resolved_ratio"] < WEAK_RESOLVED or out["challenge_ratio"] >= WEAK_CHALLENGE or out["known_ratio"] < WEAK_KNOWN
    standard = (out["resolved_ratio"] >= STANDARD_RESOLVED and out["known_ratio"] >= STANDARD_KNOWN
                and out["mixed_ratio"] <= STANDARD_MIXED and out["challenge_ratio"] <= STANDARD_CHALLENGE
                and (duration is None or duration >= DURATION_MIN))
    grade = "zayıf" if weak else "standart" if standard else "karışık"
    out["grade"] = out["label"] = grade
    out["reasons"] = _reasons(out, n, len(resolved), challenged, duration, len(good))
    out["note"] = {"karışık": NOTE_MIXED, "zayıf": NOTE_WEAK}.get(grade, "")
    return out


def rows_flat(rows: list[list[dict]]) -> list[dict]:
    return [r for sources in rows for r in sources]


def _names(hosts: list[str], limit: int = 3) -> str:
    return ", ".join(hosts[:limit]) + (" ..." if len(hosts) > limit else "")


def _reasons(q: dict, n: int, resolved: int, challenged: int, duration: Optional[float], good: int) -> list[str]:
    """2-4 short Turkish lines, most telling first."""
    out: list[str] = []
    known, unknown = q["known_hosts"], q["unknown_hosts"]
    if resolved == n and q["known_ratio"] >= STANDARD_KNOWN and known:
        out.append(f"{n}/{n} örnek bilinen provider'dan ({_names(known)})")
    else:
        out.append(f"{resolved}/{n} örnek çözüldü")
        if good and q["known_ratio"] < 1 and unknown:
            out.append(f"Çözülen kaynakların {_pct(1 - q['known_ratio'])}'i provider kütüphanesinde yok ({_names(unknown)})")
        elif good and known:
            out.append(f"Çözülenler bilinen provider'dan ({_names(known)})")
    if q["mixed_ratio"] > 0:
        out.append(f"Örneklerin {_pct(q['mixed_ratio'])}'inde farklı host kümesi")
    if challenged:
        out.append(f"{challenged} örnekte Cloudflare / challenge işareti")
    if duration is not None and duration < DURATION_MIN:
        out.append(f"Akış süresi örneklerin yalnız {_pct(duration)}'inde beklenenle uyuşuyor")
    if len(out) < 2 and q["resolvers"]:
        out.append("Resolver: " + ", ".join(q["resolvers"]))
    return out[:4]


# --- the views ----------------------------------------------------------------------------------------------------
def line(quality: Any) -> str:
    """One short line ("Site kalitesi: karışık - ..."), ``""`` when there is no block."""
    if not isinstance(quality, dict) or quality.get("grade") not in GRADES:
        return ""
    reasons = [r for r in quality.get("reasons") or [] if isinstance(r, str)]
    return "Site kalitesi: " + quality["grade"] + (" - " + reasons[0] if reasons else "")


def view(quality: Any) -> Optional[dict]:
    """What the admin card shows: ``{grade, line, reasons[], note, details[{label, value}]}`` (None without a block)."""
    text = line(quality)
    if not text:
        return None
    details = [{"label": "Örnek", "value": quality.get("n")},
               {"label": "Çözülen", "value": _pct(float(quality.get("resolved_ratio") or 0))},
               {"label": "Bilinen provider", "value": _pct(float(quality.get("known_ratio") or 0))},
               {"label": "Farklı host kümesi", "value": _pct(float(quality.get("mixed_ratio") or 0))},
               {"label": "Cloudflare / challenge", "value": _pct(float(quality.get("challenge_ratio") or 0))},
               {"label": "Ort. kaynak / bölüm", "value": quality.get("avg_sources")},
               {"label": "Host'lar", "value": ", ".join(f"{h} ({c})" for h, c in (quality.get("hosts") or {}).items()) or "-"},
               {"label": "Resolver türleri", "value": ", ".join(quality.get("resolvers") or []) or "-"}]
    if quality.get("duration_ratio") is not None:
        details.append({"label": "Süre uyuşması", "value": _pct(float(quality["duration_ratio"]))})
    return {"grade": quality["grade"], "line": text, "reasons": list(quality.get("reasons") or [])[:4],
            "note": str(quality.get("note") or ""), "details": details}
