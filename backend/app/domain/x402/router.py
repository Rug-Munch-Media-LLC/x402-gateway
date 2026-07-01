"""T34 x402 Paid Tools Catalog.

Per v4.0 §T34. Endpoints:
  GET  /api/v1/x402/catalog    — list all tools with pricing tiers
  GET  /api/v1/x402/usage      — per-agent usage stats
  GET  /api/v1/x402/receipts/{tx_hash}  — payment receipt
  POST /api/v1/x402/verify     — verify a payment header
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.catalog.service import get_catalog
from app.domain.x402.middleware import (
    PRICING,
    get_tool_metadata,
    get_tool_price_usd,
    verify_payment_header,
    verify_payment_on_chain,
)

router = APIRouter(prefix="/api/v1/x402", tags=["x402"])


class CatalogResponse(BaseModel):
    server: str = "rugmunch-intelligence"
    version: str = "4.0"
    tools: list[dict[str, Any]] = Field(default_factory=list)
    payment_protocol: str = "x402"


class UsageResponse(BaseModel):
    agent_id: str
    daily_calls: int
    free_limit: int
    paid_calls: int
    total_usd: float
    recent_receipts: list[dict[str, Any]] = Field(default_factory=list)


class ReceiptResponse(BaseModel):
    tx_hash: str
    tool: str
    agent_id: str | None = None
    amount_usd: float
    chain: str | None = None
    paid_at: str | None = None
    tier: str | None = None


@router.get("/catalog", response_model=CatalogResponse)
async def catalog() -> CatalogResponse:
    """List all paid tools with pricing tiers."""
    return CatalogResponse(tools=[get_tool_metadata(t) for t in PRICING])


@router.get("/usage", response_model=UsageResponse)
async def usage(request: Request) -> UsageResponse:
    """Per-agent usage stats: calls made, $ spent, top tools."""
    catalog = get_catalog()
    await catalog._init_stores()
    agent_id = request.headers.get("X-Agent-Id") or request.headers.get("X-Api-Key") or "anon"
    daily_calls = 0
    paid_calls = 0
    total_usd = 0.0
    recent: list[dict] = []
    if catalog._health.redis:
        try:
            from datetime import UTC, datetime
            day = datetime.now(UTC).strftime("%Y%m%d")
            key = f"x402:rl:{agent_id}:{day}"
            daily_calls = int(await catalog._redis.get(key) or 0)
        except Exception:
            pass
    if catalog._health.postgres:
        try:
            async with catalog._pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT tx_hash, tool, amount_usd, paid_at FROM x402_receipts "
                    "WHERE agent_id = $1 ORDER BY paid_at DESC LIMIT 10",
                    agent_id,
                )
            for r in rows:
                paid_calls += 1
                total_usd += float(r["amount_usd"] or 0)
                recent.append({
                    "tx_hash": r["tx_hash"],
                    "tool": r["tool"],
                    "amount_usd": float(r["amount_usd"] or 0),
                    "paid_at": r["paid_at"].isoformat() if r["paid_at"] else None,
                })
        except Exception as e:
            logging.getLogger(__name__).warning(f"usage_query_fail: {e}")
    return UsageResponse(
        agent_id=agent_id, daily_calls=daily_calls, free_limit=5,
        paid_calls=paid_calls, total_usd=round(total_usd, 4),
        recent_receipts=recent,
    )


@router.get("/receipts/{tx_hash}", response_model=ReceiptResponse)
async def receipt(tx_hash: str) -> ReceiptResponse:
    """Payment receipt for a specific on-chain tx."""
    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        raise HTTPException(503, "postgres unavailable")
    try:
        async with catalog._pg_pool.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT * FROM x402_receipts WHERE tx_hash=$1", tx_hash
            )
        if not r:
            raise HTTPException(404, "receipt not found")
        d = dict(r)
        return ReceiptResponse(
            tx_hash=d["tx_hash"],
            tool=d["tool"],
            agent_id=d.get("agent_id"),
            amount_usd=float(d.get("amount_usd", 0)),
            chain=d.get("chain"),
            paid_at=d["paid_at"].isoformat() if d.get("paid_at") else None,
            tier=d.get("tier"),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"receipt_query_fail: {e}")


@router.post("/verify")
async def verify_payment(request: Request) -> dict:
    """Verify an X-Payment header for a given tool."""
    body = await request.json()
    tool = body.get("tool", "")
    x_payment = body.get("x_payment", "")
    if tool not in PRICING:
        raise HTTPException(404, f"unknown tool: {tool}")
    if get_tool_price_usd(tool) == 0:
        return {"valid": True, "tier": "free", "amount_usd": 0}
    decoded = verify_payment_header(x_payment, get_tool_price_usd(tool))
    if not decoded:
        return {"valid": False, "reason": "invalid format"}
    tx_hash, _ = decoded
    valid = await verify_payment_on_chain(tx_hash, get_tool_price_usd(tool))
    return {
        "valid": valid,
        "tool": tool,
        "amount_usd": get_tool_price_usd(tool),
        "tx_hash": tx_hash,
    }
