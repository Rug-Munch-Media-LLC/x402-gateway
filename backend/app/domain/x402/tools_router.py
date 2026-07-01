"""x402 Paid Tools Router — endpoints that REQUIRE payment.

Mounted at /api/v1/x402-tools/*. Each endpoint is gated by the
x402_enforcement_middleware which returns 402 Payment Required
if no valid X-PAYMENT header is provided.

Tools here mirror the catalog at /api/v1/x402/catalog but require
USDC payment to our wallet before the request is processed.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request

from app.domain.x402.tools.label_tools import (
    get_entity_labels,
    get_wallet_labels,
)
from app.domain.x402.tools.market_tools import (
    get_market_overview,
    get_trending,
)
from app.domain.x402.tools.news_tools import (
    get_news,
    get_news_sentiment,
)
from app.domain.x402.tools.report_tools import (
    generate_report,
    get_report,
)
from app.domain.x402.tools.scanner_tools import (
    get_scan_result,
    run_scan,
)
from app.domain.x402.tools.token_tools import (
    deep_contract_audit,
    get_token_info,
    get_token_risk,
)
from app.domain.x402.tools.wallet_tools import (
    get_wallet_analysis,
    get_wallet_history,
)

router = APIRouter(prefix="/api/v1/x402-tools", tags=["x402-tools"])
log = logging.getLogger("x402_tools")


# ── Token tools ────────────────────────────────────────────
@router.get("/get_token_risk")
async def paid_get_token_risk(
    request: Request,
    token: str = Query(..., description="Token contract address"),
    chain: str = Query("ethereum", description="Chain name"),
):
    return await get_token_risk(token, chain)


@router.get("/get_token_info")
async def paid_get_token_info(
    request: Request,
    token: str = Query(...),
    chain: str = Query("ethereum"),
):
    return await get_token_info(token, chain)


@router.get("/deep_contract_audit")
async def paid_deep_contract_audit(
    request: Request,
    token: str = Query(...),
    chain: str = Query("ethereum"),
):
    return await deep_contract_audit(token, chain)


# ── Wallet tools ───────────────────────────────────────────
@router.get("/get_wallet_analysis")
async def paid_get_wallet_analysis(
    request: Request,
    wallet: str = Query(...),
    chain: str = Query("ethereum"),
):
    return await get_wallet_analysis(wallet, chain)


@router.get("/get_wallet_history")
async def paid_get_wallet_history(
    request: Request,
    wallet: str = Query(...),
    chain: str = Query("ethereum"),
):
    return await get_wallet_history(wallet, chain)


# ── News tools ─────────────────────────────────────────────
@router.get("/get_news")
async def paid_get_news(
    request: Request,
    topic: str = Query("crypto"),
    limit: int = Query(10, le=50),
):
    return await get_news(topic=topic, limit=limit)


@router.get("/get_news_sentiment")
async def paid_get_news_sentiment(
    request: Request,
    topic: str = Query("crypto"),
    limit: int = Query(10, le=50),
):
    return await get_news_sentiment(topic=topic, limit=limit)


# ── Scanner tools ───────────────────────────────────────────
@router.get("/get_scan_result")
async def paid_get_scan_result(
    request: Request,
    token: str = Query(...),
    chain: str = Query("ethereum"),
):
    return await get_scan_result(token, chain)


@router.post("/run_scan")
async def paid_run_scan(
    request: Request,
    token: str = Query(...),
    chain: str = Query("ethereum"),
):
    return await run_scan(token, chain)


# ── Market tools ────────────────────────────────────────────
@router.get("/get_market_overview")
async def paid_get_market_overview(request: Request):
    return await get_market_overview()


@router.get("/get_trending")
async def paid_get_trending(
    request: Request,
    timeframe: str = Query("24h"),
):
    return await get_trending(timeframe)


# ── Label tools ─────────────────────────────────────────────
@router.get("/get_entity_labels")
async def paid_get_entity_labels(
    request: Request,
    entity: str = Query(...),
):
    return await get_entity_labels(entity)


@router.get("/get_wallet_labels")
async def paid_get_wallet_labels(
    request: Request,
    wallet: str = Query(...),
):
    return await get_wallet_labels(wallet)


# ── Report tools ────────────────────────────────────────────
@router.post("/generate_report")
async def paid_generate_report(
    request: Request,
    token: str = Query(...),
    chain: str = Query("ethereum"),
    depth: str = Query("standard"),
):
    return await generate_report(token, chain, depth)


@router.get("/get_report")
async def paid_get_report(
    request: Request,
    report_id: str = Query(...),
):
    return await get_report(report_id)
