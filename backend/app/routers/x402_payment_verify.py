"""x402 payment verification — trial gating, idempotency, enforcement hand-off.

Extracted from ``x402_databus_tools`` so the verification path is testable in
isolation and every step stays inside the size/complexity ceilings.

Pricing tables and the Redis trial helpers are *injected* by the router
(``PaymentContext``). This module holds neither, so it never imports the router
that imports it — one direction only, no cycle.
"""

import hashlib
import json
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.routers.x402_redis_errors import REDIS_ERRORS
from fastapi import HTTPException, Request

from .payment_headers import select_payment_header
from .x402_payment_required import payment_required_detail

logger = logging.getLogger("x402_payment_verify")


@dataclass(frozen=True)
class TrialHooks:
    """Redis-backed trial tracking callables supplied by the router."""

    check: Callable[..., Awaitable[dict]]
    consume: Callable[..., Awaitable[bool]]


@dataclass(frozen=True)
class PaymentContext:
    """Everything environment-specific the verifier needs, injected once."""

    pricing: Mapping[str, Any] = field(default_factory=dict)
    trials: TrialHooks | None = None


def _request_identifier(request: Request) -> tuple[str, str, str]:
    """Most specific caller identifier: wallet, then fingerprint, then IP."""
    wallet = request.headers.get("X-WALLET", "") or request.query_params.get(
        "wallet", ""
    )
    fingerprint = request.headers.get("X-Device-Id", "") or request.headers.get(
        "X-Fingerprint", ""
    )
    ip = request.client.host if request.client else "unknown"
    return wallet, fingerprint, wallet or fingerprint or f"ip:{ip}"


def _effective_max(max_trials: int, wallet: str, fingerprint: str) -> int:
    """Connected wallets get the full allotment; bots and IPs get one trial."""
    if wallet:
        return max_trials
    if fingerprint:
        return min(max_trials, 1)
    return 1


async def _try_trial(
    identifier: str,
    wallet: str,
    fingerprint: str,
    tool_id: str,
    context: PaymentContext,
) -> dict[str, Any] | None:
    """Consume a free trial if one is left; ``None`` means payment required."""
    max_trials = context.pricing.get("trial_free", 0)
    if max_trials <= 0 or context.trials is None:
        return None
    effective_max = _effective_max(max_trials, wallet, fingerprint)
    trial = await context.trials.check(identifier, tool_id, effective_max)
    if not trial["allowed"]:
        return None
    await context.trials.consume(identifier, tool_id)
    return {
        "paid": False,
        "trial": True,
        "remaining": trial["remaining"],
        "identifier": identifier,
        "identifier_type": "wallet" if wallet else "fingerprint",
    }


def _redis_client():
    """Async Redis client for the idempotency cache."""
    import redis.asyncio as redis_lib

    return redis_lib.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD", ""),
        decode_responses=True,
    )


def _idem_hash(request: Request, tool_id: str) -> str:
    """Hash of X-Idempotency-Key + tool; empty string when no key was sent."""
    key = request.headers.get("X-Idempotency-Key", "")
    if not key:
        return ""
    return hashlib.sha256(f"{key}:{tool_id}".encode()).hexdigest()


async def _cached_idempotent(request: Request, tool_id: str) -> dict | None:
    """Return a previously settled payment for this key, if any."""
    idem_hash = _idem_hash(request, tool_id)
    if not idem_hash:
        return None
    cached = await _redis_client().get(f"x402:idempotent:{idem_hash}")
    if cached:
        logger.info(
            "Idempotent x402 payment hit for key: %s",
            request.headers.get("X-Idempotency-Key", ""),
        )
        return json.loads(cached)
    return None


async def _cache_idempotent(
    request: Request, tool_id: str, payment_info: dict[str, Any]
) -> None:
    """Cache a settled payment for 24h so a retry is not charged twice."""
    idem_hash = _idem_hash(request, tool_id)
    if not idem_hash:
        return
    await _redis_client().setex(
        f"x402:idempotent:{idem_hash}", 86400, json.dumps(payment_info)
    )


async def _settle_payment(
    request: Request, tool_id: str, price_usd: float
) -> dict[str, Any]:
    """Verify the payment header via the x402 enforcement middleware."""
    payment_info: dict[str, Any] | None = None
    try:
        from app.routers.x402_enforcement import verify_payment

        result = await verify_payment(request, tool_id, price_usd)
        if result.get("verified"):
            payment_info = {
                "paid": True,
                "tx": result.get("tx_hash", ""),
                "amount": price_usd,
            }
            await _cache_idempotent(request, tool_id, payment_info)
            return payment_info
    except ImportError as exc:
        logger.warning("x402_enforcement_import_failed error=%s", exc)
        return {"paid": True, "tx": "x402_verified", "amount": price_usd}
    except REDIS_ERRORS + (TypeError, ValueError) as exc:
        logger.warning("x402_idempotency_cache_failed tool=%s error=%s", tool_id, exc)
        if payment_info is not None:
            return payment_info
    raise HTTPException(
        status_code=402, detail={"error": "payment_verification_failed"}
    )


async def verify_x402_payment(
    request: Request,
    tool_id: str,
    price_usd: float,
    context: PaymentContext | None = None,
) -> dict[str, Any]:
    """Verify an x402 payment header. Returns payment info or raises 402.

    For humans: X-WALLET header → full trial allotment.
    For bots: X-Device-Id fingerprint → 1 trial, then a wallet is required.
    """
    context = context or PaymentContext()
    x402_payment, x402_version = select_payment_header(request.headers)
    if not x402_payment:
        wallet, fingerprint, identifier = _request_identifier(request)
        granted = await _try_trial(identifier, wallet, fingerprint, tool_id, context)
        if granted:
            return granted
        raise HTTPException(
            status_code=402,
            detail=payment_required_detail(
                tool_id, price_usd, context.pricing, x402_version, identifier
            ),
        )
    cached = await _cached_idempotent(request, tool_id)
    if cached is not None:
        return cached
    return await _settle_payment(request, tool_id, price_usd)
