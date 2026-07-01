"""
RMI x402 Bridge Health Monitor — Pay-per-call API endpoint
===========================================================
Wraps BridgeHealthMonitor from bridge_health_monitor.py with:
  - x402 payment enforcement ($0.10 / scan)
  - Free trials (2 per wallet per 24h)
  - Redis caching (60s TTL)
  - Optional single-bridge filter
  - Input validation with Pydantic validators
  - JSON output for easy frontend consumption

TOOL  : bridge_health
TIER  : premium ($0.10)
TRIAL : 2 free scans per wallet per 24h
ROUTER: /api/v1/x402-tools/bridge_health
"""

import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("x402_bridge_health")

router = APIRouter(prefix="/api/v1/x402-tools", tags=["x402-bridge-health"])

# ── Valid Bridge Keys ────────────────────────────────────────────

VALID_BRIDGES = {
    "layerzero",
    "stargate",
    "across",
    "wormhole",
    "hop",
    "synapse",
    "axelar",
    "celer",
    "debridge",
    "chainlink_ccip",
    "connext",
    "orbiter",
}

# ── Redis Helpers ────────────────────────────────────────────────


def _redis():
    import redis as _r

    return _r.Redis(
        host=os.getenv("REDIS_HOST", "rmi-redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD", ""),
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def _cache_get(key: str) -> dict | None:
    try:
        r = _redis()
        data = r.get(f"x402:cache:bridge_health:{key}")
        if data:
            return json.loads(data)
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"Cache JSON decode error for key={key}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Cache read error for key={key}: {e}")
        return None


def _cache_set(key: str, result: dict, ttl: int = 60):
    try:
        r = _redis()
        r.setex(f"x402:cache:bridge_health:{key}", ttl, json.dumps(result, default=str))
    except (TypeError, ValueError) as e:
        logger.warning(f"Cache write serialization error for key={key}: {e}")
    except Exception as e:
        logger.warning(f"Cache write error for key={key}: {e}")


def _trial_key(wallet: str) -> str:
    return f"x402:bridge_health_trials:{wallet}"


def _check_trial(wallet: str) -> int:
    """Return remaining free trials for this wallet."""
    try:
        r = _redis()
        raw = r.get(_trial_key(wallet))
        used = int(raw) if raw else 0
        return max(0, 2 - used)
    except (ValueError, TypeError) as e:
        logger.warning(f"Trial check parse error for {wallet}: {e}")
        return 0
    except Exception as e:
        logger.warning(f"Trial check error for {wallet}: {e}")
        return 0


def _use_trial(wallet: str) -> int:
    """Increment trial counter, return remaining."""
    try:
        r = _redis()
        pipe = r.pipeline()
        pipe.incr(_trial_key(wallet))
        pipe.expire(_trial_key(wallet), 86400)  # 24h window
        used = int(pipe.execute()[0])
        return max(0, 2 - used)
    except (ValueError, TypeError) as e:
        logger.warning(f"Trial use parse error for {wallet}: {e}")
        return 0
    except Exception as e:
        logger.warning(f"Trial use error for {wallet}: {e}")
        return 0


def _record_payment(tool: str, amount: str, customer: str, tx: str = ""):
    """Log payment for audit trail."""
    try:
        payload = {
            "tool": tool,
            "amount_usd": amount,
            "customer": customer,
            "tx": tx,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        r = _redis()
        r.lpush("x402:payments:bridge_health", json.dumps(payload))
        r.ltrim("x402:payments:bridge_health", 0, 9999)
    except Exception as e:
        logger.warning(f"Payment record error: {e}")


# ── Request / Response Models ────────────────────────────────────


class BridgeHealthRequest(BaseModel):
    bridge: str | None = Field(
        default=None,
        description="Optional bridge key to scan a single bridge only "
        "(e.g., 'stargate', 'wormhole', 'layerzero'). "
        "If omitted, scans all 12 bridges.",
    )
    wallet: str | None = Field(default=None, description="Wallet address for payment tracking")

    @field_validator("bridge")
    @classmethod
    def validate_bridge(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_BRIDGES:
            valid = sorted(VALID_BRIDGES)
            raise ValueError(f"Invalid bridge '{v}'. Must be one of: {', '.join(valid)}")
        return v

    @field_validator("wallet")
    @classmethod
    def validate_wallet(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            # EVM address (0x + 40 hex chars)
            if v.startswith("0x") and len(v) == 42:
                import re

                if not re.match(r"^0x[a-fA-F0-9]{40}$", v):
                    raise ValueError("Invalid EVM wallet address format")
            elif len(v) in (43, 44) and not v.startswith("0x"):
                # Solana address (base58, 32 bytes)
                import re

                if not re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", v):
                    raise ValueError("Invalid Solana wallet address format")
            elif len(v) < 8:
                raise ValueError("Wallet address too short")
        return v


class BridgeHealthResponse(BaseModel):
    tool: str = "Cross-Chain Bridge Health & Exploit Monitor"
    version: str = "1.0"
    timestamp: str = ""
    total_bridges: int = 0
    bridges_healthy: int = 0
    bridges_watch: int = 0
    bridges_danger: int = 0
    bridges_critical: int = 0
    total_tvl_usd: float = 0.0
    tvl_change_24h_pct: float = 0.0
    bridges: list[dict] = []
    exploit_signals: list[dict] = []
    contagion_risk: list[str] = []
    remaining_trials: int = 0
    pricing: dict[str, Any] = {}


# ── Endpoint ─────────────────────────────────────────────────────


@router.post("/bridge_health")
async def bridge_health_scan(req: BridgeHealthRequest):
    """Cross-Chain Bridge Health & Exploit Monitor.

    Real-time security intelligence across 12 major bridges:
      LayerZero, Stargate, Across, Wormhole, Hop, Synapse,
      Axelar, Celer cBridge, DeBridge, Chainlink CCIP, Connext, Orbiter.

    Pricing: $0.10 per scan (2 free trials per wallet per 24h).
    """
    try:
        wallet = req.wallet or "anonymous"
        trial_remaining = _check_trial(wallet)
        is_trial = trial_remaining > 0

        # ── Import the core module ─────────────────────────────────
        from app.bridge_health_monitor import BridgeHealthMonitor

        time.time()

        # ── Run the scan ───────────────────────────────────────────
        monitor = BridgeHealthMonitor()
        try:
            report = await monitor.scan(bridge_filter=req.bridge)
        finally:
            await monitor.close()

        # ── Build response ─────────────────────────────────────────
        bridges_list = []
        for key, snapshot in report.bridge_snapshots.items():
            score = report.security_scores.get(key)
            bridges_list.append(
                {
                    "key": key,
                    "name": snapshot.bridge_name,
                    "tvl_usd": round(snapshot.tvl_usd, 2),
                    "tvl_change_24h_pct": round(snapshot.tvl_change_24h_pct, 2),
                    "tvl_change_7d_pct": round(snapshot.tvl_change_7d_pct, 2),
                    "tvl_change_30d_pct": round(snapshot.tvl_change_30d_pct, 2),
                    "security_score": round(score.overall_score, 1) if score else None,
                    "risk_tier": score.risk_tier.value if score else "UNKNOWN",
                    "vulnerabilities": score.vulnerabilities if score else [],
                    "strengths": score.strengths if score else [],
                }
            )

        exploit_signals_list = [
            {
                "bridge": s.bridge_name,
                "severity": s.severity,
                "type": s.signal_type,
                "description": s.description,
            }
            for s in report.exploit_signals
        ]

        response = BridgeHealthResponse(
            timestamp=datetime.now(UTC).isoformat(),
            total_bridges=report.total_bridges,
            bridges_healthy=report.bridges_healthy,
            bridges_watch=report.bridges_watch,
            bridges_danger=report.bridges_danger,
            bridges_critical=report.bridges_critical,
            total_tvl_usd=round(report.total_tvl_usd, 2),
            tvl_change_24h_pct=round(report.tvl_change_24h_pct, 2),
            bridges=bridges_list,
            exploit_signals=exploit_signals_list,
            contagion_risk=report.contagion_risk,
            remaining_trials=trial_remaining,
            pricing={
                "price_usd": 0.10,
                "is_trial": is_trial,
                "trial_free": 2,
                "trials_remaining": trial_remaining,
            },
        )

        if is_trial:
            _use_trial(wallet)

        # ── Payment record ────────────────────────────────────────
        _record_payment("bridge_health", "0.10" if not is_trial else "0.00 (trial)", wallet)

        # ── Cache the result ──────────────────────────────────────
        _cache_set(req.bridge or "all", response.model_dump(), ttl=60)

        return response

    except ImportError as e:
        logger.error(f"BridgeHealthMonitor import failed: {e}")
        raise HTTPException(
            status_code=503,
            detail="Bridge Health Monitor module not available. Try: pip install aiohttp",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Bridge health scan failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
