"""Audio / subtitle track description of resolved streams (additive API fields, see API.md ``/api/streams``).

A *variant* is one provider file. Two files of one episode may differ only in their subtitles: yabancidizi's Turkish
tab is a hard-subbed encode (the Turkish text is burned into the picture), its English tab is a clean encode that the
provider ships with a soft English VTT. Nothing here is guessed from labels the source does not give: an unknown fact
stays ``None`` / ``"none"`` and the client shows less, never something invented.

Per stream: ``variant_id``, ``audio_lang``, ``sub_mode`` (``hard`` | ``soft`` | ``none``), ``hard_lang``, and the
later additions ``sub_known`` (did the server MEASURE the subtitle state of this file, or is ``sub_mode="none"`` just
"nothing known"), ``site_lang_hint`` (the language the site's tab names: a claim, not a measurement) and ``mirror_of``
(set on a spare copy of another stream of the same file and quality, ``null`` otherwise).
Response root: ``subtitles[]`` (soft tracks, served by ``/api/subtitles/<id>.vtt``) and ``audio[]``.
"""
from __future__ import annotations

import hashlib
from typing import Optional
from urllib.parse import parse_qsl, urlparse

from .. import langs, subtitles

# Providers whose "<language> altyazı" file is an encode with that language burned into the picture (measured for
# VidMolly on yabancidizi: the Turkish file shows Turkish text + the site watermark; OK.ru's files were not measured,
# so they stay ``sub_mode="none"`` unless a soft track is found). Only these providers have a MEASURED subtitle state
# (``sub_known``): for every other one "none" means "not known", and the site's tab language is not put in the label.
HARDSUB_PROVIDERS = ("vidmolly",)
HARDSUB_LANGS = ("tr",)

DEFAULT_FIELDS = {"audio_lang": None, "sub_mode": "none", "hard_lang": None, "sub_known": False, "site_lang_hint": None}


def variant_id(source_id: str, key: str = "", url: str = "") -> str:
    """Stable id of one provider file inside a source: the provider's own file id (``key``) when it gives one, else
    the URL path without host/query (signed CDN URLs change per resolution, the path does not)."""
    basis = key or urlparse(url or "").path or url or ""
    return "v_" + hashlib.sha1(f"{source_id}|{basis}".encode("utf-8")).hexdigest()[:12]


def describe(provider: str, lang: Optional[str], language_text: str, soft_tracks: list[dict]) -> dict:
    """``audio_lang`` / ``sub_mode`` / ``hard_lang`` of one provider file.

    ``lang`` / ``language_text`` come from the catalogue page's language tab (``"tr"`` / ``"Türkçe altyazı"``,
    ``"Türkçe dublaj"``), ``soft_tracks`` from the provider's embed page. Rules:
    - a dubbed page: the audio is that language;
    - soft tracks present: ``soft``; ``captions`` tracks transcribe the speech, so their language is the audio language;
    - no soft track, a Turkish-subtitle file of a hard-sub provider: ``hard`` + ``hard_lang``;
    - anything else: unknown (``audio_lang=None``, ``sub_mode="none"``).

    ``sub_known`` is true for a provider whose subtitle state was measured (:data:`HARDSUB_PROVIDERS`) or when soft
    tracks were found; ``site_lang_hint`` is the tab's language code as the site states it (``None`` without a tab).
    """
    lang = (lang or "").lower() or None
    dubbed = "dublaj" in (language_text or "").casefold()
    fields = dict(DEFAULT_FIELDS)
    fields["site_lang_hint"] = lang
    fields["sub_known"] = bool(soft_tracks) or (provider or "").casefold() in HARDSUB_PROVIDERS
    if dubbed and lang:
        fields["audio_lang"] = lang
    if soft_tracks:
        fields["sub_mode"] = "soft"
        if not dubbed:
            captions = next((t for t in soft_tracks if t.get("kind") == "captions" and t.get("lang")), None)
            if captions:
                fields["audio_lang"] = captions["lang"]
    elif not dubbed and lang in HARDSUB_LANGS and (provider or "").casefold() in HARDSUB_PROVIDERS:
        fields["sub_mode"] = "hard"
        fields["hard_lang"] = lang
    return fields


def label_language(fields: dict, language_text: Optional[str]) -> Optional[str]:
    """The language part of a menu label ("Türkçe altyazı"), or ``None`` when it must not be shown.

    The catalogue page's tab names a language for every provider file, but for a provider whose subtitle state was not
    measured (``sub_known`` false, e.g. OK.ru) that is only the site's claim about the tab: the label then carries
    "<provider> · <quality>" alone. A dub tab stays: it says which audio the file has, and ``audio_lang`` already
    follows it."""
    if fields.get("sub_known") or "dublaj" in (language_text or "").casefold():
        return language_text or None
    return None


_VOLATILE_QUERY = frozenset({"s", "t", "e", "sp", "sig", "signature", "expires", "expire", "exp", "token", "ts", "st",
                             "hash", "md5"})


def file_identity(stream: dict) -> tuple:
    """What makes two resolutions of the same provider file the same stream: provider + host + path.

    Signed CDN URLs differ per resolution only in their query (``s``/``t``/``e`` ...), so two candidates that lead to
    one file (the site's ``/dl/<code>`` link and its ``/api/moly`` hand-off) must not count as two streams. A URL
    without a file path (OK.ru serves ``https://<cdn>/?id=..&type=..&sig=..``) keeps its query, minus the signing
    keys, because there the query names the file."""
    url = urlparse(str(stream.get("url") or ""))
    path = url.path or "/"
    query = ()
    if path == "/":
        query = tuple(sorted((k, v) for k, v in parse_qsl(url.query, keep_blank_values=True)
                             if k.lower() not in _VOLATILE_QUERY))
    return (str(stream.get("provider") or "").casefold(), (url.hostname or "").lower(), path, query)


def mirror_key(stream: dict) -> Optional[str]:
    """``<variant_id>:<quality>``: the file (variant) at one quality; two streams with the same key show the same
    video and differ only in where it is served from."""
    variant = stream.get("variant_id")
    return f"{variant}:{stream.get('quality') or ''}" if variant else None


def link_mirrors(streams: list[dict]) -> list[dict]:
    """Mark spare copies and keep them next to the stream they back up.

    Streams that share :func:`mirror_key` (same file, same quality, but another host/path: a real second CDN edge,
    not just a re-signed URL, see :func:`file_identity`) form a group: the first one stays as it is
    (``mirror_of: None``), the others get ``mirror_of`` = that key and follow it immediately. Order otherwise
    unchanged; streams without a ``variant_id`` are left alone."""
    slots: list[list[dict]] = []
    by_key: dict[str, list[dict]] = {}
    for stream in streams:
        key = mirror_key(stream)
        if key is None:
            slots.append([stream])
        elif key in by_key:
            by_key[key].append({**stream, "mirror_of": key})
        else:
            by_key[key] = [{**stream, "mirror_of": None}]
            slots.append(by_key[key])
    return [stream for slot in slots for stream in slot]


def share_audio(streams: list[dict]) -> None:
    """A hard-sub file of a provider has the audio of the clean file the same provider serves for the same source
    (the site's "altyazılı" tabs are one video with different subtitles); fill an unknown audio language from it."""
    for stream in streams:
        if stream.get("sub_mode") != "hard" or stream.get("audio_lang"):
            continue
        sibling = next((s for s in streams if s.get("provider") == stream.get("provider")
                        and s.get("source_id") == stream.get("source_id")
                        and s.get("sub_mode") == "soft" and s.get("audio_lang")), None)
        if sibling:
            stream["audio_lang"] = sibling["audio_lang"]


def with_defaults(stream: dict, source_id: str) -> dict:
    """Stream dict carrying every track field (cached payloads from before this feature, direct/embed sources)."""
    out = {**DEFAULT_FIELDS, "mirror_of": None, **stream}
    if not out.get("variant_id"):
        out["variant_id"] = variant_id(source_id, "", out.get("url") or "")
    return out


def public_subtitles(tracks: list[dict]) -> list[dict]:
    """API ``subtitles[]`` from resolved soft tracks (``url`` = provider source, ``referer`` = its embed page,
    ``variant_id`` = the file it belongs to): each source URL is registered with the proxy and replaced by
    ``/api/subtitles/<id>.vtt``; the same track offered by several sources appears once."""
    out: dict[str, dict] = {}
    for track in tracks:
        sid = subtitles.register(track["url"], referer=track.get("referer") or "")
        if sid is None:   # host not on the allow-list: the proxy would refuse it, so it is not advertised
            continue
        item = out.get(sid)
        if item is None:
            item = out[sid] = {
                "id": sid, "lang": track.get("lang"),
                "label": track.get("label") or langs.label(track.get("lang")) or "Altyazı",
                "kind": track.get("kind") or "captions", "format": "vtt", "url": f"/api/subtitles/{sid}.vtt",
                "stream_ids": [] if track.get("variant_id") else None, "default": bool(track.get("default")),
                "origin": "soft",
            }
        vid = track.get("variant_id")
        if vid and item["stream_ids"] is not None and vid not in item["stream_ids"]:
            item["stream_ids"].append(vid)
    return list(out.values())


def public_audio(streams: list[dict]) -> list[dict]:
    """API ``audio[]``: the distinct audio languages of the playable (non-embed, non-trailer) streams, first = default;
    an unknown language is one ``lang=None`` entry labelled "Orijinal"."""
    groups: dict[Optional[str], dict] = {}
    for stream in streams:
        if stream.get("kind") == "trailer" or stream.get("type") == "embed":
            continue
        lang = stream.get("audio_lang") or None
        group = groups.get(lang)
        if group is None:
            group = groups[lang] = {"id": "a_" + (lang or "orig"), "lang": lang,
                                    "label": langs.label(lang) or "Orijinal", "stream_ids": [], "default": not groups}
        if stream["variant_id"] not in group["stream_ids"]:
            group["stream_ids"].append(stream["variant_id"])
    return list(groups.values())
