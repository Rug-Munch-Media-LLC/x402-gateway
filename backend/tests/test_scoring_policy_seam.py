"""Contract tests for the public half of the x402 scoring seam.

They must pass in a bare clone: no calibrated provider, no credentials, no
network. They pin three things:

1. the interface (``ScoringPolicy``) and the pure helpers;
2. the example policy is illustrative and clearly labelled as such;
3. the calibration is NOT in this repository - neither in the policy package
   nor as a numeric table in the routers that consume it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.domain.scoring import (
    Policy,
    PolicyUnavailable,
    ScoringPolicy,
    band_for,
    example_policy,
    load_policy,
    reset_policy,
    similarity_tier,
    weighted_composite,
)

ROUTERS = Path(__file__).resolve().parents[1] / "app" / "routers"
SCORING_ROUTERS = (
    ROUTERS / "x402_alpha_tools.py",
    ROUTERS / "x402_alpha_revenue_tools.py",
    ROUTERS / "x402_premium_tools.py",
)
TABLE_NAMES = {
    "weights",
    "bands",
    "cutoffs",
    "floors",
    "penalties",
    "thresholds",
    "scoring",
}


def _fixture_policy() -> Policy:
    return Policy.from_spec(
        {
            "name": "fixture",
            "composite_weights": {"a": 2, "b": 1, "c": 1},
            "composite_bands": {"strong_buy": 90, "buy": 70, "hold": 50},
            "rug_bands": {"critical": 90, "high": 60},
            "similarity_cutoffs": {"strong": 0.9, "possible": 0.5},
            "similarity_penalties": {"strong": 50, "possible": 25},
        }
    )


# ── engine ────────────────────────────────────────────────────────────


def test_example_policy_satisfies_the_protocol() -> None:
    assert isinstance(example_policy(), ScoringPolicy)


def test_example_policy_is_not_a_reduced_copy_of_the_method() -> None:
    example = example_policy()
    assert example.name.startswith("example")
    assert len(set(example.composite_weights.values())) == 1


def test_composite_follows_the_resolved_weights() -> None:
    policy = _fixture_policy()
    components = {"a": {"score": 80}, "b": {"score": 40}, "c": {"score": 20}}
    expected = (80 * 2 + 40 * 1 + 20 * 1) / (2 + 1 + 1)
    assert weighted_composite(components, policy.composite_weights) == round(
        expected, 1
    )


def test_composite_renormalises_and_keeps_the_documented_default() -> None:
    # A component that published a score but no weight does not dilute the blend;
    # one that published no score still contributes the documented default (50).
    policy = _fixture_policy()
    unweighted = {"a": {"score": 80}, "z": {"score": 10}}
    assert weighted_composite(unweighted, policy.composite_weights) == 80.0
    scoreless = {"a": {"score": 80}, "b": {"error": "upstream down"}}
    assert weighted_composite(scoreless, policy.composite_weights) == 100.0


def test_composite_is_clamped_and_fails_open() -> None:
    policy = _fixture_policy()
    assert weighted_composite({"a": {"score": 500}}, policy.composite_weights) == 100.0
    assert weighted_composite({}, policy.composite_weights) == 0.0


def test_bands_pick_the_highest_floor_and_default_below() -> None:
    policy = _fixture_policy()
    assert band_for(95, policy.composite_bands, default="avoid") == "strong_buy"
    assert band_for(90, policy.composite_bands, default="avoid") == "strong_buy"
    assert band_for(69.9, policy.composite_bands, default="avoid") == "hold"
    assert band_for(1, policy.composite_bands, default="avoid") == "avoid"


def test_similarity_cutoffs_are_strictly_exceeded() -> None:
    cutoffs = _fixture_policy().similarity_cutoffs
    assert similarity_tier(0.91, cutoffs) == "strong"
    assert similarity_tier(0.9, cutoffs) == "possible"
    assert similarity_tier(0.5, cutoffs) == ""
    assert similarity_tier(0.0, cutoffs) == ""


def test_bad_spec_is_rejected() -> None:
    with pytest.raises(PolicyUnavailable):
        Policy.from_spec({"name": "x"})
    with pytest.raises(PolicyUnavailable):
        Policy.from_spec({**_fixture_policy().__dict__, "composite_weights": {}})


# ── resolution ────────────────────────────────────────────────────────


def test_load_policy_is_cached_and_resettable() -> None:
    first = load_policy()
    assert load_policy() is first
    reset_policy()
    assert load_policy() is not first


def test_named_provider_that_cannot_be_imported_is_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("X402_SCORING_POLICY", "does.not.exist:default_policy")
    reset_policy()
    with pytest.raises(PolicyUnavailable):
        load_policy()
    reset_policy()


def test_env_provider_is_used_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X402_SCORING_POLICY", "scoring_fixture:default_policy")
    reset_policy()
    assert load_policy().name == "fixture-policy"
    reset_policy()


# ── the point: the recipe is not in these files ───────────────────────


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_unit_weight(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, float)
        and 0.0 < node.value < 1.0
    )


def _assigned_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def test_scoring_routers_import_the_seam_not_a_table() -> None:
    for path in SCORING_ROUTERS:
        tree = _parse(path)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "app.domain.scoring"
            for alias in node.names
        }
        assert "load_policy" in imported, (
            f"{path.name} must resolve the policy from the seam"
        )
        for node in ast.walk(tree):
            for name in _assigned_names(node):
                assert name.lower() not in TABLE_NAMES, (
                    f"{path.name}: local scoring table {name!r}"
                )


def test_no_numeric_weight_table_reappears() -> None:
    for path in SCORING_ROUTERS:
        for node in ast.walk(_parse(path)):
            if not isinstance(node, ast.Dict):
                continue
            unit_weights = [
                value
                for value in node.values
                if value is not None and _is_unit_weight(value)
            ]
            assert len(unit_weights) < 2, (
                f"{path.name}: numeric weight table at line {node.lineno}"
            )


def test_no_similarity_cutoff_literals_reappear() -> None:
    for path in SCORING_ROUTERS:
        for node in ast.walk(_parse(path)):
            if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name):
                continue
            if "sim" not in node.left.id.lower():
                continue
            for comparator in node.comparators:
                assert not _is_unit_weight(comparator), (
                    f"{path.name}: similarity cut-off literal at line {node.lineno}; "
                    "it belongs in the resolved policy"
                )


def test_no_verdict_threshold_literals_reappear() -> None:
    watched = {"composite", "rug_score", "sim_score", "trust_score"}
    for path in SCORING_ROUTERS:
        for node in ast.walk(_parse(path)):
            if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name):
                continue
            if node.left.id not in watched:
                continue
            for comparator in node.comparators:
                assert not isinstance(comparator, ast.Constant), (
                    f"{path.name}: verdict threshold {node.left.id} vs {comparator.value!r} "
                    f"at line {node.lineno}; it belongs in the resolved policy"
                )
