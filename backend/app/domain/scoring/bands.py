"""Threshold -> band lookup. The thresholds are policy, not code."""

from __future__ import annotations

from collections.abc import Mapping


def band_for(score: float, floors: Mapping[str, float], default: str = "") -> str:
    """Return the highest band whose floor ``score`` reaches.

    ``default`` is returned when the score falls below every floor.
    """
    for name, floor in sorted(floors.items(), key=lambda item: item[1], reverse=True):
        if score >= floor:
            return name
    return default
