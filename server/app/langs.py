"""Language codes and Turkish display names for audio/subtitle tracks (shared by providers and the API layer).

Only what a track label or file name can honestly say is decided here: :func:`code` returns ``None`` for text it does
not recognise, so callers leave the language out instead of guessing.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import unquote, urlparse

# code -> (Turkish label, lower-case aliases without diacritic quirks: see _fold)
_LANGS: dict[str, tuple[str, tuple[str, ...]]] = {
    "tr": ("Türkçe", ("tr", "tur", "turkish", "turkce", "turk")),
    "en": ("İngilizce", ("en", "eng", "english", "ingilizce")),
    "de": ("Almanca", ("de", "deu", "ger", "german", "deutsch", "almanca")),
    "fr": ("Fransızca", ("fr", "fra", "fre", "french", "francais", "fransizca")),
    "es": ("İspanyolca", ("es", "spa", "spanish", "espanol", "castellano", "ispanyolca")),
    "it": ("İtalyanca", ("it", "ita", "italian", "italiano", "italyanca")),
    "pt": ("Portekizce", ("pt", "por", "portuguese", "portugues", "portekizce")),
    "ru": ("Rusça", ("ru", "rus", "russian", "rusca")),
    "ar": ("Arapça", ("ar", "ara", "arabic", "arapca")),
    "ja": ("Japonca", ("ja", "jpn", "jap", "japanese", "japonca")),
    "ko": ("Korece", ("ko", "kor", "korean", "korece")),
    "zh": ("Çince", ("zh", "zho", "chi", "chinese", "cince", "mandarin")),
    "nl": ("Felemenkçe", ("nl", "nld", "dut", "dutch", "nederlands", "felemenkce", "flemenkce")),
    "pl": ("Lehçe", ("pl", "pol", "polish", "polski", "lehce")),
    "sv": ("İsveççe", ("sv", "swe", "swedish", "svenska", "isvecce")),
    "da": ("Danca", ("da", "dan", "danish", "dansk", "danca")),
    "fi": ("Fince", ("fi", "fin", "finnish", "suomi", "fince")),
    "no": ("Norveççe", ("no", "nor", "norwegian", "norsk", "norvecce")),
    "hi": ("Hintçe", ("hi", "hin", "hindi", "hintce")),
    "fa": ("Farsça", ("fa", "fas", "per", "persian", "farsi", "farsca")),
    "el": ("Yunanca", ("el", "ell", "gre", "greek", "yunanca")),
    "ro": ("Romence", ("ro", "ron", "rum", "romanian", "romence")),
    "hu": ("Macarca", ("hu", "hun", "hungarian", "magyar", "macarca")),
    "cs": ("Çekçe", ("cs", "ces", "cze", "czech", "cekce")),
    "bg": ("Bulgarca", ("bg", "bul", "bulgarian", "bulgarca")),
    "uk": ("Ukraynaca", ("uk", "ukr", "ukrainian", "ukraynaca")),
    "he": ("İbranice", ("he", "heb", "hebrew", "ibranice")),
    "id": ("Endonezce", ("id", "ind", "indonesian", "endonezce")),
    "th": ("Tayca", ("th", "tha", "thai", "tayca")),
    "vi": ("Vietnamca", ("vi", "vie", "vietnamese", "vietnamca")),
}
_ALIAS: dict[str, str] = {alias: code for code, (_, aliases) in _LANGS.items() for alias in aliases}
_WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)
_TRANSLIT = str.maketrans({"ı": "i", "ş": "s", "ç": "c", "ğ": "g", "ö": "o", "ü": "u", "é": "e", "è": "e", "ñ": "n",
                           "ã": "a", "í": "i", "ó": "o", "á": "a", "ê": "e", "ô": "o"})


def _fold(text: str) -> str:
    """Case-fold + strip Turkish/Latin diacritics (``İngilizce`` -> ``ingilizce``)."""
    return (text or "").replace("İ", "i").replace("I", "ı").casefold().replace("̇", "").translate(_TRANSLIT)


def code(text: Optional[str]) -> Optional[str]:
    """Language code named by a label / ``srclang`` (``"English"``, ``"Türkçe"``, ``"tr"``, ``"en_US"``, ``"Turkish (SDH)"``).
    A full language name counts anywhere in the text, a 2-3 letter code only as its first word (``"it"``/``"no"`` inside
    a sentence are not languages); ``None`` when the text does not name a language."""
    for position, word in enumerate(_WORDS.findall(_fold(text or ""))):
        found = _ALIAS.get(word)
        if found and (len(word) > 3 or position == 0):
            return found
    return None


def file_lang(url: Optional[str]) -> Optional[str]:
    """Language named by a track file name such as ``<file-code>_English.vtt`` (words counted from the end)."""
    name = unquote(urlparse(str(url or "")).path.rsplit("/", 1)[-1])
    stem = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name)
    for word in reversed(re.split(r"[_.\-\s]+", stem)):
        found = _ALIAS.get(_fold(word)) if word.isalpha() else None
        if found:
            return found
    return None


def label(lang: Optional[str]) -> Optional[str]:
    """Turkish display name of a language code (``None`` when unknown)."""
    entry = _LANGS.get((lang or "").lower())
    return entry[0] if entry else None
