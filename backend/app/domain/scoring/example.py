"""A deliberately trivial policy so the repository runs and tests on its own."""

from __future__ import annotations

from app.domain.scoring.policy import Policy


def example_policy() -> Policy:
    """Illustrative only: equal weights and round numbers.

    It is NOT a reduced copy of the calibrated method and must never be
    presented as production scoring.
    """
    return Policy.from_spec(
        {
            "name": "example-v0",
            "composite_weights": {
                "reputation": 1.0,
                "rug_risk": 1.0,
                "market_health": 1.0,
                "narrative": 1.0,
                "mev_exposure": 1.0,
            },
            "composite_bands": {"strong_buy": 80, "buy": 60, "hold": 40, "caution": 20},
            "rug_bands": {"critical": 80, "high": 60, "medium": 40, "low": 20},
            "similarity_cutoffs": {"strong": 0.95, "possible": 0.75},
            "similarity_penalties": {"strong": 10, "possible": 5},
        }
    )
