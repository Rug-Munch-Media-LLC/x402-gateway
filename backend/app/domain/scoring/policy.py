"""Resolve the scoring calibration from a private provider.

The public contract is the *shape* of a policy spec::

    {
      "name": str,
      "composite_weights": {component: weight, ...},
      "composite_bands": {band: minimum_composite, ...},
      "rug_bands": {band: minimum_rug_score, ...},
      "similarity_cutoffs": {tier: minimum_similarity, ...},
      "similarity_penalties": {tier: points_to_subtract, ...},
    }

The values themselves are not in this repository. Penalties are whole points,
so a score stays an integer; thresholds accept any number.
"""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import entry_points
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

ENV_VAR = "X402_SCORING_POLICY"
ENTRY_POINT_GROUP = "x402.scoring_policy"

REQUIRED_KEYS = (
    "name",
    "composite_weights",
    "composite_bands",
    "rug_bands",
    "similarity_cutoffs",
    "similarity_penalties",
)


class PolicyUnavailable(RuntimeError):
    """A provider was named but could not be loaded or is malformed."""


@runtime_checkable
class ScoringPolicy(Protocol):
    """What the scorers need from a calibration provider."""

    name: str
    composite_weights: Mapping[str, float]
    composite_bands: Mapping[str, float]
    rug_bands: Mapping[str, float]
    similarity_cutoffs: Mapping[str, float]
    similarity_penalties: Mapping[str, int]


@dataclass(frozen=True)
class Policy:
    """A validated calibration spec. It carries no method of its own."""

    name: str
    composite_weights: Mapping[str, float]
    composite_bands: Mapping[str, float]
    rug_bands: Mapping[str, float]
    similarity_cutoffs: Mapping[str, float]
    similarity_penalties: Mapping[str, int]

    @classmethod
    def from_spec(cls, spec: Mapping[str, Any]) -> Policy:
        """Validate a provider spec; reject anything a scorer cannot use."""
        missing = [key for key in REQUIRED_KEYS if key not in spec]
        if missing:
            raise PolicyUnavailable(f"policy spec missing keys: {missing}")
        weights = {str(k): float(v) for k, v in spec["composite_weights"].items()}
        if not weights or any(weight <= 0 for weight in weights.values()):
            raise PolicyUnavailable("composite_weights must be non-empty and positive")
        for key in ("composite_bands", "rug_bands", "similarity_cutoffs"):
            if not spec[key]:
                raise PolicyUnavailable(f"{key} must not be empty")
        return cls(
            name=str(spec["name"]),
            composite_weights=weights,
            composite_bands={str(k): v for k, v in spec["composite_bands"].items()},
            rug_bands={str(k): v for k, v in spec["rug_bands"].items()},
            similarity_cutoffs={
                str(k): v for k, v in spec["similarity_cutoffs"].items()
            },
            similarity_penalties={
                str(k): int(v) for k, v in spec["similarity_penalties"].items()
            },
        )


def _provider_from_env() -> Callable[[], Mapping[str, Any]] | None:
    target = os.environ.get(ENV_VAR, "").strip()
    if not target:
        return None
    module_name, _, attr = target.partition(":")
    attr = attr or "default_policy"
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise PolicyUnavailable(
            f"{ENV_VAR}={target!r} could not be imported: {exc}"
        ) from exc
    provider = getattr(module, attr, None)
    if not callable(provider):
        raise PolicyUnavailable(f"{ENV_VAR}={target!r} has no callable {attr!r}")
    return provider


def _provider_from_entry_point() -> Callable[[], Mapping[str, Any]] | None:
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        return entry.load()
    return None


@lru_cache(maxsize=1)
def load_policy() -> Policy:
    """Resolve the calibrated policy; fall back to the example, loudly."""
    provider = _provider_from_env()
    source = ENV_VAR
    if provider is None:
        provider = _provider_from_entry_point()
        source = f"entry point {ENTRY_POINT_GROUP!r}"
    if provider is None:
        from app.domain.scoring.example import example_policy

        policy = example_policy()
        logger.warning(
            "scoring: no calibrated provider (%s unset, no %r entry point). "
            "Using %s - scores are ILLUSTRATIVE. Install the private policy package "
            "or a deployment is misconfigured.",
            ENV_VAR,
            ENTRY_POINT_GROUP,
            policy.name,
        )
        return policy
    spec = provider()
    if isinstance(spec, Policy):
        return spec
    logger.info("scoring: loaded %r from %s", str(spec.get("name")), source)
    return Policy.from_spec(spec)


def reset_policy() -> None:
    """Drop the cached policy (tests and hot-reload)."""
    load_policy.cache_clear()
