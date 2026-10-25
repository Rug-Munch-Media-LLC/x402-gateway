"""Payment invariants: unpaid, malformed, expired, mismatched, replay.

Synchronous wrappers keep the suite free of asyncio plugins.
"""

from __future__ import annotations

import asyncio

import pytest
from app.domain.x402.payments import (
    AmountMismatch,
    AssetMismatch,
    ChainMismatch,
    InvalidPaymentHeader,
    MemoryNonceStore,
    PaymentExpired,
    PaymentGate,
    PaymentReplay,
    PaymentRequired,
    RecipientMismatch,
    to_http_exception,
)
from x402_fakes import (
    OTHER_ADDRESS,
    PAY_TO,
    FakeReader,
    attempt,
    good_observation,
    legacy_header,
    make_config,
    v2_header,
)

FREE_TOOL = ("get_token_risk", 0)
PAID_TOOL = ("get_deployer_reputation", 2)
SOLANA = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"


def run(coro):
    return asyncio.run(coro)


def build_gate(reader=None, settler=None, nonce_store=None) -> PaymentGate:
    return PaymentGate(
        config=make_config(),
        reader=reader or FakeReader(observation=good_observation()),
        nonce_store=nonce_store or MemoryNonceStore(),
        settler=settler,
    )


def test_unpaid_request_raises_402_with_valid_accepts():
    with pytest.raises(PaymentRequired) as raised:
        run(build_gate().authorize(attempt(PAID_TOOL, "agent-1")))

    error = raised.value
    assert error.status_code == 402
    accept = error.challenge["accepts"][0]
    assert accept["scheme"] == "exact"
    assert accept["network"] == "eip155:8453"
    assert accept["amount"] == "20000"  # 2 cents -> USDC 6dp atoms
    assert accept["payTo"] == PAY_TO
    http = to_http_exception(error)
    assert http.status_code == 402
    assert http.detail["invoice"]["accepts"][0]["payTo"] == PAY_TO


def test_free_tool_needs_no_payment_and_reader_untouched():
    reader = FakeReader(observation=good_observation())
    context = run(build_gate(reader=reader).authorize(attempt(FREE_TOOL, "a")))
    assert context["tier"] == "free"
    assert reader.calls == 0


@pytest.mark.parametrize("header", ["not-base64!!", "eyJmb28iOiJiYXIifQ=="])
def test_malformed_proof_is_rejected(header):
    with pytest.raises(InvalidPaymentHeader) as raised:
        run(build_gate().authorize(attempt(PAID_TOOL, "a", header)))
    assert to_http_exception(raised.value).status_code == 402


def test_expired_authorization_is_rejected_before_chain_io():
    reader = FakeReader(observation=good_observation())
    with pytest.raises(PaymentExpired):
        run(
            build_gate(reader=reader).authorize(
                attempt(PAID_TOOL, "a", v2_header(valid_before=1_000), now_ts=2_000)
            )
        )
    assert reader.calls == 0


@pytest.mark.parametrize(
    ("header_kwargs", "error"),
    [
        ({"amount": "1"}, AmountMismatch),
        ({"asset": OTHER_ADDRESS}, AssetMismatch),
        ({"network": SOLANA}, ChainMismatch),
        ({"pay_to": OTHER_ADDRESS}, RecipientMismatch),
    ],
)
def test_declared_terms_must_match(header_kwargs, error):
    with pytest.raises(error):
        run(build_gate().authorize(attempt(PAID_TOOL, "a", v2_header(**header_kwargs))))


def test_legacy_tx_hash_header_still_verifies():
    tx_hash = "0x" + "11" * 32
    gate = build_gate(
        reader=FakeReader(observation=good_observation(value_atoms=20_000))
    )
    context = run(gate.authorize(attempt(PAID_TOOL, "a", legacy_header(tx_hash))))
    assert context["paid_via_x402"] == tx_hash


def test_replay_on_a_different_tool_is_rejected():
    store = MemoryNonceStore()
    run(
        build_gate(nonce_store=store).authorize(
            attempt(("get_deployer_reputation", 2), "a", v2_header())
        )
    )
    with pytest.raises(PaymentReplay) as raised:
        rich_reader = FakeReader(observation=good_observation(value_atoms=30_000))
        run(
            build_gate(reader=rich_reader, nonce_store=store).authorize(
                attempt(("find_similar_tokens", 3), "a", v2_header(amount="30000"))
            )
        )
    assert to_http_exception(raised.value).status_code == 410
