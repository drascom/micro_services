"""Central genre dictionary (id -> Turkish label) and site-label -> canonical-label mapping.

``GENRES`` is the single source for ``/api/catalog`` / ``/api/genres``; ``library_items.genres`` stores the
Turkish labels. A source's own genre words are mapped onto those labels by ``canonical_labels``; words the
dictionary does not know are returned separately (kept for review, never shown as a genre).
"""
from __future__ import annotations

import unicodedata

GENRES = dict(zip(
    'action comedy drama horror science_fiction thriller romance animation documentary adventure crime fantasy family war history music western mystery biography sport youth children'.split(),
    'Aksiyon|Komedi|Dram|Korku|Bilim Kurgu|Gerilim|Romantik|Animasyon|Belgesel|Macera|Suç|Fantastik|Aile|Savaş|Tarih|Müzikal|Western|Gizem|Biyografi|Spor|Gençlik|Çocuk'.split('|')))


def fold(text: str) -> str:
    """Lower-case, diacritic-free key ("Bilim-Kurgu" -> "bilim kurgu", "SUÇ" -> "suc")."""
    text = (text or "").replace("ı", "i").replace("İ", "i")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


_BY_FOLD = {fold(label): label for label in GENRES.values()}

# Spellings / compound labels used by sources -> one or more canonical labels.
_ALIASES = {
    "muzik": ["Müzikal"], "muzikal": ["Müzikal"], "musical": ["Müzikal"], "music": ["Müzikal"],
    "sci fi": ["Bilim Kurgu"], "science fiction": ["Bilim Kurgu"], "bilimkurgu": ["Bilim Kurgu"],
    "fantazi": ["Fantastik"], "fantasy": ["Fantastik"],
    "aksiyon macera": ["Aksiyon", "Macera"], "aksiyon ve macera": ["Aksiyon", "Macera"],
    "action adventure": ["Aksiyon", "Macera"],
    "bilim kurgu fantazi": ["Bilim Kurgu", "Fantastik"], "sci fi fantasy": ["Bilim Kurgu", "Fantastik"],
    "cocuklar": ["Çocuk"], "genclik": ["Gençlik"],
    "polisiye": ["Suç"], "crime": ["Suç"], "mystery": ["Gizem"], "gizem gerilim": ["Gizem", "Gerilim"],
    "komedi drama": ["Komedi", "Dram"],
}


def canonical_labels(names) -> tuple[list[str], list[str]]:
    """``(mapped, unmapped)``: canonical Turkish labels (deduplicated, source order) and the words left over."""
    mapped: list[str] = []
    unmapped: list[str] = []
    for raw in names or []:
        key = fold(str(raw))
        if not key:
            continue
        labels = [_BY_FOLD[key]] if key in _BY_FOLD else _ALIASES.get(key)
        if not labels:
            if str(raw).strip() not in unmapped:
                unmapped.append(str(raw).strip())
            continue
        for label in labels:
            if label not in mapped:
                mapped.append(label)
    return mapped, unmapped
