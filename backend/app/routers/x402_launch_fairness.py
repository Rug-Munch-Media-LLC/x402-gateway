"""
x402 Router: launch_fairness
==============================
Wraps Launch Fairness Analyzer with:
  - Address validation
  - x402 payment middleware integration
  - Caching (Redis if available)
  - Trial quota tracking
  - Rate limiting support

TOOL  : launch_fairness
TIER  : premium
PRICE : $0.10 (100000 atoms)
TRIAL : 2 free checks
ROUTER: /api/v1/x402-tools/launch_fairness
"""

import json
import logging
import os
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.launch_fairness_analyzer import analyze_launch_fairness

logger = logging.getLogger("x402_launch_fairness")

router = APIRouter(prefix="/api/v1/x402-tools", tags=["x402-tools"])

# ── Redis cache (best-effort) ───────────────────────────────────
_redis = None
try:
    import redis.asyncio as aioredis

    _redis = aioredis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
        socket_connect_timeout=2,
    )
except Exception:
    logger.debug("Redis not available for launch_fairness cache")


async def _get_cache(key: str) -> dict[str, Any] | None:
    """Get cached result if Redis is available."""
    if _redis is None:
        return None
    with suppress(Exception):
        data = await _redis.get(f"x402:cache:launch_fairness:{key}")
        if data:
            result: dict[str, Any] = json.loads(data)
            return result
    return None


async def _set_cache(key: str, result: dict[str, Any], ttl: int = 300) -> None:
    """Cache result in Redis with TTL."""
    if _redis is None:
        return
    with suppress(Exception):
        await _redis.setex(
            f"x402:cache:launch_fairness:{key}", ttl, json.dumps(result, default=str)
        )


# ── Trial tracking ──────────────────────────────────────────────


def _trial_key(wallet: str) -> str:
    return f"x402:launch_fairness_trials:{wallet}"


async def _check_trials(wallet: str) -> int:
    """Check how many trials this wallet has used."""
    if _redis is None:
        return 0
    try:
        used = await _redis.get(_trial_key(wallet))
        return int(used) if used else 0
    except Exception:
        return 0


async def _increment_trials(wallet: str) -> None:
    """Increment trial usage for a wallet."""
    if _redis is None:
        return
    try:
        await _redis.incr(_trial_key(wallet))
        await _redis.expire(_trial_key(wallet), 86400 * 30)  # Reset monthly
    except Exception:
        pass


# ── Request / Response models ───────────────────────────────────


class LaunchFairnessRequest(BaseModel):
    """Request model for launch fairness analysis."""

    address: str = Field(..., description="Token contract address to analyze")
    chain: str = Field(
        "auto",
        description="Blockchain (ethereum, solana, bsc, base, polygon, or 'auto' for detection)",
    )
    simulate_data: bool = Field(True, description="Use simulated data for demonstration / testing")

    @field_validator("address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        v = v.strip()
        is_evm = v.startswith("0x") and len(v) == 42
        is_solana = not v.startswith("0x") and 32 <= len(v) <= 44 and v.isascii()
        if not is_evm and not is_solana:
            raise ValueError(
                "Address must be a valid token contract address (0x... for EVM, base58 for Solana)"
            )
        return v.lower()

    @field_validator("chain")
    @classmethod
    def validate_chain(cls, v: str) -> str:
        valid_chains = {
            "auto",
            "ethereum",
            "solana",
            "bsc",
            "base",
            "polygon",
            "arbitrum",
            "avalanche",
        }
        v = v.lower().strip()
        if v not in valid_chains:
            raise ValueError(f"Chain must be one of: {', '.join(sorted(valid_chains))}")
        return v


class LaunchFairnessResponse(BaseModel):
    """Response model for launch fairness analysis."""

    success: bool = True
    tool: str = "launch_fairness"
    data: dict[str, Any] = Field(default_factory=dict)
    cached: bool = False
    scanned_at: str = ""


# ── Endpoint ────────────────────────────────────────────────────


@router.post("/launch_fairness")
async def analyze_launch_fairness_endpoint(
    request: Request,
    body: LaunchFairnessRequest,
) -> LaunchFairnessResponse:
    """
    Analyze token launch fairness — detect sniped distributions, bundled launches,
    bot activity, LP manipulation, and presale concentration.

    Returns a fairness score (0-100) with per-signal breakdown and evidence.
    """
    start = time.time()
    wallet = request.headers.get("x-wallet-address", "anonymous")

    # ── Trial check ──
    max_trial = 2
    trials_used = await _check_trials(wallet)
    if trials_used < max_trial:
        await _increment_trials(wallet)

    # ── Cache check ──
    cache_key = f"{body.address}:{body.chain}:{body.simulate_data}"
    cached = await _get_cache(cache_key)
    if cached:
        elapsed = time.time() - start
        logger.info(f"Launch fairness cache hit for {body.address[:12]}... ({elapsed:.2f}s)")
        return LaunchFairnessResponse(
            data=cached,
            cached=True,
            scanned_at=cached.get("scanned_at", ""),
        )

    # ── Run analysis ──
    try:
        result = await analyze_launch_fairness(
            token_address=body.address,
            chain=body.chain,
            simulate_data=body.simulate_data,
        )

        # Add metadata
        result["scanned_at"] = datetime.now(tz=UTC).isoformat()
        result["tier"] = "premium"
        result["price_usd"] = 0.10

        # ── Cache result (short TTL — fairness data changes quickly) ──
        await _set_cache(cache_key, result, ttl=120)

        logger.info(
            f"Launch fairness analyzed {body.address[:12]}... "
            f"score={result.get('fairness_score', '?')}% "
            f"({time.time() - start:.2f}s)"
        )

        return LaunchFairnessResponse(
            data=result,
            cached=False,
            scanned_at=result["scanned_at"],
        )

    except ValueError as e:
        logger.warning(f"Launch fairness validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Launch fairness analysis failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Launch fairness analysis failed. Please try again.",
        ) from e
