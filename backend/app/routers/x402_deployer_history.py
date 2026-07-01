"""
x402 Router: deployer_history
==============================
Wraps DeployerHistoryAnalyzer from deployer_history.py with:
  - Address validation
  - x402 payment middleware integration
  - Caching (Redis if available)
  - Trial quota tracking
  - Rate limiting support

TOOL  : deployer_history
TIER  : premium
PRICE : $0.05 (50000 atoms)
TRIAL : 2 free checks
ROUTER: /api/v1/x402-tools/deployer_history
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

from app.deployer_history import DeployerHistoryAnalyzer

logger = logging.getLogger("x402_deployer_history")

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
    logger.debug("Redis not available for deployer_history cache")


async def _get_cache(key: str) -> dict[str, Any] | None:
    """Get cached result if Redis is available."""
    if _redis is None:
        return None
    with suppress(Exception):
        data = await _redis.get(f"x402:cache:deployer_history:{key}")
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
            f"x402:cache:deployer_history:{key}", ttl, json.dumps(result, default=str)
        )


# ── Trial tracking ──────────────────────────────────────────────


def _trial_key(wallet: str) -> str:
    return f"x402:deployer_history_trials:{wallet}"


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


class DeployerHistoryRequest(BaseModel):
    """Request model for deployer history analysis."""

    address: str = Field(..., description="Deployer wallet address to investigate")
    include_token_details: bool = Field(True, description="Include detailed token list in response")
    chain: str | None = Field(
        None, description="Optional chain filter (ethereum, solana, bsc, base, polygon)"
    )

    @field_validator("address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        v = v.strip()
        is_evm = v.startswith("0x") and len(v) == 42
        is_solana = not v.startswith("0x") and 32 <= len(v) <= 44 and v.isascii()
        if not is_evm and not is_solana:
            raise ValueError(
                "Address must be a valid wallet address (0x... for EVM, base58 for Solana)"
            )
        return v.lower()


class DeployerHistoryResponse(BaseModel):
    """Response model for deployer history analysis."""

    success: bool = True
    tool: str = "deployer_history"
    data: dict[str, Any] = Field(default_factory=dict)
    cached: bool = False
    scanned_at: str = ""


# ── Endpoint ────────────────────────────────────────────────────


@router.post("/deployer_history")
async def analyze_deployer_history(
    request: Request,
    body: DeployerHistoryRequest,
) -> DeployerHistoryResponse:
    """
    Investigate the complete deployment history of any token creator address.

    Analyzes all tokens deployed by the given address, detects rug pulls,
    honeypots, and other scam patterns. Returns a comprehensive risk assessment
    with per-token breakdown.
    """
    start = time.time()
    wallet = request.headers.get("x-wallet-address", "anonymous")

    # ── Trial check ──
    max_trial = 2
    trials_used = await _check_trials(wallet)
    if trials_used < max_trial:
        await _increment_trials(wallet)

    # ── Cache check ──
    cache_key = f"{body.address}:{body.include_token_details}:{body.chain or 'all'}"
    cached = await _get_cache(cache_key)
    if cached:
        elapsed = time.time() - start
        return DeployerHistoryResponse(
            data=cached,
            cached=True,
            scanned_at=cached.get("scanned_at", ""),
        )

    # ── Run analysis ──
    try:
        analyzer = DeployerHistoryAnalyzer(body.address)
        result = await analyzer.analyze()

        # Filter by chain if specified
        if body.chain and result.get("tokens"):
            result["tokens"] = [
                t for t in result["tokens"] if t.get("chain", "").lower() == body.chain.lower()
            ]
            result["total_tokens_deployed"] = len(result["tokens"])
            result["chains_used"] = list({t.get("chain", "") for t in result["tokens"]})

        # Remove detailed token list if not requested
        if not body.include_token_details and "tokens" in result:
            token_summary = [
                {
                    "address": t["address"],
                    "chain": t["chain"],
                    "symbol": t["symbol"],
                    "risk_level": "rug"
                    if t.get("is_rug")
                    else (
                        "honeypot"
                        if t.get("is_honeypot")
                        else "active"
                        if t.get("is_active")
                        else "dead"
                    ),
                }
                for t in result["tokens"]
            ]
            result["tokens"] = token_summary

        # ── Cache result ──
        await _set_cache(cache_key, result, ttl=300)

        elapsed = time.time() - start
        logger.info(
            f"deployer_history: {body.address} → "
            f"{result.get('total_tokens_deployed', 0)} tokens, "
            f"risk={result.get('risk_level', 'unknown')}, "
            f"took={elapsed:.1f}s"
        )

        return DeployerHistoryResponse(
            data=result,
            scanned_at=result.get("scanned_at", datetime.now(UTC).isoformat()),
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"deployer_history failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)[:200]}") from e


# ── Health check ────────────────────────────────────────────────


@router.get("/deployer_history/health")
async def deployer_history_health() -> dict[str, object]:
    """Health check for deployer_history tool."""
    return {
        "tool": "deployer_history",
        "status": "ok",
        "tier": "premium",
        "price_usd": 0.05,
        "trial_free": 2,
    }
