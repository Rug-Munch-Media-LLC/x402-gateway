"""Blend component scores by weight. The weights are policy, not code."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def weighted_composite(
    components: Mapping[str, Mapping[str, Any]],
    weights: Mapping[str, float],
) -> float:
    """Return the 0-100 weighted blend of ``components[*]["score"]``.

    Weights are renormalised over the components that actually published a
    score, so a missing upstream degrades the blend instead of zeroing it.
    """
    if not components:
        return 0.0
    available_weight = sum(
        weights.get(key, 0.0) for key in components if "score" in components[key]
    )
    total = sum(
        components[key].get("score", 50) * weights.get(key, 0.0) for key in components
    )
    return round(max(0.0, min(100.0, total / max(available_weight, 0.01))), 1)
