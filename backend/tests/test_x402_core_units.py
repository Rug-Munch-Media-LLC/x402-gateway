"""Unit coverage for the payment core's edges (no network, no Redis)."""

from __future__ import annotations

import asyncio
import base64

import pytest
from app.domain.x402.middleware import (
    _build_gate,
    _check_rate_limit,
    verify_payment_on_chain,
)
from app.domain.x402.payments import (
    ClaimOutcome,
    InvalidPaymentHeader,
    PaymentConfig,
    PaymentGate,
    PaymentProof,
    RedisNonceStore,
    SettlementError,
    VerificationFailed,
    decode_payment_header,
    load_config,
    settle_payment,
    verify_payment,
)
from app.domain.x402.payments.challenge import build_challenge
from app.domain.x402.payments.challenge_verify import verify_challenge_signature
from app.domain.x402.payments.requirements import build_requirements
from app.domain.x402.payments.verifier import VerifiedPayment
from fastapi import HTTPException
from x402_fakes import FakeReader, FakeSettler, good_observation, make_config, v2_header


def run(coro):
    return asyncio.run(coro)


def test_challenge_signature_roundtrip_and_tamper_detection():
    config = make_config()
    invoice = build_challenge(config, "generate_report", "agent-1", 500)
    assert verify_challenge_signature(config, invoice) is True
    assert invoice["accepts"][0]["amount"] == "5000000"
    tampered = {**invoice, "amount_usd": 0.01}
    assert verify_challenge_signature(config, tampered) is False


def test_config_never_ships_a_hardcoded_signing_key():
    config = load_config({})
    assert len(config.hmac_secret) == 32
    assert b"RMI_PLATFORM_HMAC_KEY" not in config.hmac_secret
    assert config.pay_to == ""
    with pytest.raises(ValueError):
        PaymentConfig(network="", chain="")
    with pytest.raises(ValueError):
        PaymentConfig(max_timeout_seconds=0)


def test_header_edge_cases():
    with pytest.raises(InvalidPaymentHeader):
        decode_payment_header(base64.b64encode(b'{"tx_hash": "0xdead"}').decode())
    with pytest.raises(InvalidPaymentHeader):
        decode_payment_header(
            base64.b64encode(b'{"payload": {"authorization": {}}}').decode()
        )
    with pytest.raises(InvalidPaymentHeader):
        decode_payment_header(base64.b64encode(b"[1, 2]").decode())
    proof = decode_payment_header(v2_header())
    assert proof.replay_key == "0x" + "ab" * 32


def test_verifier_rejects_unconfigured_recipient_and_missing_hash():
    requirements = build_requirements(make_config(pay_to=""), 2)
    with pytest.raises(VerificationFailed):
        run(
            verify_payment(
                PaymentProof(tx_hash="0x" + "11" * 32),
                requirements,
                FakeReader(good_observation()),
            )
        )
    requirements = build_requirements(make_config(), 2)
    unresolved = good_observation()
    unresolved = type(unresolved)(
        success=True,
        value_atoms=unresolved.value_atoms,
        pay_to=unresolved.pay_to,
        confirmations=unresolved.confirmations,
        settled_tx_hash="",
    )
    with pytest.raises(VerificationFailed):
        run(
            verify_payment(
                PaymentProof(nonce="n"), requirements, FakeReader(unresolved)
            )
        )


@pytest.mark.parametrize("tx_hash", ["", None])
def test_settle_rejects_missing_hash(tx_hash):
    payment = VerifiedPayment(
        proof=PaymentProof(tx_hash=tx_hash, nonce="n"),
        observation=good_observation(),
        tx_hash=tx_hash or "",
    )
    with pytest.raises(VerificationFailed):
        run(settle_payment(payment, FakeSettler()))


def test_settle_maps_empty_facilitator_result_to_error():
    payment = VerifiedPayment(
        proof=PaymentProof(tx_hash="0x" + "11" * 32),
        observation=good_observation(),
        tx_hash="0x" + "11" * 32,
    )
    with pytest.raises(SettlementError):
        run(settle_payment(payment, FakeSettler(tx_hash="")))


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        self.data.pop(key, None)


def test_redis_nonce_store_claim_semantics():
    store = RedisNonceStore(FakeRedis())

    async def scenario():
        return (
            await store.claim("k", "tool:a", 60),
            await store.claim("k", "tool:a", 60),
            await store.claim("k", "tool:b", 60),
        )

    assert run(scenario()) == (
        ClaimOutcome.FIRST_USE,
        ClaimOutcome.SAME_SCOPE,
        ClaimOutcome.REPLAY,
    )


class CountingRedis:
    def __init__(self, count: int) -> None:
        self.count = count
        self.expires = 0

    async def incr(self, key):
        return self.count

    async def expire(self, key, ttl):
        self.expires += 1
        return True


def test_rate_limit_raises_429_and_sets_ttl_on_first_hit():
    run(_check_rate_limit(CountingRedis(1), "agent-1"))
    with pytest.raises(HTTPException) as raised:
        run(_check_rate_limit(CountingRedis(6), "agent-1"))
    assert raised.value.status_code == 429


def test_gate_falls_back_to_memory_store_without_redis(caplog):
    gate = _build_gate(None)
    assert isinstance(gate, PaymentGate)
    assert gate.nonce_store is not None
    assert "in-process nonce store" in caplog.text


def test_verify_payment_on_chain_bool_boundary_never_raises():
    assert run(verify_payment_on_chain("", 0.02)) is False
    assert run(verify_payment_on_chain("0x" + "22" * 32, 0.02)) is False
