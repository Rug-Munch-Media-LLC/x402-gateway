"""What a payment for this tool must satisfy.

Terms declared by the client (x402 v2 ``accepted``) are checked against the
server's requirements *before* any on-chain work. Legacy proofs that declare
only a tx hash are checked on-chain instead, so the accepted wire shapes are
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.x402.payments.config import ATOMS_PER_CENT, PaymentConfig
from app.domain.x402.payments.errors import (
    AmountMismatch,
    AssetMismatch,
    ChainMismatch,
    PaymentExpired,
    RecipientMismatch,
)
from app.domain.x402.payments.header import PaymentProof


@dataclass(frozen=True)
class Requirements:
    """Server-side payment requirements for one tool call."""

    amount_atoms: int
    asset: str
    network: str
    pay_to: str
    max_timeout_seconds: int
    required_confirmations: int


def build_requirements(config: PaymentConfig, price_cents: int) -> Requirements:
    return Requirements(
        amount_atoms=price_cents * ATOMS_PER_CENT,
        asset=config.asset,
        network=config.network,
        pay_to=config.pay_to,
        max_timeout_seconds=config.max_timeout_seconds,
        required_confirmations=config.required_confirmations,
    )


def _check_field(declared: str, expected: Any, error: type, message: str) -> None:
    """One comparison: only declared terms are enforced."""
    if declared and declared.lower() != str(expected).lower():
        raise error(message)


def check_requirements(
    proof: PaymentProof, requirements: Requirements, now_ts: int
) -> None:
    """Reject a proof whose declared terms do not match ours.

    Only declared fields are compared (legacy headers declare none); the
    on-chain verifier enforces the rest. Raises a typed error or returns None.
    """
    if proof.valid_before is not None and proof.valid_before <= now_ts:
        raise PaymentExpired("payment authorization expired")
    if proof.amount and int(proof.amount) != requirements.amount_atoms:
        raise AmountMismatch(f"expected {requirements.amount_atoms} atoms")
    _check_field(proof.asset, requirements.asset, AssetMismatch, "wrong asset")
    _check_field(proof.network, requirements.network, ChainMismatch, "wrong network")
    _check_field(
        proof.pay_to, requirements.pay_to, RecipientMismatch, "wrong recipient"
    )
