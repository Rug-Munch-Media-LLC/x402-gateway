"""The payment gate: one ordered decision, no I/O of its own.

Order is the invariant: challenge -> decode -> terms -> verify -> claim ->
settle. Settlement is last and unreachable when verification fails; a retry of
the same proof for the same tool/agent is idempotent and never re-settles.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.domain.x402.payments.challenge import build_challenge
from app.domain.x402.payments.config import PaymentConfig
from app.domain.x402.payments.errors import PaymentReplay, PaymentRequired
from app.domain.x402.payments.header import decode_payment_header
from app.domain.x402.payments.nonce import ClaimOutcome, NonceStore
from app.domain.x402.payments.requirements import build_requirements, check_requirements
from app.domain.x402.payments.settle import SettlementClient, settle_payment
from app.domain.x402.payments.verifier import ChainReader, verify_payment

logger = logging.getLogger("x402_gate")
NONCE_TTL_SECONDS = 86_400


@dataclass(frozen=True)
class PaymentGate:
    """Authorizes one paid call at a time against injected collaborators."""

    config: PaymentConfig
    reader: ChainReader
    nonce_store: NonceStore
    settler: SettlementClient | None = None

    async def authorize(
        self,
        tool: str,
        price_cents: int,
        agent_id: str,
        x_payment: str | None = None,
        now_ts: int | None = None,
    ) -> dict[str, Any]:
        """Return the payment context for an authorized call.

        Raises a :class:`PaymentError` subclass otherwise; never returns a
        half-authorized context.
        """
        if price_cents <= 0:
            return {
                "agent_id": agent_id,
                "tool": tool,
                "tier": "free",
                "paid_via_x402": None,
            }

        if not x_payment:
            raise PaymentRequired(
                build_challenge(self.config, tool, agent_id, price_cents)
            )

        proof = decode_payment_header(x_payment)
        requirements = build_requirements(self.config, price_cents)
        check_requirements(proof, requirements, now_ts or int(time.time()))

        verified = await verify_payment(proof, requirements, self.reader)

        scope = f"{tool}:{agent_id}"
        outcome = await self.nonce_store.claim(
            verified.tx_hash, scope, NONCE_TTL_SECONDS
        )
        if outcome is ClaimOutcome.REPLAY:
            raise PaymentReplay("payment proof already used", tx_hash=verified.tx_hash)
        context: dict[str, Any] = {
            "agent_id": agent_id,
            "tool": tool,
            "tier": "pro",
            "paid_via_x402": verified.tx_hash,
            "amount_atoms": verified.paid_amount_atoms,
            "idempotent": outcome is ClaimOutcome.SAME_SCOPE,
        }
        if context["idempotent"] or self.settler is None:
            return context

        try:
            receipt = await settle_payment(verified, self.settler)
        except Exception:
            # Settlement failed: release the claim so the client can retry.
            await self.nonce_store.release(verified.tx_hash)
            raise
        context["settlement_tx"] = receipt.tx_hash
        return context
