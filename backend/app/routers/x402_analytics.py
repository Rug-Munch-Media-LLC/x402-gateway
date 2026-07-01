"""
RMI Payment Analytics Dashboard
================================
Real-time revenue, usage, and customer analytics.
Reads from Redis counters populated by x402 enforcement + developer tier.

Endpoints:
  GET /api/v1/analytics/x402/revenue        — Revenue overview
  GET /api/v1/analytics/x402/tools          — Per-tool usage + revenue
  GET /api/v1/analytics/x402/daily          — Daily breakdown (30 days)
  GET /api/v1/analytics/x402/developers     — Developer tier stats
  GET /api/v1/analytics/x402/funnel         — Conversion funnel (trial → paid)
  GET /api/v1/analytics/x402/summary        — One-line summary for dashboards

Author: RMI Development
Date: 2026-06-05
"""

from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v1/analytics/x402", tags=["x402-analytics"])


# ── Redis Helper ─────────────────────────────────────────────────


async def revenue_overview():
    """Get overall revenue metrics."""
    return JSONResponse(content=get_revenue_overview())


@router.get("/tools")
async def tool_breakdown():
    """Get per-tool usage and revenue."""
    return JSONResponse(content=get_tool_breakdown())


@router.get("/daily")
async def daily_breakdown(days: int = 30):
    """Get daily revenue breakdown."""
    days = min(days, 90)  # Cap at 90 days
    return JSONResponse(content=get_daily_breakdown(days))


@router.get("/developers")
async def developer_analytics():
    """Get developer tier analytics."""
    return JSONResponse(content=get_developer_analytics())


@router.get("/funnel")
async def conversion_funnel():
    """Get trial → paid conversion funnel."""
    return JSONResponse(content=get_conversion_funnel())


@router.get("/summary")
async def analytics_summary():
    """One-line summary for dashboards."""
    overview = get_revenue_overview()
    if "error" in overview:
        return JSONResponse(content=overview)

    revenue = overview["revenue"]
    calls = overview["tool_calls"]
    devs = overview["developer_tier"]

    return JSONResponse(
        content={
            "summary": f"${revenue['total_usd']:.4f} total revenue · {calls['total']} tool calls · {devs['total_keys']} dev keys",
            "today": f"${revenue['today_usd']:.4f} · {calls['today']} calls",
            "developers": f"{devs['total_keys']} registered · {devs['total_free_calls']} free calls used",
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )
