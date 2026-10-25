"""x402 payment middleware — pricing plus the HTTP seam onto the payment core.

The decision logic lives in ``app.domain.x402.payments`` (one job per module).
This module keeps only what callers already import: the price list, per-agent
rate limiting, the legacy helper signatures, and the HTTP exception mapping.

Flow: agent calls a paid tool without X-Payment -> 402 with a signed invoice
and accepts list -> wallet pays -> agent retries with X-Payment -> the gate
verifies, claims and settles.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from app.domain.x402.payments import (
    MemoryNonceStore,
    PaymentError,
    PaymentGate,
    RedisNonceStore,
    build_requirements,
    decode_payment_header,
    load_config,
    to_http_exception,
    verify_payment,
)
from app.domain.x402.payments.challenge import build_challenge
from app.domain.x402.payments.gate import PaymentAttempt
from app.domain.x402.payments.web3_reader import Web3ChainReader
from fastapi import HTTPException, Request

log = logging.getLogger(__name__)

X402_REQUIRED_CONFIRMATIONS = int(os.environ.get("X402_REQUIRED_CONFIRMATIONS", "12"))
FREE_DAILY_LIMIT = 5

PRICING: dict[str, dict[str, Any]] = {
    "get_token_risk": {
        "free_tier": True,
        "pro_cents": 1,
        "ent_cents": 1,
        "description": "Real-time token risk score",
    },
    "get_wallet_analysis": {
        "free_tier": True,
        "pro_cents": 1,
        "ent_cents": 1,
        "description": "Wallet activity + reputation",
    },
    "get_deployer_reputation": {
        "free_tier": False,
        "pro_cents": 2,
        "ent_cents": 1,
        "description": "Deployer reputation score",
    },
    "get_news_sentiment": {
        "free_tier": True,
        "pro_cents": 1,
        "ent_cents": 1,
        "description": "Latest news + sentiment",
    },
    "generate_report": {
        "free_tier": False,
        "pro_cents": 500,
        "ent_cents": 400,
        "description": "Full AI research report",
    },
    "query_catalog": {
        "free_tier": False,
        "pro_cents": 5,
        "ent_cents": 3,
        "description": "Natural language catalog query",
    },
    "find_similar_tokens": {
        "free_tier": False,
        "pro_cents": 3,
        "ent_cents": 2,
        "description": "Vector-similar tokens",
    },
    "resolve_entity": {
        "free_tier": False,
        "pro_cents": 10,
        "ent_cents": 5,
        "description": "Cross-chain entity resolution",
    },
    "search_rag": {
        "free_tier": True,
        "pro_cents": 1,
        "ent_cents": 1,
        "description": "RAG semantic search",
    },
    "bulk_ingest": {
        "free_tier": False,
        "pro_cents": 10,
        "ent_cents": 5,
        "description": "Bulk RAG ingestion",
    },
}


def get_tool_price_usd(tool: str) -> float:
    """Pro-tier price in USD; 0.0 for free tools."""
    entry = PRICING.get(tool, {})
    if entry.get("free_tier", False):
        return 0.0
    return entry.get("pro_cents", 0) / 100.0


def get_tool_metadata(tool: str) -> dict[str, Any]:
    """Public metadata for /api/v1/x402/catalog."""
    entry = PRICING.get(tool, {})
    return {
        "name": tool,
        "free_tier": entry.get("free_tier", False),
        "free_daily_limit": FREE_DAILY_LIMIT if entry.get("free_tier") else 0,
        "price_pro_usd": entry.get("pro_cents", 0) / 100.0,
        "price_ent_usd": entry.get("ent_cents", 0) / 100.0,
        "description": entry.get("description", ""),
    }


def _price_cents(tool: str) -> int:
    return 0 if get_tool_price_usd(tool) == 0 else int(PRICING[tool]["pro_cents"])


def _agent_id_from_request(request: Request | None) -> str:
    """Agent identity from headers, falling back to the client address."""
    if request is None:
        return "anon:unknown"
    agent_id = request.headers.get("X-Agent-Id") or request.headers.get("X-Api-Key")
    if agent_id:
        return f"key:{agent_id}"
    return f"anon:{request.client.host}" if request.client else "anon:unknown"


async def _check_rate_limit(redis, agent_id: str) -> None:
    """Daily free-tier counter; raises 429 when the limit is passed."""
    if not redis:
        return
    day = datetime.now(UTC).strftime("%Y%m%d")
    key = f"x402:rl:{agent_id}:{day}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 86400)
    if count > FREE_DAILY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "agent_id": agent_id,
                "daily_count": count,
                "limit": FREE_DAILY_LIMIT,
                "next_action": "Send X-Payment header with a valid proof to upgrade tier",
            },
        )


def create_invoice(tool: str, agent_id: str) -> dict[str, Any]:
    """Signed payment challenge (legacy helper signature)."""
    return build_challenge(load_config(), tool, agent_id, _price_cents(tool) or 1)


def verify_payment_header(
    x_payment: str, expected_amount_usd: float
) -> tuple[str, str] | None:
    """Legacy tuple helper; returns None instead of raising for old callers."""
    try:
        proof = decode_payment_header(x_payment)
    except PaymentError as error:
        log.warning("x402_bad_header error=%s", error.code)
        return None
    return (proof.tx_hash or proof.nonce, proof.signature)


async def verify_payment_on_chain(tx_hash: str, expected_amount_usd: float) -> bool:
    """Legacy boolean helper used by /api/v1/x402/verify.

    Kept as a bool boundary for that endpoint; the reason is logged, never
    swallowed, and the core path raises typed errors instead.
    """
    if not tx_hash:
        return False
    config = load_config()
    proof_text = None
    try:
        import base64
        import json

        proof_text = base64.b64encode(
            json.dumps({"tx_hash": tx_hash, "signature": ""}).encode()
        ).decode()
        proof = decode_payment_header(proof_text)
        requirements = build_requirements(
            config, max(1, round(expected_amount_usd * 100))
        )
        await verify_payment(proof, requirements, Web3ChainReader(config))
        return True
    except PaymentError as error:
        log.warning("x402_verify_rejected reason=%s tx=%s", error.code, tx_hash[:12])
        return False


def _build_gate(catalog: Any) -> PaymentGate:
    """Production gate: web3 reader + Redis claims (memory only as a fallback)."""
    config = load_config()
    redis = getattr(getattr(catalog, "_redis", None), "set", None) and catalog._redis
    if redis is None:
        log.warning("x402_no_redis_replay_store using in-process nonce store")
        store = MemoryNonceStore()
    else:
        store = RedisNonceStore(redis)
    return PaymentGate(
        config=config,
        reader=Web3ChainReader(config),
        nonce_store=store,
    )


async def require_payment(
    tool: str,
    request: Request | None = None,
    catalog: Any = None,
    gate: PaymentGate | None = None,
) -> dict[str, Any]:
    """Authorize a paid call. Raises 402/410/429 as typed HTTP errors."""
    agent_id = _agent_id_from_request(request)
    if catalog is not None and getattr(catalog, "_redis", None):
        await _check_rate_limit(catalog._redis, agent_id)

    gate = gate or _build_gate(catalog)
    x_payment = request.headers.get("X-Payment") if request is not None else None
    try:
        attempt = PaymentAttempt(
            tool=tool,
            price_cents=_price_cents(tool),
            agent_id=agent_id,
            x_payment=x_payment,
        )
        return await gate.authorize(attempt)
    except PaymentError as error:
        raise to_http_exception(error) from error
