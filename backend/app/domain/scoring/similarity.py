"""Similarity -> tier lookup. The cut-offs are policy, not code."""

from __future__ import annotations

from collections.abc import Mapping


def similarity_tier(score: float, cutoffs: Mapping[str, float]) -> str:
    """Return the highest tier whose cut-off ``score`` strictly exceeds.

    Strictness is part of the contract: a similarity exactly on a cut-off
    falls to the next tier down, and ``""`` means "below every cut-off".
    """
    for name, cutoff in sorted(cutoffs.items(), key=lambda item: item[1], reverse=True):
        if score > cutoff:
            return name
    return ""

#: Public flag wording per tier. The cut-offs that select them and the points
#: they subtract are calibration; only the wording is open.
SIMILARITY_LABELS: Mapping[str, tuple[str, str]] = {
    "strong": ("high", "Strong scam pattern match"),
    "possible": ("medium", "Possible scam pattern"),
}
