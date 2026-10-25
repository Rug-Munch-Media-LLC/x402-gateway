"""Wire contract: the HTTP envelope an agent actually receives.

No network: the app is built in-test and the gate is wired to fakes.
"""

from __future__ import annotations

from app.domain.x402.payments import (
    MemoryNonceStore,
    PaymentAttempt,
    PaymentError,
    PaymentGate,
    to_http_exception,
)
from app.domain.x402.payments.settle import SettlementReceipt
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from x402_fakes import FakeReader, FakeSettler, good_observation, make_config, v2_header

PRICE_CENTS = 2


def build_app(store=None) -> FastAPI:
    app = FastAPI()
    gate = PaymentGate(
        config=make_config(),
        reader=FakeReader(observation=good_observation()),
        nonce_store=store or MemoryNonceStore(),
        settler=FakeSettler(),
    )

    @app.get("/paid")
    async def paid(request: Request):
        try:
            attempt = PaymentAttempt(
                tool="get_deployer_reputation",
                price_cents=PRICE_CENTS,
                agent_id="agent-1",
                x_payment=request.headers.get("X-Payment"),
            )
            return await gate.authorize(attempt)
        except PaymentError as error:
            raise to_http_exception(error) from error

    return app


def test_unpaid_request_returns_402_with_actionable_accepts():
    response = TestClient(build_app()).get("/paid")
    assert response.status_code == 402
    body = response.json()["detail"]
    assert body["error"] == "payment_required"
    accept = body["invoice"]["accepts"][0]
    assert accept["scheme"] == "exact"
    assert accept["network"] == "eip155:8453"
    assert accept["amount"] == "20000"
    assert accept["payTo"]
    assert body["invoice"]["signature"]


def test_paid_request_returns_payment_context():
    response = TestClient(build_app()).get("/paid", headers={"X-Payment": v2_header()})
    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "pro"
    assert body["paid_via_x402"].startswith("0x")
    assert body["settlement_tx"].startswith("0x")


def test_replayed_proof_on_same_tool_returns_idempotent_context():
    client = TestClient(build_app())
    header = {"X-Payment": v2_header()}
    assert client.get("/paid", headers=header).status_code == 200
    retry = client.get("/paid", headers=header)
    assert retry.status_code == 200
    assert retry.json()["idempotent"] is True


def test_malformed_proof_returns_402_not_500():
    response = TestClient(build_app()).get("/paid", headers={"X-Payment": "garbage"})
    assert response.status_code == 402
    assert response.json()["detail"]["error"] == "invalid_payment_header"


def test_settlement_receipt_shape():
    receipt = SettlementReceipt(
        tx_hash="0xabc", network="eip155:8453", amount_atoms=20_000
    )
    assert receipt.amount_atoms == 20_000
