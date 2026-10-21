"""Target schemas + validation/quality metrics.

Multiple schemas are supported and resolved by name (``SCHEMAS`` registry) so a
new site can declare a different ``schema:`` in its yaml. sinemalar uses
``MovieItem``.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ValidationError


class VideoSource(BaseModel):
    url: str
    kind: str = "movie"
    resolver: str = "direct"
    type: str = "mp4"
    key: Optional[str] = None
    label: Optional[str] = None
    language: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    title: Optional[str] = None
    overview: Optional[str] = None
    still_url: Optional[str] = None
    runtime: Optional[int] = None


class MovieItem(BaseModel):
    tmdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    video_sources: list[VideoSource] = []
    title: str
    original_title: Optional[str] = None
    year: Optional[int] = None
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    genres: list[str] = []
    synopsis: Optional[str] = None
    rating: Optional[float] = None
    runtime: Optional[int] = None
    country: Optional[str] = None
    followers: Optional[int] = None
    cast: list[str] = []
    trailer_url: Optional[str] = None
    detail_url: Optional[str] = None


class HomepageItem(MovieItem):
    featured: Optional[str] = None
    """Mixed film/series cards; homepage metadata is intentionally sparse."""
    season: Optional[int] = None
    episode: Optional[int] = None
    trend_score: Optional[int] = None


#: name -> pydantic model. Register new schemas here for new sites.
SCHEMAS: dict[str, type[BaseModel]] = {
    "MovieItem": MovieItem,
    "HomepageItem": HomepageItem,
}

#: per-schema fields that make an item "worth counting" (used for fill metrics).
_KEY_FIELDS: dict[str, list[str]] = {
    "HomepageItem": ["title", "poster_url", "detail_url"],
    "MovieItem": ["title", "poster_url", "detail_url", "year", "genres", "synopsis"],
}


def get_schema(name: str) -> type[BaseModel]:
    if name not in SCHEMAS:
        raise KeyError(f"unknown schema {name!r}; known: {sorted(SCHEMAS)}")
    return SCHEMAS[name]


def _filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, dict)) and len(value) == 0:
        return False
    return True


def validate_items(schema_name: str, raw_items: list[dict]) -> tuple[list[dict], dict]:
    """Validate raw dicts against ``schema_name``.

    Returns ``(valid_items, metrics)``. ``metrics`` carries item counts, per-field
    fill ratios and an aggregate ``fill_ratio`` over the schema's key fields —
    everything drift detection and the dashboard need.
    """
    model = get_schema(schema_name)
    key_fields = _KEY_FIELDS.get(schema_name, list(model.model_fields))

    valid: list[dict] = []
    errors = 0
    for raw in raw_items:
        try:
            valid.append(model(**raw).model_dump())
        except ValidationError:
            errors += 1

    n = len(valid)
    field_fill: dict[str, float] = {}
    for f in key_fields:
        hits = sum(1 for it in valid if _filled(it.get(f)))
        field_fill[f] = round(hits / n, 3) if n else 0.0
    agg = round(sum(field_fill.values()) / len(field_fill), 3) if field_fill else 0.0

    metrics = {
        "schema": schema_name,
        "raw_count": len(raw_items),
        "valid_count": n,
        "invalid_count": errors,
        "field_fill": field_fill,
        "fill_ratio": agg,
    }
    return valid, metrics
