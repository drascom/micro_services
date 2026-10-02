"""Drift detection: compare a run's metrics against a per-site baseline.

Baseline (``configs/<site>.baseline.json``) declares the expected healthy range;
any of the following trips drift:
  * zero items parsed
  * item count below ``min_items``
  * aggregate field fill ratio below ``min_fill_ratio`` (default 0.5)
  * a specific critical field's fill below ``critical_field_fill``
"""
from __future__ import annotations

from typing import Any


def detect(metrics: dict, baseline: dict[str, Any]) -> dict:
    reasons: list[str] = []

    n = metrics.get("valid_count", 0)
    if n == 0:
        reasons.append("zero valid items parsed")

    min_items = baseline.get("min_items")
    if min_items is not None and n < min_items:
        reasons.append(f"item count {n} < min_items {min_items}")

    min_fill = baseline.get("min_fill_ratio", 0.5)
    fill = metrics.get("fill_ratio", 0.0)
    if fill < min_fill:
        reasons.append(f"fill_ratio {fill} < min_fill_ratio {min_fill}")

    for fld, thresh in (baseline.get("critical_field_fill") or {}).items():
        got = metrics.get("field_fill", {}).get(fld, 0.0)
        if got < thresh:
            reasons.append(f"field '{fld}' fill {got} < {thresh}")

    return {"drift": bool(reasons), "reasons": reasons}
