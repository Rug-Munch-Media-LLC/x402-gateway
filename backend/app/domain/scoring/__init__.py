"""Public half of the x402 scoring seam (open code, closed calibration).

Call sites import :func:`load_policy` and the pure helpers; the component
weights, verdict thresholds and similarity cut-offs come from the resolved
policy, never from literals in a router.

Resolution order (see :mod:`app.domain.scoring.policy`):

1. ``X402_SCORING_POLICY=module:callable`` environment variable;
2. the ``x402.scoring_policy`` entry point;
3. :func:`example_policy` - illustrative values, loudly logged.
"""

from app.domain.scoring.bands import band_for
from app.domain.scoring.composite import weighted_composite
from app.domain.scoring.example import example_policy
from app.domain.scoring.policy import (
    ENTRY_POINT_GROUP,
    ENV_VAR,
    Policy,
    PolicyUnavailable,
    ScoringPolicy,
    load_policy,
    reset_policy,
)
from app.domain.scoring.similarity import SIMILARITY_LABELS, similarity_tier

__all__ = [
    "SIMILARITY_LABELS",
    "ENTRY_POINT_GROUP",
    "ENV_VAR",
    "Policy",
    "PolicyUnavailable",
    "ScoringPolicy",
    "band_for",
    "example_policy",
    "load_policy",
    "reset_policy",
    "similarity_tier",
    "weighted_composite",
]
