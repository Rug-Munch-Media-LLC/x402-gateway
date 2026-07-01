"""
x402 Token Watch / LP Monitor Endpoints
=========================================
Paid feature: Set monitoring conditions on tokens, receive alerts when triggered.
Prices: token_watch_create=$0.05, alert delivery free.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("x402_token_watch")

router = APIRouter(prefix="/api/v1/x402-tools", tags=["x402 Token Watch"])


# ─── Request Models ───────────────────────────────────────────────────


class WatchCreateRequest(BaseModel):
    token_address: str = Field(..., description="Token contract address")
    chain: str = Field(..., description="Blockchain (ethereum, bsc, solana, base, etc.)")
    condition: str = Field(
        ...,
        description="Watch condition: lp_drop_below, lp_unlocked, price_drop, price_change, rug_indicators",
    )
    threshold: float = Field(
        ...,
        description="Threshold value (e.g. 50000 for $50K LP, 0.001 for price, 20 for 20% change)",
    )
    check_interval: int = Field(300, description="Check interval in seconds (min 60, default 300)")
    webhook_url: str = Field("", description="Optional webhook URL for external alert delivery")


class WatchListRequest(BaseModel):
    token_address: str = Field("", description="Filter by token address")
    chain: str = Field("", description="Filter by chain")
    created_by: str = Field("", description="Filter by creator")


class WatchDeleteRequest(BaseModel):
    watch_id: str = Field(..., description="Watch ID to deactivate")


class WatchAlertsRequest(BaseModel):
    watch_id: str = Field("", description="Get alerts for specific watch")
    token_address: str = Field("", description="Get alerts for token")
    chain: str = Field("", description="Chain for token filter")


# ─── Endpoints ────────────────────────────────────────────────────────


@router.post("/token_watch_create")
async def token_watch_create(req: WatchCreateRequest, request: Request):
    """Create a token monitoring watch — alerts when conditions are met.

    Paid x402 tool ($0.05). Monitors LP levels, price drops, lock status changes.
    Alerts delivered via WebSocket and optional webhook.
    """
    from app.token_watch import WatchCondition, get_token_watch_service

    # Validate condition
    valid_conditions = [c.value for c in WatchCondition]
    if req.condition not in valid_conditions:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid condition '{req.condition}'. Must be one of: {', '.join(valid_conditions)}"},
        )

    # Clamp check interval
    check_interval = max(60, min(3600, req.check_interval))

    # Get creator from headers
    created_by = request.headers.get("x-wallet-address", "") or request.headers.get("X-Device-ID", "") or "anonymous"

    try:
        svc = get_token_watch_service()
        watch = await svc.create_watch(
            token_address=req.token_address,
            chain=req.chain,
            condition=req.condition,
            threshold=req.threshold,
            created_by=created_by,
            check_interval=check_interval,
            webhook_url=req.webhook_url,
        )

        # Record payment
        try:
            from app.routers.x402_tools import record_x402_payment

            await record_x402_payment("token_watch_create", "0.05", created_by)
        except Exception:
            pass

        return {
            "status": "created",
            "watch_id": watch.watch_id,
            "token_address": watch.token_address,
            "chain": watch.chain,
            "condition": watch.condition,
            "threshold": watch.threshold,
            "check_interval_seconds": watch.check_interval_seconds,
            "expires_at": watch.expires_at,
            "message": f"Watching {req.token_address[:10]}... on {req.chain} for {req.condition}. Alerts via WebSocket at /ws/alerts",
        }
    except Exception as e:
        logger.error(f"Token watch create failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)[:200]})


@router.post("/token_watch_list")
async def token_watch_list(req: WatchListRequest, request: Request):
    """List active token watches. Free endpoint."""
    from app.token_watch import get_token_watch_service

    try:
        svc = get_token_watch_service()
        watches = await svc.list_watches(
            token_address=req.token_address,
            chain=req.chain,
            created_by=req.created_by,
        )
        return {
            "status": "ok",
            "count": len(watches),
            "watches": [w.to_dict() for w in watches],
        }
    except Exception as e:
        logger.error(f"Token watch list failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)[:200]})


@router.post("/token_watch_delete")
async def token_watch_delete(req: WatchDeleteRequest, request: Request):
    """Deactivate a token watch. Free endpoint."""
    from app.token_watch import get_token_watch_service

    try:
        svc = get_token_watch_service()
        deleted = await svc.delete_watch(req.watch_id)
        return {
            "status": "deactivated" if deleted else "not_found",
            "watch_id": req.watch_id,
        }
    except Exception as e:
        logger.error(f"Token watch delete failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)[:200]})


@router.post("/token_watch_alerts")
async def token_watch_alerts(req: WatchAlertsRequest, request: Request):
    """Get triggered alerts for token watches. Free endpoint."""
    from app.token_watch import get_token_watch_service

    try:
        svc = get_token_watch_service()
        alerts = await svc.get_alerts(
            watch_id=req.watch_id,
            token_address=req.token_address,
            chain=req.chain,
        )
        return {
            "status": "ok",
            "count": len(alerts),
            "alerts": alerts[:50],
        }
    except Exception as e:
        logger.error(f"Token watch alerts failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)[:200]})


@router.post("/token_watch_check")
async def token_watch_check(req: WatchCreateRequest, request: Request):
    """One-shot check of a token's current status (LP, price, etc.) without creating a watch.

    Paid x402 tool ($0.03). Returns current LP, price, and whether any rug indicators detected.
    """
    from app.token_watch import get_token_watch_service

    try:
        svc = get_token_watch_service()
        pair_data = await svc._fetch_token_data(req.token_address, req.chain)

        if not pair_data:
            return {"status": "no_data", "token_address": req.token_address, "chain": req.chain}

        # Extract key metrics
        liquidity = pair_data.get("liquidity", {})
        lp_usd = liquidity.get("usd", 0) if isinstance(liquidity, dict) else liquidity
        price_usd = pair_data.get("priceUsd", 0)
        price_native = pair_data.get("priceNative", 0)
        dex_id = pair_data.get("dexId", "unknown")
        pair_address = pair_data.get("pairAddress", "")
        volume = pair_data.get("volume", {})
        volume_24h = volume.get("h24", 0) if isinstance(volume, dict) else volume
        price_change = pair_data.get("priceChange", {})
        change_24h = price_change.get("h24", 0) if isinstance(price_change, dict) else 0

        # Simple rug indicators
        warnings = []
        info = pair_data.get("info", {})
        if isinstance(info, dict):
            if not info.get("liquidityLocked", True):
                warnings.append("LP NOT LOCKED — high rug risk")
            if info.get("renouncedOwnership"):
                warnings.append("Ownership renounced (good sign)")

        # Record payment
        try:
            from app.routers.x402_tools import record_x402_payment

            await record_x402_payment("token_watch_check", "0.03", req.token_address)
        except Exception:
            pass

        return {
            "status": "ok",
            "token_address": req.token_address,
            "chain": req.chain,
            "dex": dex_id,
            "pair_address": pair_address,
            "price_usd": float(price_usd) if price_usd else 0,
            "price_native": price_native,
            "liquidity_usd": float(lp_usd) if lp_usd else 0,
            "volume_24h": float(volume_24h) if volume_24h else 0,
            "price_change_24h_pct": float(change_24h) if change_24h else 0,
            "warnings": warnings,
        }
    except Exception as e:
        logger.error(f"Token watch check failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)[:200]})
