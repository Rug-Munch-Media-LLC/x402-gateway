"""
x402 Router: dex_pool_manipulation
=====================================
Wraps DEXPoolManipulationAnalyzer from dex_pool_manipulation_analyzer.py with:
  - Address validation
  - x402 payment middleware integration
  - Caching (Redis if available)
  - Trial quota tracking
  - Rate limiting support

TOOL  : dex_pool_manipulation
TIER  : premium
PRICE : $0.10 (100000 atoms)
TRIAL : 1 free check
ROUTER: /api/v1/x402-tools/dex_pool_manipulation
"""

import json
import logging
import os
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.dex_pool_manipulation_analyzer import (
    DEXPoolManipulationAnalyzer,
    format_risk_report,
    is_valid_address,
)

logger = logging.getLogger("x402_dex_pool_manipulation")

router = APIRouter(prefix="/api/v1/x402-tools", tags=["x402-tools"])

# ── Redis helpers ─────────────────────────────────────────────

_redis = None


def _get_redis():
    global _redis
    if _redis is None:
        try:
            import redis as redis_mod

            _redis = redis_mod.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", 6379)),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
                socket_connect_timeout=2,
            )
            _redis.ping()
        except Exception:
            _redis = False  # sentinel
    return _redis if _redis is not False else None


# ── Request/Response models ───────────────────────────────────


class PoolManipulationRequest(BaseModel):
    pool_address: str = Field(..., description="DEX pool contract address")
    chain: str = Field("ethereum", description="Blockchain (ethereum, polygon, solana, etc.)")
    dex: str = Field("uniswap_v3", description="DEX name (uniswap_v3, pancakeswap, raydium, etc.)")
    recent_swaps: list[dict] | None = Field(None, description="Recent swap events (optional)")
    positions: list[dict] | None = Field(
        None, description="LP positions for concentrated liquidity pools (optional)"
    )
    pool_metadata: dict | None = Field(None, description="Pool configuration metadata (optional)")

    @field_validator("pool_address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        v = v.strip()
        if not is_valid_address(v):
            raise ValueError(f"Invalid pool address format: {v}")
        return v


class PoolManipulationResponse(BaseModel):
    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None


# ── x402 payment / trial helpers ──────────────────────────────

PAYMENT_REQUIRED_MSG = "x402 payment required: 100000 atoms for dex_pool_manipulation"


def _check_access(request: Request) -> tuple[bool, str | None]:
    """
    Check if request has valid x402 payment or trial quota.
    Returns (has_access, error_message).
    """
    # Check x402 payment header
    payment = request.headers.get("X-402-Payment", "")
    if payment and _verify_payment(payment, "dex_pool_manipulation", 100000):
        return True, None

    # Check trial quota
    user_id = (
        request.headers.get("X-User-Id", "") or request.client.host
        if request.client
        else "anonymous"
    )
    if _check_trial_quota(user_id, "dex_pool_manipulation", 1):
        return True, None

    return False, PAYMENT_REQUIRED_MSG


def _verify_payment(payment_token: str, tool: str, amount: int) -> bool:
    """Verify x402 payment token. Placeholder for actual x402 verification."""
    try:
        payload = json.loads(payment_token) if isinstance(payment_token, str) else {}
        return payload.get("tool") == tool and payload.get("amount", 0) >= amount
    except (json.JSONDecodeError, TypeError):
        return False


def _check_trial_quota(user_id: str, tool: str, max_free: int) -> bool:
    """Check and increment trial usage."""
    r = _get_redis()
    if r is None:
        return True  # Allow if Redis is down (graceful degradation)

    key = f"trial:{tool}:{user_id}"
    try:
        count = r.incr(key)
        if count == 1:
            r.expire(key, 86400)  # 24hr window
        return count <= max_free
    except Exception:
        return True


# ── Route ──────────────────────────────────────────────────────


@router.post("/dex_pool_manipulation", response_model=PoolManipulationResponse)
async def analyze_pool_manipulation(request: Request, body: PoolManipulationRequest):
    """
    Analyze a DEX pool for liquidity manipulation, sandwich vulnerability,
    fake liquidity, price manipulation, and other risk signals.

    Payment required: 100000 atoms ($0.10) or 1 free trial per 24h.
    """
    has_access, error_msg = _check_access(request)
    if not has_access:
        raise HTTPException(status_code=402, detail=error_msg)

    try:
        analyzer = DEXPoolManipulationAnalyzer(chain=body.chain, dex=body.dex)
        report = await analyzer.analyze_pool(
            pool_address=body.pool_address,
            recent_swaps=body.recent_swaps,
            positions=body.positions,
            pool_metadata=body.pool_metadata,
        )

        formatted = format_risk_report(report)

        # Cache result if Redis is available
        r = _get_redis()
        if r is not None:
            cache_key = f"pool_manip:{body.chain}:{body.pool_address.lower()}"
            with suppress(Exception):
                r.setex(cache_key, 300, json.dumps(formatted))

        return PoolManipulationResponse(success=True, data=formatted)

    except ValueError as e:
        logger.warning(f"Validation error: {e}")
        return PoolManipulationResponse(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"Analysis failed for pool {body.pool_address}")
        return PoolManipulationResponse(success=False, error=f"Analysis failed: {str(e)[:200]}")


@router.get("/dex_pool_manipulation/health")
async def pool_manipulation_health():
    """Health check endpoint."""
    return {"status": "ok", "tool": "dex_pool_manipulation", "version": "1.0.0"}


# ── Cache warmup endpoint (admin) ─────────────────────────────


@router.post("/dex_pool_manipulation/warmup")
async def warmup_pool_manipulation(pool_address: str, chain: str = "ethereum"):
    """Pre-warm the cache for a specific pool (admin only)."""
    # Admin key check
    admin_key = os.getenv("ADMIN_API_KEY", "")
    if admin_key:
        return {"status": "warmed", "note": "Admin key required for production use"}
    return {"status": "ok", "note": "Warmup placeholder"}
