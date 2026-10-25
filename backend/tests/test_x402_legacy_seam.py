"""The legacy require_payment seam must keep the published envelope."""

from __future__ import annotations

import asyncio

import pytest
from app.domain.x402.middleware import (
    PRICING,
    get_tool_price_usd,
    require_payment,
    verify_payment_header,
)
from app.domain.x402.payments import MemoryNonceStore, PaymentGate
from fastapi import HTTPException
from x402_fakes import FakeReader, good_observation, make_config, v2_header


class FakeRequest:
    """Just enough of a Starlette Request for the seam."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}
        self.client = type("Client", (), {"host": "127.0.0.1"})()


def run(coro):
    return asyncio.run(coro)


def build_gate(settler=None, store=None) -> PaymentGate:
    return PaymentGate(
        config=make_config(),
        reader=FakeReader(observation=good_observation()),
        nonce_store=store or MemoryNonceStore(),
        settler=settler,
    )


def test_pricing_table_unchanged_for_known_tools():
    assert get_tool_price_usd("get_token_risk") == 0.0
    assert get_tool_price_usd("get_deployer_reputation") == 0.02
    assert PRICING["generate_report"]["pro_cents"] == 500


def test_unpaid_paid_tool_returns_legacy_402_envelope():
    with pytest.raises(HTTPException) as raised:
        run(
            require_payment(
                "get_deployer_reputation", FakeRequest(), None, build_gate()
            )
        )
    detail = raised.value.detail
    assert raised.value.status_code == 402
    assert detail["error"] == "payment_required"
    assert detail["payment_url"].startswith("https://pay.rugmunch.io/")
    assert detail["instructions"]
    assert detail["invoice"]["accepts"][0]["amount"] == "20000"


def test_free_tool_passes_without_payment():
    context = run(require_payment("get_token_risk", FakeRequest(), None, build_gate()))
    assert context["tier"] == "free"


def test_paid_tool_with_valid_proof_authorizes():
    request = FakeRequest({"X-Payment": v2_header(), "X-Agent-Id": "agent-7"})
    context = run(
        require_payment("get_deployer_reputation", request, None, build_gate())
    )
    assert context["tier"] == "pro"
    assert context["agent_id"] == "key:agent-7"


def test_proof_reused_by_another_agent_returns_410():
    store = MemoryNonceStore()
    header = v2_header()
    run(
        require_payment(
            "get_deployer_reputation",
            FakeRequest({"X-Payment": header, "X-Agent-Id": "agent-a"}),
            None,
            build_gate(store=store),
        )
    )
    with pytest.raises(HTTPException) as raised:
        run(
            require_payment(
                "get_deployer_reputation",
                FakeRequest({"X-Payment": header, "X-Agent-Id": "agent-b"}),
                None,
                build_gate(store=store),
            )
        )
    assert raised.value.status_code == 410
    assert raised.value.detail["error"] == "payment_already_used"


def test_verify_payment_header_is_a_bool_boundary():
    assert verify_payment_header("garbage", 0.02) is None
    tx_hash, signature = verify_payment_header(v2_header(), 0.02)
    assert tx_hash == "0x" + "ab" * 32
    assert signature == "0xsig"
