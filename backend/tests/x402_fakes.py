"""Deterministic fakes for the x402 payment tests — no network, no Redis."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from app.domain.x402.payments.config import USDC_BASE, PaymentConfig
from app.domain.x402.payments.gate import PaymentAttempt
from app.domain.x402.payments.verifier import ChainObservation

PAY_TO = "0x1E3AC01d0fdb976179790BDD02823196A92705C9"
OTHER_ADDRESS = "0x000000000000000000000000000000000000dEaD"
NETWORK = "eip155:8453"


def attempt(
    tool: tuple[str, int],
    agent_id: str = "a",
    x_payment: str | None = None,
    now_ts: int | None = None,
) -> PaymentAttempt:
    """Build the gate argument for one (tool, price) call."""
    return PaymentAttempt(
        tool=tool[0],
        price_cents=tool[1],
        agent_id=agent_id,
        x_payment=x_payment,
        now_ts=now_ts,
    )


def make_config(**overrides) -> PaymentConfig:
    values = {
        "network": NETWORK,
        "chain": "base",
        "asset": USDC_BASE,
        "pay_to": PAY_TO,
        "required_confirmations": 12,
        "hmac_secret": b"test-secret-not-a-real-key",
    }
    values.update(overrides)
    return PaymentConfig(**values)


def v2_header(**overrides) -> str:
    """Build a base64 x402 v2 header; keyword args override any field."""
    fields = {
        "amount": "20000",
        "asset": USDC_BASE,
        "network": NETWORK,
        "pay_to": PAY_TO,
        "valid_before": 4_102_444_800,
        "nonce": "0x" + "ab" * 32,
    }
    fields.update(overrides)
    payload = {
        "x402Version": 2,
        "accepted": {
            "scheme": "exact",
            "network": fields["network"],
            "asset": fields["asset"],
            "amount": fields["amount"],
            "payTo": fields["pay_to"],
        },
        "payload": {
            "signature": "0xsig",
            "authorization": {
                "from": OTHER_ADDRESS,
                "to": fields["pay_to"],
                "value": fields["amount"],
                "nonce": fields["nonce"],
                "validBefore": fields["valid_before"],
            },
        },
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def legacy_header(tx_hash: str) -> str:
    return base64.b64encode(
        json.dumps({"tx_hash": tx_hash, "signature": "0xsig"}).encode()
    ).decode()


@dataclass
class FakeReader:
    """Returns a canned observation, or raises, or misses."""

    observation: ChainObservation | None = None
    error: Exception | None = None
    calls: int = 0

    async def observe(self, proof):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.observation


@dataclass
class FakeSettler:
    """Records settlements; can fail on demand."""

    tx_hash: str = "0x" + "cd" * 32
    error: Exception | None = None
    calls: int = 0

    async def settle(self, payment) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.tx_hash


def good_observation(
    *, value_atoms: int = 20_000, pay_to: str = PAY_TO, confirmations: int = 24
) -> ChainObservation:
    return ChainObservation(
        success=True,
        value_atoms=value_atoms,
        pay_to=pay_to,
        confirmations=confirmations,
        settled_tx_hash="0x" + "11" * 32,
    )
