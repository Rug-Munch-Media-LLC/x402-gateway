"""Invented provider used by the seam tests. Round numbers, never production."""

from __future__ import annotations


def default_policy() -> dict:
    return {
        "name": "fixture-policy",
        "composite_weights": {"reputation": 2, "rug_risk": 1, "market_health": 1},
        "composite_bands": {"strong_buy": 90, "buy": 70, "hold": 50, "caution": 25},
        "rug_bands": {"critical": 90, "high": 60, "medium": 40, "low": 10},
        "similarity_cutoffs": {"strong": 0.9, "possible": 0.5},
        "similarity_penalties": {"strong": 50, "possible": 25},
    }
