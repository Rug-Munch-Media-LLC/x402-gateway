"""Pure payment verification against an injected chain reader.

The reader is the only I/O: tests pass a fake, production passes the web3
adapter. A verifier failure raises; it never returns a falsy value that could
be ignored, and nothing here can settle a payment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.domain.x402.payments.errors import (
    AmountMismatch,
    RecipientMismatch,
    VerificationFailed,
)
from app.domain.x402.payments.header import PaymentProof
from app.domain.x402.payments.requirements import Requirements


@dataclass(frozen=True)
class ChainObservation:
    """What the chain says about a payment."""

    success: bool
    value_atoms: int
    pay_to: str
    confirmations: int
    settled_tx_hash: str = ""


class ChainReader(Protocol):
    """Minimal on-chain read surface (adapter lives outside this module)."""

    async def observe(self, proof: PaymentProof) -> ChainObservation | None: ...


@dataclass(frozen=True)
class VerifiedPayment:
    """Proof that passed verification. Only this type may be settled."""

    proof: PaymentProof
    observation: ChainObservation
    tx_hash: str
    payer: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def paid_amount_atoms(self) -> int:
        return self.observation.value_atoms


async def verify_payment(
    proof: PaymentProof, requirements: Requirements, reader: ChainReader
) -> VerifiedPayment:
    """Verify a proof on-chain, raising VerificationFailed on any doubt."""
    if not requirements.pay_to:
        raise VerificationFailed("gateway pay-to address is not configured")

    observation = await reader.observe(proof)
    if observation is None:
        raise VerificationFailed("payment transaction not found")
    if not observation.success:
        raise VerificationFailed("payment transaction failed on-chain")
    if observation.pay_to.lower() != requirements.pay_to.lower():
        raise RecipientMismatch("payment went to the wrong recipient")
    if observation.value_atoms < requirements.amount_atoms:
        raise AmountMismatch(
            f"paid {observation.value_atoms} atoms, need {requirements.amount_atoms}"
        )
    if observation.confirmations < requirements.required_confirmations:
        raise VerificationFailed(
            f"only {observation.confirmations} confirmations, "
            f"need {requirements.required_confirmations}"
        )

    tx_hash = proof.tx_hash or observation.settled_tx_hash
    if not tx_hash:
        raise VerificationFailed("verified payment has no transaction hash")
    return VerifiedPayment(proof=proof, observation=observation, tx_hash=tx_hash)
