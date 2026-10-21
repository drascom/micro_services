"""``ProviderResult`` (README §5.9): the one shape the provider library hands out.

    {provider, duration_s?, cache_ttl?,
     streams:   [{url, type: hls|mp4|embed, quality?, width?, height?, bitrate?, label?, request_headers?, stream_proxy?}],
     subtitles: [{url, lang, label, format}],
     audio:     [{lang, label, default?}],
     measured:  {resolve_ms}}

The legacy keys (``url``, ``type``, ``quality``, ``duration``, ``variant``) stay on the dict for the resolution core.
An HLS master playlist is read once: its ``#EXT-X-STREAM-INF`` variants become extra ``streams[]`` entries (after the
master, best first) with ``width/height/bitrate``; ``#EXT-X-MEDIA`` audio / subtitle renditions fill ``audio[]`` /
``subtitles[]``. Input is a provider URL's result only: nothing here knows a site.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from .. import langs

MASTER_TIMEOUT = 4.0
MAX_VARIANTS = 8
_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def _attrs(line: str) -> dict:
    body = line.split(":", 1)[1] if ":" in line else ""
    return {k: v.strip('"') for k, v in _ATTR.findall(body)}


def parse_master(text: str, base_url: str) -> dict:
    """``{variants: [{url, width, height, bitrate}], audio: [...], subtitles: [...]}`` of an HLS master playlist (empty
    lists for a media playlist / anything else). Pure function."""
    out: dict = {"variants": [], "audio": [], "subtitles": []}
    if not isinstance(text, str) or "#EXTM3U" not in text[:64]:
        return out
    lines = [ln.strip() for ln in text.splitlines()]
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            attrs = _attrs(line)
            uri = next((ln for ln in lines[i + 1:] if ln and not ln.startswith("#")), "")
            if not uri:
                continue
            width = height = 0
            res = re.fullmatch(r"(\d{2,5})x(\d{2,5})", attrs.get("RESOLUTION", ""))
            if res:
                width, height = int(res.group(1)), int(res.group(2))
            try:
                bitrate = int(attrs.get("BANDWIDTH") or attrs.get("AVERAGE-BANDWIDTH") or 0)
            except ValueError:
                bitrate = 0
            out["variants"].append({"url": urljoin(base_url, uri), "width": width, "height": height, "bitrate": bitrate})
        elif line.startswith("#EXT-X-MEDIA"):
            attrs = _attrs(line)
            kind = attrs.get("TYPE", "").upper()
            lang = langs.code(attrs.get("LANGUAGE") or "") or langs.code(attrs.get("NAME") or "") or (attrs.get("LANGUAGE") or None)
            label = attrs.get("NAME") or langs.label(lang) or (lang or "")
            default = attrs.get("DEFAULT", "").upper() == "YES"
            if kind == "AUDIO":
                out["audio"].append({"lang": lang, "label": label, "default": default})
            elif kind == "SUBTITLES" and attrs.get("URI"):
                out["subtitles"].append({"url": urljoin(base_url, attrs["URI"]), "lang": lang, "label": label,
                                         "format": "m3u8", "default": default})
    return out


def _quality(height: int) -> str:
    return f"{height}p" if height else "auto"


def _master_details(stream: dict, referer: str, fetch_api: Any) -> dict:
    headers = dict(stream.get("request_headers") or {})
    if referer and "Referer" not in headers:
        headers["Referer"] = referer
    try:
        text = fetch_api.fetch_url(stream["url"], headers=headers or None, timeout=MASTER_TIMEOUT, retries=1,
                                   check_robots=False)
    except Exception:
        return {"variants": [], "audio": [], "subtitles": []}
    return parse_master(text if isinstance(text, str) else "", stream["url"])


def _subtitle(track: dict) -> dict:
    url = str(track.get("url") or "")
    fmt = track.get("format") or ("srt" if urlparse(url).path.lower().endswith(".srt") else "vtt")
    return {**track, "url": url, "lang": track.get("lang"), "label": track.get("label") or langs.label(track.get("lang")) or "Altyazı",
            "format": fmt}


def to_result(raw: Optional[dict], *, resolve_ms: int, referer: str = "", fetch_api: Any = None,
              read_master: bool = True) -> Optional[dict]:
    """``raw`` (a provider's dict) as a :data:`ProviderResult`; None stays None."""
    if not isinstance(raw, dict):
        return None
    streams = [dict(s) for s in raw.get("streams") or [] if isinstance(s, dict) and s.get("url")]
    if not streams and raw.get("url"):
        streams = [{"url": raw["url"], "type": raw.get("type") or "mp4", "quality": raw.get("quality") or "auto"}]
    subtitles = [_subtitle(t) for t in raw.get("subtitles") or [] if isinstance(t, dict) and t.get("url")]
    audio = [dict(a) for a in raw.get("audio") or [] if isinstance(a, dict)]
    if read_master and fetch_api is not None:
        master = next((s for s in streams if s.get("type") == "hls" and not s.get("height")), None)
        if master is not None:
            details = _master_details(master, referer, fetch_api)
            variants = sorted(details["variants"], key=lambda v: (v["height"], v["bitrate"]), reverse=True)[:MAX_VARIANTS]
            extra = []
            for v in variants:
                if v["url"] == master["url"] or any(s["url"] == v["url"] for s in streams):
                    continue
                item = {"url": v["url"], "type": "hls", "quality": _quality(v["height"]), "label": _quality(v["height"]),
                        "width": v["width"] or None, "height": v["height"] or None, "bitrate": v["bitrate"] or None}
                for key in ("request_headers", "stream_proxy"):
                    if master.get(key):
                        item[key] = master[key]
                extra.append(item)
            if extra:
                index = streams.index(master) + 1
                streams[index:index] = extra
            seen = {(a.get("lang"), a.get("label")) for a in audio}
            audio += [a for a in details["audio"] if (a.get("lang"), a.get("label")) not in seen]
            known = {t["url"] for t in subtitles}
            subtitles += [t for t in details["subtitles"] if t["url"] not in known]
    if not streams:
        return None
    best = streams[0]
    duration = raw.get("duration_s", raw.get("duration")) or 0
    out = {**raw, "provider": raw.get("provider") or "", "streams": streams, "subtitles": subtitles, "audio": audio,
           "duration_s": int(duration) if isinstance(duration, (int, float)) else 0,
           "measured": {**(raw.get("measured") or {}), "resolve_ms": int(resolve_ms)},
           "url": raw.get("url") or best["url"], "type": raw.get("type") or best.get("type"),
           "quality": raw.get("quality") or best.get("quality")}
    out["duration"] = out["duration_s"]
    return out
