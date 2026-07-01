"""
x402 Router: insider_network
=============================
Wraps InsiderNetworkAnalyzer with:
  - Address validation
  - x402 payment middleware integration
  - Caching (Redis if available)
  - Trial quota tracking
  - Rate limiting support

TOOL  : insider_network
TIER  : premium / intelligence
PRICE : $0.10 (100000 atoms)
TRIAL : 1 free check
ROUTER: /api/v1/x402-tools/insider_network
"""

import json
import logging
import os
from contextlib import suppress

import redis as _redis_mod
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.insider_network import (
    format_insider_network_report,
    get_insider_network_analyzer,
    is_valid_address,
)

logger = logging.getLogger("x402_insider_network")

router = APIRouter(prefix="/api/v1/x402-tools", tags=["x402-tools"])

# ── Redis helpers ─────────────────────────────────────────────

_redis: "_redis_mod.Redis | None" = None


def _get_redis():
    global _redis
    if _redis is None:
        try:
            r = _redis_mod.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", 6379)),
                db=int(os.getenv("REDIS_DB", 0)),
                password=os.getenv("REDIS_PASSWORD", None),
                decode_responses=True,
            )
            r.ping()
            _redis = r
        except Exception:
            _redis = None  # Not available
    return _redis


_CACHE_TTL = 600  # 10 minutes — insider networks don't change rapidly


# ── Request Models ─────────────────────────────────────────────


class InsiderNetworkRequest(BaseModel):
    """Request body for insider network analysis."""

    wallet_address: str = Field(
        ...,
        description="Wallet address to investigate for insider connections",
    )
    chains: list[str] | None = Field(
        default=None,
        description="Chains to search (default: solana, ethereum, bsc, base)",
    )
    deep_scan: bool = Field(
        default=False,
        description="If True, perform deeper (slower) analysis with more API calls",
    )

    @field_validator("wallet_address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        v = v.strip()
        if not is_valid_address(v):
            raise ValueError(f"Invalid address: {v}. Must be a valid Solana (base58) or EVM (0x-prefixed hex) address.")
        return v

    @field_validator("chains")
    @classmethod
    def validate_chains(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            valid = {"solana", "ethereum", "bsc", "polygon", "arbitrum", "base"}
            for c in v:
                if c not in valid:
                    raise ValueError(f"Unsupported chain: {c}. Supported: {', '.join(sorted(valid))}")
        return v


# ── Endpoints ──────────────────────────────────────────────────


@router.post("/insider_network")
async def analyze_insider_network(request: Request, body: InsiderNetworkRequest) -> dict:
    """
    Analyze a wallet for insider connections across token projects.
    Maps shared funding sources, coordinated trading, and team relationships.
    """
    wallet = body.wallet_address.strip()

    # Check cache
    cache_key = f"insider_network:{wallet}:{body.deep_scan}"
    r = _get_redis()
    if r:
        with suppress(Exception):
            cached = r.get(cache_key)
            if cached:
                return json.loads(cached)

    # Check trial quota (via x402 middleware headers if available)
    trial_used = False
    x402_headers = getattr(request.state, "x402", None) or {}
    quota = x402_headers.get("remaining_quota", {})
    if isinstance(quota, dict) and quota.get("insider_network", 0) > 0:
        trial_used = True
        logger.info(
            "Trial quota used for insider_network on wallet %s",
            wallet[:10],
        )

    try:
        analyzer = get_insider_network_analyzer()
        report = await analyzer.analyze(
            wallet_address=wallet,
            chains=body.chains,
            deep_scan=body.deep_scan,
        )

        response = {
            "success": True,
            "tool": "insider_network",
            "wallet_address": wallet,
            "total_connected_wallets": report.total_connected_wallets,
            "total_clusters": report.total_clusters,
            "highest_risk_score": round(report.highest_risk_score, 1),
            "clusters": [
                {
                    "cluster_id": c.cluster_id,
                    "member_count": c.member_count,
                    "risk_score": round(c.risk_score, 1),
                    "projects_involved": c.projects_involved[:5],
                    "risk_factors": c.risk_factors[:3],
                    "top_relationships": c.top_relationships[:5],
                }
                for c in report.clusters
            ],
            "summary": report.summary,
            "report_text": format_insider_network_report(report),
            "trial_used": trial_used,
        }

        if not trial_used and r:
            with suppress(Exception):
                r.setex(cache_key, _CACHE_TTL, json.dumps(response))

        return response

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Insider network analysis failed for %s", wallet[:10])
        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed: {e!s}",
        ) from e


@router.get("/insider_network/health")
async def insider_network_health() -> dict:
    """Health check endpoint."""
    return {
        "status": "ok",
        "tool": "insider_network",
        "version": "1.0.0",
    }
