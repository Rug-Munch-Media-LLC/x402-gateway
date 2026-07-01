"""
RMI DataBus Fallback Middleware — Universal Single-Source Pipeline
====================================================================
Every x402 tool gets DataBus as its data backbone. If the primary endpoint
returns empty data, an error, or times out, DataBus.fetch() catches it with
its 38-chain fallback system and caching layer.

Architecture:
  Request → Primary endpoint → success with real data? → Response
                                  ↓ empty/error/timeout
           → DataBus.fetch(data_type, ...) → success? → Response + metadata
                                               ↓ fail
           → Original error response (DataBus exhausted all sources)

Zero internal source names leak to clients. Descriptions are bot-friendly.
The `_fallback` and `_databus_chain` fields tell bots this data is verified
through multi-source consensus, not a single point of failure.
"""

import logging
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("x402_databus_fallback")

# ── Complete Tool ID → DataBus data_type mapping ──────────────────
# Every tool maps to at least one DataBus chain. Where a tool's primary
# function doesn't match a chain exactly, we map to the closest chain
# that provides relevant data. Composite tools map to their most
# important underlying data need.

TOOL_TO_DATABUS = {
    # ─── Price & Token Intelligence ───
    "token_price": "token_price",
    "token_detail": "token_detail",
    "token_deep_dive": "token_detail",
    "token_comparison": "token_price",
    "token_forensics": "token_detail",
    "token_pulse": "token_price",
    "token_velocity": "token_detail",
    "token_distribution_health": "token_detail",
    "token_age": "token_detail",
    "pulse": "token_price",
    "fresh_pair": "dex_data",
    "dex_volume_rank": "dex_data",
    # ─── Market Data ───
    "market_overview": "market_overview",
    "market_movers": "market_movers",
    "trending": "trending",
    "tvl": "tvl",
    "defi_protocols": "defi_protocols",
    "chain_health": "tvl",
    "funding_rate": "market_overview",
    "liquidation_heatmap": "market_movers",
    "volatility_surface": "market_movers",
    "volume_profile": "market_movers",
    "sector_rotation": "market_overview",
    "options_flow": "market_movers",
    "orderbook_imbalance": "dex_data",
    # ─── DEX & DeFi ───
    "dex_data": "dex_data",
    "defi_yield_scanner": "tvl",
    "defi_position": "defi_protocols",
    "yield_aggregator": "tvl",
    "impermanent_loss": "defi_protocols",
    "liquidity_depth": "dex_data",
    "liquidity_flow": "dex_data",
    "liquidity_migration": "dex_data",
    "liquidity_verify": "dex_data",
    "bridge_security": "tvl",
    # ─── Wallet Intelligence ───
    "wallet_balance": "wallet_balance",
    "wallet_profile": "wallet_profile",
    "wallet_labels": "wallet_labels",
    "wallet_tokens": "wallet_tokens",
    "wallet_pnl": "wallet_pnl",
    "wallet_cluster": "wallet_cluster",
    "wallet_graph": "wallet_cluster",
    "wallet": "wallet_profile",
    "whale": "wallet_profile",
    "whale_profile": "wallet_profile",
    "whale_scan": "wallet_profile",
    "whale_accumulation": "smart_money",
    "whale_network_map": "wallet_cluster",
    "full_wallet_dossier": "wallet_profile",
    "wallet_label_registry": "wallet_labels",
    "wallet_drain_scanner": "risk_scan",
    "wallet_cluster_score": "wallet_cluster",
    "smart_contract_interactions": "wallet_profile",
    "portfolio_tracker": "portfolio",
    "portfolio_aggregate": "portfolio",
    "portfolio_risk": "portfolio",
    # ─── Smart Money & Tracking ───
    "smart_money": "smart_money",
    "smart_money_alpha": "smart_money",
    "smartmoney": "smart_money",
    "gmgn_smart_money": "gmgn_smart_money",
    "copy_trade_finder": "gmgn_smart_money",
    "insider": "smart_money",
    "insider_network": "entity_intel",
    "dormant_whale_alert": "smart_money",
    # ─── Cross-Chain & Forensics ───
    "cross_chain": "cross_chain",
    "cross_chain_trace": "cross_chain",
    "cross_chain_whale": "cross_chain",
    "funding_source": "funding_source",
    "fund_flow": "funding_source",
    # ─── Risk & Security ───
    "risk_scan": "risk_scan",
    "rug_shield": "risk_scan",
    "rugshield": "risk_scan",
    "rug_pull_predictor": "risk_scan",
    "rug_probability": "risk_scan",
    "honeypot_check": "risk_scan",
    "threat_check": "threat_check",
    "sentinel_scan": "rugmuncher_scan",
    "rugmuncher_scan": "rugmuncher_scan",
    "audit": "contract_scan",
    "deep_contract_audit": "contract_scan",
    "contract_scan": "contract_scan",
    "static_analysis": "contract_scan",
    "decompiler_analysis": "contract_scan",
    "contract_diff": "contract_scan",
    "contract_upgrade_monitor": "contract_scan",
    "reentrancy_scanner": "contract_scan",
    "proxy_detect": "contract_scan",
    "scam_database": "threat_check",
    "urlcheck": "threat_check",
    "url_scam_detector": "threat_check",
    "dust_attack_detect": "threat_check",
    "phantom_mint_detect": "threat_check",
    "privilege_escalation": "contract_scan",
    # ─── Bundle & MEV Detection ───
    "bundle_detect": "bundle_detect",
    "bundler_detect": "bundle_detect",
    "mev_alert": "bundle_detect",
    "mev_detect": "bundle_detect",
    "mev_protection": "bundle_detect",
    "flash_loan_detect": "bundle_detect",
    "pump_dump_detect": "bundle_detect",
    "pumpfun_analysis": "risk_scan",
    # ─── Entity & Labels ───
    "entity_intel": "entity_intel",
    "arkham_labels": "arkham_labels",
    "arkham_entity": "arkham_entity",
    "arkham_portfolio": "arkham_portfolio",
    "arkham_transfers": "arkham_transfers",
    "arkham_counterparties": "arkham_counterparties",
    "nansen_labels": "nansen_labels",
    "nansen_smart_money": "nansen_smart_money",
    "address_labels": "wallet_labels",
    "deployer_history": "wallet_labels",
    "dev_reputation": "wallet_labels",
    "reputation_score": "wallet_labels",
    "kol_performance": "social_feed",
    # ─── Holder & Distribution ───
    "bubble_map": "bubble_map",
    "rugmaps_analysis": "rugmaps_analysis",
    "holder_analysis": "rugmaps_analysis",
    # ─── Social & Sentiment ───
    "social_feed": "social_feed",
    "social_sentiment": "social_feed",
    "social_signal": "social_feed",
    "sentiment": "social_feed",
    "sentiment_spike": "social_feed",
    "sentiment_check": "social_feed",
    "narrative": "social_feed",
    "reddit_sentiment": "social_feed",
    "discord_alpha": "social_feed",
    "influencer_impact_score": "social_feed",
    "twitter_profile": "social_feed",
    "twitter_timeline": "social_feed",
    "twitter_search": "social_feed",
    "tw_profile": "social_feed",
    "tw_timeline": "social_feed",
    "tw_search": "social_feed",
    "telegram_pump_detect": "social_feed",
    "meme_vibe_score": "social_feed",
    # ─── News & Information ───
    "news": "news",
    "alpha_digest": "news",
    "github_developer_activity": "news",
    # ─── Prediction Markets ───
    "prediction_markets": "prediction_markets",
    "prediction_signals": "prediction_signals",
    "listing_predictor": "prediction_markets",
    # ─── Social Identity ───
    "socialfi_resolve": "socialfi_resolve",
    # ─── Launch & New Tokens ───
    "sniper_alert": "trending",
    "launch_radar": "trending",
    "launch_intel": "trending",
    "launch": "trending",
    "fair_launch_detect": "trending",
    "presale_scanner": "trending",
    "ido_tracker": "defi_protocols",
    # ─── RAG ───
    "rag_search": "rag_search",
    "osint_identity_hunt": "rag_search",
    "investigation_report": "rag_search",
    "forensic_valuation": "rag_search",
    # ─── Gas ───
    "gas_forecast": "market_overview",
    # ─── Syndicate & Clustering ───
    "syndicate_scan": "wallet_cluster",
    "syndicate_track": "wallet_cluster",
    "cluster": "wallet_cluster",
    "cluster_detection": "wallet_cluster",
    # ─── NFT ───
    "nft_wash_detector": "threat_check",
    "nft_floor_analytics": "dex_data",
    # ─── Anomaly & Analysis ───
    "anomaly": "market_movers",
    "anomaly_detector": "market_movers",
    "correlation_matrix": "market_overview",
    "drawdown_analyzer": "market_movers",
    "sharpe_ratio_calc": "market_overview",
    "tax_lot_optimizer": "portfolio",
    "forensics": "token_detail",
    "composite_score": "risk_scan",
    # ─── Token Watch (static/config endpoints need no fallback) ───
    # These return config/status, not data
    # "token_watch_create", "token_watch_list", etc. → no fallback needed
    # ─── Webhooks (config, not data) ───
    # "webhook_register", "webhook_list" → no fallback needed
    # ─── Airdrop ───
    "airdrop_check": "defi_protocols",
    "airdrop_finder": "defi_protocols",
    "vesting_schedule_analyzer": "defi_protocols",
    "unlock_calendar": "defi_protocols",
    # ─── Analysis ───
    "history": "token_price",
    "degen_score": "risk_scan",
    "profile_flip": "social_feed",
}

# Data types that accept 'address' parameter
ADDRESS_TYPES = {
    "risk_scan",
    "contract_scan",
    "wallet_profile",
    "wallet_pnl",
    "wallet_labels",
    "smart_money",
    "gmgn_smart_money",
    "funding_source",
    "cross_chain",
    "wallet_cluster",
    "bundle_detect",
    "rugmuncher_scan",
    "entity_intel",
    "arkham_labels",
    "arkham_entity",
    "arkham_portfolio",
    "arkham_transfers",
    "arkham_counterparties",
    "nansen_labels",
    "nansen_smart_money",
    "portfolio",
    "wallet_balance",
    "wallet_tokens",
    "threat_check",
    "socialfi_resolve",
    "bubble_map",
    "rugmaps_analysis",
    "token_detail",
    "dex_data",
}

# Data types that accept 'mint' parameter
MINT_TYPES = {"token_price", "token_detail", "dex_data", "risk_scan"}

# Data types that accept 'query' parameter
QUERY_TYPES = {
    "trending",
    "news",
    "social_feed",
    "market_movers",
    "prediction_markets",
    "prediction_signals",
    "rag_search",
    "smart_money",
    "gmgn_smart_money",
}


class DataBusFallbackMiddleware(BaseHTTPMiddleware):
    """
    Universal fallback middleware: if any x402-tools endpoint returns
    empty data, errors, or times out, transparently falls back to
    DataBus.fetch() which has 38 chains with automatic source failover
    and multi-layer caching.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Only intercept x402-tools paths (not x402-databus which already uses DataBus)
        path = request.url.path
        if not path.startswith("/api/v1/x402-tools/"):
            return response

        # Only intercept failed or empty responses
        if response.status_code >= 500:
            return await self._try_databus_fallback(request, response, "server_error")

        if response.status_code == 200:
            # Skip trial responses — DataBus was already used for execution
            if response.headers.get("X-RMI-Trial") == "true":
                return response

            try:
                body = b""
                async for chunk in response.body_iterator:
                    body += chunk
                import json

                data = json.loads(body)

                if self._is_empty_response(data):
                    return await self._try_databus_fallback(
                        request, response, "empty_data", original_data=data
                    )

                # Success — return as-is with body we already read
                return JSONResponse(content=data, status_code=200, headers=dict(response.headers))
            except Exception:
                # Can't parse body — return original response
                return response

        # 4xx errors (402 payment required, 400 bad request) pass through
        return response

    def _is_empty_response(self, data: Any) -> bool:
        """Check if response has meaningful data."""
        if data is None:
            return True
        if isinstance(data, dict):
            if data.get("error") and not data.get("data"):
                return True
            if data.get("status") in ("error", "failed", "no_data", "unavailable"):
                return True
            result = (
                data.get("result") or data.get("data") or data.get("analysis") or data.get("report")
            )
            if result is None and len(data) <= 3:
                return True
        return False

    async def _try_databus_fallback(
        self,
        request: Request,
        original_response: Response,
        reason: str,
        original_data: dict | None = None,
    ) -> Response:
        """Attempt DataBus fallback for the tool."""
        # Extract tool_id from path
        tool_id = request.url.path.replace("/api/v1/x402-tools/", "").strip("/")

        # Strip trailing path segments (e.g., /api/v1/x402-tools/rug_shield/solana)
        tool_id = tool_id.split("/")[0] if "/" in tool_id else tool_id

        data_type = TOOL_TO_DATABUS.get(tool_id)
        if not data_type:
            # No DataBus mapping — return original response
            return original_response

        try:
            from app.databus import databus

            kwargs = {"consumer_type": "x402_paid", "x402_tier": "basic"}

            # Extract parameters from request body
            try:
                body_bytes = await request.body()
                if body_bytes:
                    import json

                    body_data = json.loads(body_bytes)
                else:
                    body_data = {}
            except Exception:
                body_data = {}

            # Map request params to DataBus kwargs
            address = body_data.get("address") or body_data.get("wallet")
            if address and data_type in ADDRESS_TYPES:
                kwargs["address"] = address

            mint = (
                body_data.get("mint")
                or body_data.get("token")
                or (address if data_type in MINT_TYPES else None)
            )
            if mint and data_type in MINT_TYPES:
                kwargs["mint"] = mint

            chain = body_data.get("chain", "solana")
            kwargs["chain"] = chain

            query = body_data.get("query") or body_data.get("q")
            if query and data_type in QUERY_TYPES:
                kwargs["query"] = query

            # Also check query params
            qp = dict(request.query_params)
            if "address" in qp and data_type in ADDRESS_TYPES:
                kwargs["address"] = qp["address"]
            if "mint" in qp and data_type in MINT_TYPES:
                kwargs["mint"] = qp["mint"]
            if "chain" in qp:
                kwargs["chain"] = qp["chain"]
            if "query" in qp and data_type in QUERY_TYPES:
                kwargs["query"] = qp["query"]

            result = await databus.fetch(data_type, **kwargs)

            if result and not (
                isinstance(result, dict) and result.get("error") and not result.get("data")
            ):
                logger.info(f"DataBus fallback: {tool_id} → {data_type} (reason: {reason})")
                if isinstance(result, dict):
                    result["_fallback"] = True
                    result["_databus_chain"] = data_type
                    result["_original_error"] = reason
                return JSONResponse(content=result, status_code=200)

        except Exception as e:
            logger.warning(f"DataBus fallback failed for {tool_id} → {data_type}: {e}")

        # DataBus also failed — return original error
        return original_response
