"""Settlement — the only place a payment is finalized.

Settlement receives a :class:`VerifiedPayment` and nothing else, so an
unverified or failed payment cannot reach the settler: the type is only
produced by the verifier, and the guard below rejects anything that is not a
successful observation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from app.domain.x402.payments.errors import SettlementError, VerificationFailed
from app.domain.x402.payments.verifier import VerifiedPayment

logger = logging.getLogger("x402_settle")


@dataclass(frozen=True)
class SettlementReceipt:
    """Result of a successful settlement."""

    tx_hash: str
    network: str
    amount_atoms: int


class SettlementClient(Protocol):
    """Facilitator/chain settlement surface (adapter lives outside)."""

    async def settle(self, payment: VerifiedPayment) -> str:
        """Broadcast settlement and return the transaction hash."""
        ...


def _assert_verified(payment: VerifiedPayment) -> None:
    if not isinstance(payment, VerifiedPayment):
        raise VerificationFailed("settlement requires a verified payment")
    if not payment.tx_hash:
        raise VerificationFailed("verified payment has no transaction hash")
    if not payment.observation.success:
        raise VerificationFailed("cannot settle a failed payment")
    if payment.observation.value_atoms <= 0:
        raise VerificationFailed("cannot settle a zero-value payment")


async def settle_payment(
    payment: VerifiedPayment, client: SettlementClient
) -> SettlementReceipt:
    """Settle a verified payment, mapping transport failures to SettlementError."""
    _assert_verified(payment)
    try:
        tx_hash = await client.settle(payment)
    except SettlementError:
        raise
    except Exception as exc:  # transport/protocol failure from the facilitator
        logger.error("x402_settlement_failed tx=%s error=%s", payment.tx_hash[:12], exc)
        raise SettlementError("settlement failed", reason=str(exc)) from exc
    if not tx_hash:
        raise SettlementError("settlement returned no transaction hash")
    logger.info(
        "x402_settled tx=%s network=%s amount_atoms=%s",
        tx_hash[:12],
        payment.proof.network or "unknown",
        payment.observation.value_atoms,
    )
    return SettlementReceipt(
        tx_hash=tx_hash,
        network=payment.proof.network,
        amount_atoms=payment.observation.value_atoms,
    )
