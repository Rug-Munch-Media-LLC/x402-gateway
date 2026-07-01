"""
x402 Router: cross_chain_whale
================================
Wraps CrossChainWhaleTracker with:
  - Address validation
  - x402 payment middleware integration
  - Caching (Redis if available)
  - Trial quota tracking
  - Rate limiting support

TOOL  : cross_chain_whale
TIER  : intelligence
PRICE : $0.08 (80000 atoms)
TRIAL : 2 free checks
ROUTER: /api/v1/x402-tools/cross_chain_whale
"""

import json
import logging
import os
from contextlib import suppress

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.cross_chain_whale import (
    CrossChainWhaleTracker,
    format_whale_report,
    get_whale_tracker,
    is_valid_address,
)

logger = logging.getLogger("x402_cross_chain_whale")

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
                db=int(os.getenv("REDIS_DB", 0)),
                password=os.getenv("REDIS_PASSWORD", None),
                decode_responses=True,
            )
            _redis.ping()
        except Exception:
            _redis = False  # Sentinel for "not available"
    return _redis if _redis is not False else None


_CACHE_TTL = 300  # 5 minutes


# ── Request Models ─────────────────────────────────────────────


class CrossChainWhaleRequest(BaseModel):
    """Request body for cross-chain whale tracking."""

    token_address: str = Field(..., description="Token contract/mint address to track")
    chains: list[str] | None = Field(
        default=None,
        description="Chains to check (default: solana, ethereum, base, bsc)",
    )

    @field_validator("token_address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        v = v.strip()
        if not is_valid_address(v):
            raise ValueError(
                f"Invalid address: {v}. Must be a valid Solana (base58) "
                "or EVM (0x-prefixed hex) address."
            )
        return v


class CrossChainWhaleWalletRequest(BaseModel):
    """Request body for wallet-level cross-chain tracking."""

    wallet_address: str = Field(..., description="Wallet address to track across chains")
    chains: list[str] | None = Field(
        default=None,
        description="Chains to check (default: all supported chains)",
    )

    @field_validator("wallet_address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        v = v.strip()
        if not is_valid_address(v):
            raise ValueError(f"Invalid wallet address: {v}")
        return v


# ── Endpoints ──────────────────────────────────────────────────


@router.post("/cross_chain_whale")
async def track_cross_chain_whale(
    request: Request,
    body: CrossChainWhaleRequest,
):
    """Track whale holdings for a token across multiple chains.

    Returns a comprehensive report of holder concentrations,
    cross-chain whale positions, and risk scoring.
    """
    redis_client = _get_redis()
    cache_key = f"x402:whale:{body.token_address}:{','.join(body.chains or [])}"

    # Check cache
    if redis_client:
        with suppress(Exception):
            cached = redis_client.get(cache_key)
            if cached:
                return {"success": True, "data": json.loads(cached), "cached": True}

    try:
        tracker: CrossChainWhaleTracker = get_whale_tracker()

        # Determine chains to scan
        chains = body.chains or ["solana", "ethereum", "base", "bsc"]

        report = await tracker.track_token(
            token_address=body.token_address,
            chains=chains,
        )

        # Build response data
        response_data = {
            "token_address": report.token_address,
            "token_symbol": report.token_symbol,
            "token_name": report.token_name,
            "chains_found": report.chains_found,
            "chains_no_data": report.chains_no_data,
            "total_holders_tracked": report.total_holders,
            "total_value_tracked_usd": round(report.total_value_tracked, 2),
            "concentration_score": report.concentration_score,
            "cross_chain_whale_count": len(report.cross_chain_whales),
            "top_holders_by_chain": report.top_holders_by_chain,
            "cross_chain_whales": [
                {
                    "address": cw.primary_address,
                    "address_short": f"{cw.primary_address[:6]}...{cw.primary_address[-4:]}",
                    "chain_count": cw.chain_count,
                    "total_value_usd": round(cw.total_value_usd, 2),
                    "chains": list({p.chain for p in cw.positions}),
                    "risk_score": cw.risk_score,
                    "risk_factors": cw.risk_factors[:5],
                }
                for cw in report.cross_chain_whales[:20]
            ],
            "top_whale": (
                {
                    "address": report.cross_chain_whales[0].primary_address,
                    "chain_count": report.cross_chain_whales[0].chain_count,
                    "total_value_usd": round(report.cross_chain_whales[0].total_value_usd, 2),
                }
                if report.cross_chain_whales
                else None
            ),
            "human_readable": format_whale_report(report),
            "errors": report.errors[:5],
            "scan_timestamp": report.scan_timestamp,
        }

        # Cache it
        if redis_client and not report.errors:
            with suppress(Exception):
                redis_client.setex(cache_key, _CACHE_TTL, json.dumps(response_data))

        return {"success": True, "data": response_data}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception(f"cross_chain_whale failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Cross-chain whale tracking failed: {e!s}",
        ) from e


@router.post("/cross_chain_whale_wallet")
async def track_cross_chain_wallet(
    request: Request,
    body: CrossChainWhaleWalletRequest,
):
    """Track a specific wallet's token positions across multiple chains.

    Returns a comprehensive view of what tokens a whale wallet holds
    across all supported chains.
    """
    redis_client = _get_redis()
    cache_key = f"x402:whale_wallet:{body.wallet_address}:{','.join(body.chains or [])}"

    if redis_client:
        with suppress(Exception):
            cached = redis_client.get(cache_key)
            if cached:
                return {"success": True, "data": json.loads(cached), "cached": True}

    try:
        tracker: CrossChainWhaleTracker = get_whale_tracker()

        chains = body.chains or [
            "solana",
            "ethereum",
            "base",
            "bsc",
            "polygon",
            "arbitrum",
            "optimism",
        ]

        result = await tracker.track_wallet(
            wallet_address=body.wallet_address,
            chains=chains,
        )

        if redis_client:
            with suppress(Exception):
                redis_client.setex(cache_key, _CACHE_TTL, json.dumps(result))

        return {"success": True, "data": result}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception(f"cross_chain_whale_wallet failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Wallet cross-chain tracking failed: {e!s}",
        ) from e
