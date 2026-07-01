"""
x402 Router: contract_upgrade_monitor
========================================
Wraps ContractUpgradeAnalyzer with:
  - Address validation
  - x402 payment middleware integration
  - Caching (Redis if available)
  - Trial quota tracking
  - Rate limiting support

TOOL  : contract_upgrade_monitor
TIER  : security
PRICE : $0.05 (50000 atoms)
TRIAL : 2 free checks
ROUTER: /api/v1/x402-tools/contract_upgrade_monitor
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import suppress

import redis as _redis_mod
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.contract_upgrade_monitor import (
    format_upgrade_report,
    get_upgrade_analyzer,
    is_valid_evm_address,
)

logger = logging.getLogger("x402_contract_upgrade_monitor")

router = APIRouter(prefix="/api/v1/x402-tools", tags=["x402-tools"])

# ── Redis helpers ─────────────────────────────────────────────

_redis: _redis_mod.Redis | None = None


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


_CACHE_TTL = 600  # 10 minutes — upgrade data doesn't change rapidly


# ── Request Models ─────────────────────────────────────────────


class ContractUpgradeRequest(BaseModel):
    """Request body for contract upgrade monitoring."""

    contract_address: str = Field(
        ...,
        description="EVM contract address to check for proxy upgrade risk",
        min_length=42,
        max_length=42,
    )
    chain: str = Field(
        default="ethereum",
        description="Chain name (ethereum, bsc, polygon, arbitrum, optimism, base, avalanche)",
    )

    @field_validator("contract_address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        v = v.strip()
        if not is_valid_evm_address(v):
            raise ValueError(f"Invalid EVM address: {v}. Must be a 0x-prefixed 40-char hex address.")
        return v

    @field_validator("chain")
    @classmethod
    def validate_chain(cls, v: str) -> str:
        valid = {"ethereum", "bsc", "polygon", "arbitrum", "optimism", "base", "avalanche"}
        v = v.strip().lower()
        if v not in valid:
            raise ValueError(f"Unsupported chain: {v}. Supported: {', '.join(sorted(valid))}")
        return v


# ── Endpoints ──────────────────────────────────────────────────


@router.post("/contract_upgrade_monitor")
async def analyze_contract_upgrades(request: Request, body: ContractUpgradeRequest) -> dict:
    """
    Monitor proxy contract upgrades for malicious implementation swaps.
    Detects EIP-1967, EIP-1822 UUPS, Beacon, and Gnosis Safe proxies.
    Returns upgrade history, timelock status, and risk assessment (0-100).
    """
    address = body.contract_address.strip()
    chain = body.chain.strip().lower()

    # Check cache
    cache_key = f"contract_upgrade_monitor:{address}:{chain}"
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
    if isinstance(quota, dict) and quota.get("contract_upgrade_monitor", 0) > 0:
        trial_used = True
        logger.info(
            "Trial quota used for contract_upgrade_monitor on %s (%s)",
            address[:10],
            chain,
        )

    try:
        analyzer = get_upgrade_analyzer()
        report = await analyzer.analyze(
            contract_address=address,
            chain=chain,
        )

        response = {
            "success": True,
            "tool": "contract_upgrade_monitor",
            "contract_address": address,
            "chain": chain,
            "is_proxy": report.proxy_info.is_proxy if report.proxy_info else False,
            "proxy_type": report.proxy_info.proxy_type if report.proxy_info else None,
            "proxy_type_name": report.proxy_info.proxy_type_name if report.proxy_info else None,
            "implementation_address": report.proxy_info.implementation_address if report.proxy_info else None,
            "admin_address": report.proxy_info.admin_address if report.proxy_info else None,
            "beacon_address": report.proxy_info.beacon_address if report.proxy_info else None,
            "timelock_status": report.timelock_status,
            "upgrade_count_30d": report.upgrade_count_30d,
            "upgrade_history_count": len(report.upgrade_history),
            "recent_suspicious_upgrades": len(report.recent_suspicious_upgrades),
            "implementation_age_days": report.implementation_age_days,
            "admin_privileges": report.admin_privileges[:10],
            "risk_score": report.risk_score,
            "risk_factors": report.risk_factors[:10],
            "summary": report.summary,
            "report_text": format_upgrade_report(report),
            "trial_used": trial_used,
        }

        # Cache only paid results (not trial)
        if not trial_used and r:
            with suppress(Exception):
                r.setex(cache_key, _CACHE_TTL, json.dumps(response))

        return response

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Contract upgrade monitor failed for %s on %s", address[:10], chain)
        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed: {e!s}",
        ) from e


@router.get("/contract_upgrade_monitor/health")
async def contract_upgrade_health() -> dict:
    """Health check endpoint."""
    return {
        "status": "ok",
        "tool": "contract_upgrade_monitor",
        "version": "1.0.0",
    }
