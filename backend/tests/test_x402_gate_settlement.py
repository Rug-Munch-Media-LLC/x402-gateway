"""Settlement invariants: verify-failure never settles, retries are idempotent."""

from __future__ import annotations

import asyncio

import pytest
from app.domain.x402.payments import (
    AmountMismatch,
    MemoryNonceStore,
    PaymentGate,
    RecipientMismatch,
    SettlementError,
    VerificationFailed,
    settle_payment,
)
from app.domain.x402.payments.nonce import ClaimOutcome
from x402_fakes import (
    FakeReader,
    FakeSettler,
    attempt,
    good_observation,
    make_config,
    v2_header,
)

TOOL = ("get_deployer_reputation", 2)


def run(coro):
    return asyncio.run(coro)


def build_gate(reader, settler, store=None) -> PaymentGate:
    return PaymentGate(
        config=make_config(),
        reader=reader,
        nonce_store=store or MemoryNonceStore(),
        settler=settler,
    )


@pytest.mark.parametrize(
    ("reader", "error"),
    [
        (FakeReader(observation=None), VerificationFailed),
        (FakeReader(observation=good_observation()), None),
        (FakeReader(observation=good_observation(confirmations=0)), VerificationFailed),
        (
            FakeReader(observation=good_observation(pay_to="0x" + "00" * 20)),
            RecipientMismatch,
        ),
        (FakeReader(observation=good_observation(value_atoms=1)), AmountMismatch),
        (FakeReader(error=RuntimeError("rpc down")), RuntimeError),
    ],
)
def test_verification_failure_never_settles(reader, error):
    settler = FakeSettler()
    gate = build_gate(reader, settler)
    if error is None:  # control case: happy path settles exactly once
        context = run(gate.authorize(attempt(TOOL, "a", v2_header())))
        assert context["settlement_tx"] == settler.tx_hash
        assert settler.calls == 1
        return
    with pytest.raises(error):
        run(gate.authorize(attempt(TOOL, "a", v2_header())))
    assert settler.calls == 0


def test_retry_of_same_proof_is_idempotent_and_settles_once():
    settler = FakeSettler()
    store = MemoryNonceStore()
    header = v2_header()
    first = run(
        build_gate(FakeReader(good_observation()), settler, store).authorize(
            attempt(TOOL, "a", header)
        )
    )
    second = run(
        build_gate(FakeReader(good_observation()), settler, store).authorize(
            attempt(TOOL, "a", header)
        )
    )
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert second["paid_via_x402"] == first["paid_via_x402"]
    assert settler.calls == 1


def test_settlement_failure_releases_claim_so_client_can_retry():
    store = MemoryNonceStore()
    failing = FakeSettler(error=RuntimeError("facilitator 502"))
    with pytest.raises(SettlementError):
        run(
            build_gate(FakeReader(good_observation()), failing, store).authorize(
                attempt(TOOL, "a", v2_header())
            )
        )

    recovering = FakeSettler()
    context = run(
        build_gate(FakeReader(good_observation()), recovering, store).authorize(
            attempt(TOOL, "a", v2_header())
        )
    )
    assert context["idempotent"] is False
    assert recovering.calls == 1


def test_settle_payment_rejects_unverified_input():
    class ExplodingSettler:
        calls = 0

        async def settle(self, payment):
            self.calls += 1
            return "0xdead"

    client = ExplodingSettler()
    with pytest.raises(VerificationFailed):
        run(settle_payment(None, client))  # type: ignore[arg-type]
    assert client.calls == 0


def test_memory_nonce_store_scope_semantics():
    store = MemoryNonceStore()

    async def scenario():
        first = await store.claim("k", "tool:a", 60)
        same = await store.claim("k", "tool:a", 60)
        other = await store.claim("k", "tool:b", 60)
        await store.release("k")
        again = await store.claim("k", "tool:b", 60)
        return first, same, other, again

    assert run(scenario()) == (
        ClaimOutcome.FIRST_USE,
        ClaimOutcome.SAME_SCOPE,
        ClaimOutcome.REPLAY,
        ClaimOutcome.FIRST_USE,
    )
