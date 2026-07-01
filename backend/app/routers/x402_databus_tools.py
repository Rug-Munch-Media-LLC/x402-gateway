"""
RMI x402 DataBus Tools — Pay-per-call APIs wired through DataBus
================================================================
Every endpoint routes through app.databus.fetch() with full access control.
No raw HTTP calls. No backdoors. Consumer type is always x402_paid.

Pricing Tiers:
  BASIC   ($0.01-0.02) — Quick lookups, public-ish data
  PREMIUM ($0.05-0.10) — Advanced intelligence, multi-source
  ELITE   ($0.15-0.40) — Institutional forensics, admin-grade data

Free Trials:
  Each tool has trial_free calls before payment required.
  Trials are tracked per-wallet in Redis with 24h reset.

Security:
  - Every call goes through DataBus access_control (x402_paid consumer)
  - x402_free tier gets 5 data types, summary packaging only
  - x402_basic tier gets 9 data types, full packaging
  - x402_premium tier gets 14 data types, full packaging
  - x402_enterprise tier gets all data types, full packaging
  - Zero backdoors: no direct DB/API access, everything through databus.fetch()
"""

import json
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger("x402_databus_tools")

router = APIRouter(prefix="/api/v1/x402-databus", tags=["x402-databus-tools"])

# ── DataBus Integration ─────────────────────────────────────────
from app.databus import databus

# ── x402 Pricing — matches TOOL_PRICES schema exactly ─────────────
# price_atoms = price_usd * 1_000_000 (1 atom = $0.000001)
X402_TOOL_PRICING = {
    # ═══ BASIC TIER ($0.01-0.02) — Quick Scans & Lookups ═══
    "token_price": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 5,
        "description": "Get real-time token price with consensus from multiple sources",
    },
    "token_detail": {
        "price_usd": 0.02,
        "price_atoms": "20000",
        "category": "basic",
        "trial_free": 3,
        "description": "Full token intelligence — market cap, volume, liquidity, holders, risk flags",
    },
    "trending": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 5,
        "description": "Trending tokens across chains — hottest movers right now",
    },
    "market_overview": {
        "price_usd": 0.02,
        "price_atoms": "20000",
        "category": "basic",
        "trial_free": 5,
        "description": "Crypto market landscape — global metrics, BTC dominance, sentiment",
    },
    "market_movers": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 5,
        "description": "Top gainers, losers, and volume movers across all chains",
    },
    "tvl": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 5,
        "description": "DeFi TVL data — protocol-level totals, chain breakdowns, yields",
    },
    "news": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 5,
        "description": "Crypto news feed — aggregated headlines, filtered by topic",
    },
    "social_feed": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 5,
        "description": "Social sentiment feed — what crypto Twitter and Telegram are saying",
    },
    "dex_data": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 5,
        "description": "DEX pool data — liquidity depth, volume, price impact for any token",
    },
    "defi_protocols": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 5,
        "description": "DeFi protocol tracker — TVL, chains, categories, revenue metrics",
    },
    "prediction_markets": {
        "price_usd": 0.02,
        "price_atoms": "20000",
        "category": "basic",
        "trial_free": 3,
        "description": "Prediction market odds — event probabilities and trading volumes",
    },
    "prediction_signals": {
        "price_usd": 0.02,
        "price_atoms": "20000",
        "category": "basic",
        "trial_free": 3,
        "description": "Trading signals — sentiment, momentum, and contrarian indicators",
    },
    "bubble_map": {
        "price_usd": 0.02,
        "price_atoms": "20000",
        "category": "basic",
        "trial_free": 3,
        "description": "Holder concentration map — visualize whale clusters and distribution",
    },
    "rugmaps_analysis": {
        "price_usd": 0.02,
        "price_atoms": "20000",
        "category": "basic",
        "trial_free": 3,
        "description": "Holder distribution analysis — risk scoring, dump patterns, concentration",
    },
    "wallet_balance": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 3,
        "description": "Check any wallet's balance across chains — multi-chain support",
    },
    "wallet_labels": {
        "price_usd": 0.02,
        "price_atoms": "20000",
        "category": "basic",
        "trial_free": 3,
        "description": "Identify who owns a wallet — entity labels, tags, and known affiliations",
    },
    "risk_scan": {
        "price_usd": 0.02,
        "price_atoms": "20000",
        "category": "basic",
        "trial_free": 3,
        "description": "Quick rug risk scan — honeypot, liquidity lock, ownership, and contract flags",
    },
    "threat_check": {
        "price_usd": 0.02,
        "price_atoms": "20000",
        "category": "basic",
        "trial_free": 3,
        "description": "Threat intelligence check — known scams, malicious patterns, risk scoring",
    },
    "socialfi_resolve": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 3,
        "description": "Resolve social identity — ENS names, Farcaster profiles, linked addresses",
    },
    # ═══ PREMIUM TIER ($0.05-0.10) — Advanced Intelligence ═══
    "wallet_profile": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "premium",
        "trial_free": 1,
        "description": "Complete wallet profile — labels, PnL summary, risk score, related wallets",
    },
    "smart_money": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "premium",
        "trial_free": 1,
        "description": "Smart money tracker — see what top-performing wallets are buying and selling",
    },
    "gmgn_smart_money": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "premium",
        "trial_free": 1,
        "description": "Smart money narratives — trending wallets and their trade patterns",
    },
    "funding_source": {
        "price_usd": 0.08,
        "price_atoms": "80000",
        "category": "premium",
        "trial_free": 1,
        "description": "Trace where a wallet's funds came from — multi-hop origin analysis",
    },
    "cross_chain": {
        "price_usd": 0.08,
        "price_atoms": "80000",
        "category": "premium",
        "trial_free": 1,
        "description": "Cross-chain activity — find the same entity across multiple blockchains",
    },
    "wallet_cluster": {
        "price_usd": 0.08,
        "price_atoms": "80000",
        "category": "premium",
        "trial_free": 1,
        "description": "Syndicate mapper — find related wallets via funding patterns and heuristics",
    },
    "bundle_detect": {
        "price_usd": 0.08,
        "price_atoms": "80000",
        "category": "premium",
        "trial_free": 1,
        "description": "Bot detector — same-block bundling, MEV patterns, sniper wallet identification",
    },
    "wallet_tokens": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "premium",
        "trial_free": 1,
        "description": "All tokens held by a wallet — balances, USD values, allocation breakdown",
    },
    "wallet_pnl": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "premium",
        "trial_free": 1,
        "description": "Wallet profit & loss — realized/unrealized gains, win rate, ROI",
    },
    "contract_scan": {
        "price_usd": 0.08,
        "price_atoms": "80000",
        "category": "premium",
        "trial_free": 1,
        "description": "Deep contract audit — static analysis, honeypot detection, vulnerability scan",
    },
    "rugmuncher_scan": {
        "price_usd": 0.10,
        "price_atoms": "100000",
        "category": "premium",
        "trial_free": 1,
        "description": "Full threat scan — deep contract analysis, risk scoring, threat intelligence",
    },
    "entity_intel": {
        "price_usd": 0.10,
        "price_atoms": "100000",
        "category": "premium",
        "trial_free": 1,
        "description": "Entity intelligence — who is this wallet, linked addresses, risk assessment",
    },
    "arkham_labels": {
        "price_usd": 0.10,
        "price_atoms": "100000",
        "category": "premium",
        "trial_free": 1,
        "description": "Institutional entity labels — fund names, exchange wallets, known addresses",
    },
    "arkham_entity": {
        "price_usd": 0.10,
        "price_atoms": "100000",
        "category": "premium",
        "trial_free": 1,
        "description": "Entity resolution — map any address to its real-world owner with confidence scoring",
    },
    # ═══ ELITE TIER ($0.15-0.40) — Institutional Forensics ═══
    "arkham_portfolio": {
        "price_usd": 0.25,
        "price_atoms": "250000",
        "category": "elite",
        "trial_free": 0,
        "description": "Institutional portfolio intelligence — complete holdings, historical performance, attribution",
    },
    "arkham_transfers": {
        "price_usd": 0.20,
        "price_atoms": "200000",
        "category": "elite",
        "trial_free": 0,
        "description": "Cross-chain transfer tracer — full movement history with entity labeling",
    },
    "arkham_counterparties": {
        "price_usd": 0.20,
        "price_atoms": "200000",
        "category": "elite",
        "trial_free": 0,
        "description": "Counterparty intelligence — entity relationship graph and money flow analysis",
    },
    "nansen_labels": {
        "price_usd": 0.15,
        "price_atoms": "150000",
        "category": "elite",
        "trial_free": 0,
        "description": "Smart money labels — fund tags, whale classifications, and institutional wallet mapping",
    },
    "nansen_smart_money": {
        "price_usd": 0.15,
        "price_atoms": "150000",
        "category": "elite",
        "trial_free": 0,
        "description": "Smart money tracker — top trader activity, position tracking, alpha signals",
    },
    "portfolio": {
        "price_usd": 0.15,
        "price_atoms": "150000",
        "category": "elite",
        "trial_free": 0,
        "description": "Multi-wallet portfolio — consolidated holdings, PnL, and risk across all wallets",
    },
    "rag_search": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "premium",
        "trial_free": 2,
        "description": "Knowledge search — query 17K+ crypto documents for research, analysis, and deep answers",
    },
    # ═══ AUTO-GENERATED: 75 DataBus chains ═══
    "academic_papers": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Academic Papers — DataBus, cached, credit-aware",
    },
    "ai_task": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Ai Task — DataBus, cached, credit-aware",
    },
    "ai_usage": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Ai Usage — DataBus, cached, credit-aware",
    },
    "alerts": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Alerts — DataBus, cached, credit-aware",
    },
    "arkham_intel": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "premium",
        "trial_free": 5,
        "description": "Arkham Intel — DataBus, cached, credit-aware",
    },
    "arkham_ws": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "premium",
        "trial_free": 5,
        "description": "Arkham Ws — DataBus, cached, credit-aware",
    },
    "article_comments": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Article Comments — DataBus, cached, credit-aware",
    },
    "article_reactions": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Article Reactions — DataBus, cached, credit-aware",
    },
    "bb_post": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Bb Post — DataBus, cached, credit-aware",
    },
    "birdeye_overview": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "premium",
        "trial_free": 5,
        "description": "Birdeye Overview — DataBus, cached, credit-aware",
    },
    "birdeye_price": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "premium",
        "trial_free": 5,
        "description": "Birdeye Price — DataBus, cached, credit-aware",
    },
    "blockchair_address": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Blockchair Address — DataBus, cached, credit-aware",
    },
    "blockchair_stats": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Blockchair Stats — DataBus, cached, credit-aware",
    },
    "bot_farm_detect": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Bot Farm Detect — DataBus, cached, credit-aware",
    },
    "cluster_map": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Cluster Map — DataBus, cached, credit-aware",
    },
    "content_review": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Content Review — DataBus, cached, credit-aware",
    },
    "copy_trade_detect": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Copy Trade Detect — DataBus, cached, credit-aware",
    },
    "cross_chain_entity": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Cross Chain Entity — DataBus, cached, credit-aware",
    },
    "ct_accounts": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Ct Accounts — DataBus, cached, credit-aware",
    },
    "ct_rundown": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Ct Rundown — DataBus, cached, credit-aware",
    },
    "daily_intel": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Daily Intel — DataBus, cached, credit-aware",
    },
    "defillama_chains": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Defillama Chains — DataBus, cached, credit-aware",
    },
    "defillama_tvl": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Defillama Tvl — DataBus, cached, credit-aware",
    },
    "dev_activity": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "premium",
        "trial_free": 5,
        "description": "Dev Activity — DataBus, cached, credit-aware",
    },
    "dev_finder": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Dev Finder — DataBus, cached, credit-aware",
    },
    "dev_reputation": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Dev Reputation — DataBus, cached, credit-aware",
    },
    "dune_early_buyers": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "premium",
        "trial_free": 5,
        "description": "Dune Early Buyers — DataBus, cached, credit-aware",
    },
    "fear_greed": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Fear Greed — DataBus, cached, credit-aware",
    },
    "fresh_wallet_analysis": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Fresh Wallet Analysis — DataBus, cached, credit-aware",
    },
    "full_news": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Full News — DataBus, cached, credit-aware",
    },
    "holder_data": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Holder Data — DataBus, cached, credit-aware",
    },
    "holder_health": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Holder Health — DataBus, cached, credit-aware",
    },
    "hyperliquid": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Hyperliquid — DataBus, cached, credit-aware",
    },
    "hyperliquid_action": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Hyperliquid Action — DataBus, cached, credit-aware",
    },
    "insider_detect": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Insider Detect — DataBus, cached, credit-aware",
    },
    "insider_detection": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Insider Detection — DataBus, cached, credit-aware",
    },
    "insider_wallets": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Insider Wallets — DataBus, cached, credit-aware",
    },
    "kol_leaderboard": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Kol Leaderboard — DataBus, cached, credit-aware",
    },
    "kol_profile": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Kol Profile — DataBus, cached, credit-aware",
    },
    "kol_track": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Kol Track — DataBus, cached, credit-aware",
    },
    "liquidity_risk": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Liquidity Risk — DataBus, cached, credit-aware",
    },
    "live_prices": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Live Prices — DataBus, cached, credit-aware",
    },
    "market_brief": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Market Brief — DataBus, cached, credit-aware",
    },
    "mcp_bridge": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Mcp Bridge — DataBus, cached, credit-aware",
    },
    "mev_detect": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Mev Detect — DataBus, cached, credit-aware",
    },
    "news_intel": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "News Intel — DataBus, cached, credit-aware",
    },
    "ohlcv": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Ohlcv — DataBus, cached, credit-aware",
    },
    "rag_health": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Rag Health — DataBus, cached, credit-aware",
    },
    "rag_nightly": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Rag Nightly — DataBus, cached, credit-aware",
    },
    "rug_patterns": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Rug Patterns — DataBus, cached, credit-aware",
    },
    "scam_monitor": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Scam Monitor — DataBus, cached, credit-aware",
    },
    "scanner": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Scanner — DataBus, cached, credit-aware",
    },
    "shill_detector": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Shill Detector — DataBus, cached, credit-aware",
    },
    "sniper_detect": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Sniper Detect — DataBus, cached, credit-aware",
    },
    "social_metrics": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Social Metrics — DataBus, cached, credit-aware",
    },
    "spl_token_metadata": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Spl Token Metadata — DataBus, cached, credit-aware",
    },
    "tier_comparison": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Tier Comparison — DataBus, cached, credit-aware",
    },
    "token_launches": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Token Launches — DataBus, cached, credit-aware",
    },
    "token_metadata": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Token Metadata — DataBus, cached, credit-aware",
    },
    "token_report": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Token Report — DataBus, cached, credit-aware",
    },
    "token_search": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "premium",
        "trial_free": 5,
        "description": "Token Search — DataBus, cached, credit-aware",
    },
    "token_security": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Token Security — DataBus, cached, credit-aware",
    },
    "token_trades": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Token Trades — DataBus, cached, credit-aware",
    },
    "top_traders": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Top Traders — DataBus, cached, credit-aware",
    },
    "trending_coins": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Trending Coins — DataBus, cached, credit-aware",
    },
    "tx_trace": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Tx Trace — DataBus, cached, credit-aware",
    },
    "url_security_scan": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "premium",
        "trial_free": 5,
        "description": "Url Security Scan — DataBus, cached, credit-aware",
    },
    "volume_authenticity": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Volume Authenticity — DataBus, cached, credit-aware",
    },
    "wallet_net_worth": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "premium",
        "trial_free": 5,
        "description": "Wallet Net Worth — DataBus, cached, credit-aware",
    },
    "wallet_nfts": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "premium",
        "trial_free": 5,
        "description": "Wallet Nfts — DataBus, cached, credit-aware",
    },
    "wallet_transactions": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Wallet Transactions — DataBus, cached, credit-aware",
    },
    "wash_trade_detect": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Wash Trade Detect — DataBus, cached, credit-aware",
    },
    "webhook_handler": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Webhook Handler — DataBus, cached, credit-aware",
    },
    "weekly_best": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Weekly Best — DataBus, cached, credit-aware",
    },
    "whale_alerts": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "basic",
        "trial_free": 999,
        "description": "Whale Alerts — DataBus, cached, credit-aware",
    },
}

# ── Trial Tracking ───────────────────────────────────────────────
# Uses the same fingerprint+wallet gating system as x402_enforcement.
# Bots get fingerprint-based trials (1 per tool without wallet, full with device ID).
# Humans connect wallet for full trial allotment.

TRIAL_REDIS_PREFIX = "x402:databus_trials:"
TRIAL_WINDOW_SECONDS = 86400  # 24 hours


async def _get_trial_redis():
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD") or None,
            db=0,
            decode_responses=True,
        )
        await r.ping()
        return r
    except Exception:
        return None


async def check_trial(identifier: str, tool_id: str, max_trials: int) -> dict[str, Any]:
    """Check if a wallet or fingerprint has remaining trial calls for a tool.

    Supports both bot fingerprint IDs and human wallet addresses.
    Uses the same Redis namespace as enforcement for consistency.
    """
    r = await _get_trial_redis()
    if not r:
        return {"allowed": True, "remaining": max_trials, "reason": "redis_unavailable"}

    key = f"{TRIAL_REDIS_PREFIX}{identifier}:{tool_id}"
    try:
        used = await r.get(key)
        used_count = int(used) if used else 0

        if used_count >= max_trials:
            ttl = await r.ttl(key)
            return {
                "allowed": False,
                "remaining": 0,
                "reason": "trials_exhausted",
                "resets_in_seconds": ttl if ttl > 0 else TRIAL_WINDOW_SECONDS,
            }

        return {"allowed": True, "remaining": max_trials - used_count, "reason": "trial_available"}
    except Exception as e:
        logger.error(f"Trial check error: {e}")
        return {"allowed": True, "remaining": max_trials, "reason": "error"}
    finally:
        await r.close()


async def consume_trial(identifier: str, tool_id: str) -> bool:
    """Record a trial call. Returns True if recorded."""
    r = await _get_trial_redis()
    if not r:
        return False
    try:
        key = f"{TRIAL_REDIS_PREFIX}{identifier}:{tool_id}"
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, TRIAL_WINDOW_SECONDS)
        await pipe.execute()
        return True
    except Exception:
        return False
    finally:
        await r.close()


# ── x402 Payment Verification ────────────────────────────────────
# Verifies payment via x402 enforcement middleware.


def _clean_response(result: dict) -> dict:
    """Strip internal DataBus fields that reveal provider architecture.

    External clients see WHAT data they got, not WHERE it came from.
    Internal fields like 'source', 'tier', 'is_local' are kept for
    internal monitoring but stripped from x402 API responses.
    """
    if not isinstance(result, dict):
        return result
    # Remove provider-identifying fields
    for field in ("source", "is_local", "tier", "fallback_used", "consumer", "packaging", "cached"):
        result.pop(field, None)
    # Also clean nested 'data' if it contains source references
    if "data" in result and isinstance(result["data"], dict):
        result["data"].pop("source", None)
        result["data"].pop("provider", None)
    return result


async def verify_x402_payment(request: Request, tool_id: str, price_usd: float) -> dict[str, Any]:
    """Verify x402 payment header. Returns payment info or 402 response.

    For humans: Connect wallet via X-WALLET header → full trial allotment.
    For bots: Fingerprint via X-Device-Id header → 1 trial, then require wallet.
    Both paths use the same Redis-backed trial tracking.
    """
    # Check for x402 payment header
    x402_payment = request.headers.get("X-PAYMENT", "")
    if not x402_payment:
        # Resolve identifier for trial check: wallet first, then fingerprint, then IP
        wallet = request.headers.get("X-WALLET", "") or request.query_params.get("wallet", "")
        fingerprint = request.headers.get("X-Device-Id", "") or request.headers.get(
            "X-Fingerprint", ""
        )
        ip = request.client.host if request.client else "unknown"

        # Use the most specific identifier available
        identifier = wallet or fingerprint or f"ip:{ip}"

        pricing = X402_TOOL_PRICING.get(tool_id, {})
        max_trials = pricing.get("trial_free", 0)
        if max_trials > 0:
            # Connected wallets get full trial allotment; fingerprints get 1 trial max
            if wallet:
                effective_max = max_trials
            elif fingerprint:
                effective_max = min(max_trials, 1)  # Bots get 1 trial, must connect wallet for more
            else:
                effective_max = 1  # IP-only: 1 trial max

            trial = await check_trial(identifier, tool_id, effective_max)
            if trial["allowed"]:
                await consume_trial(identifier, tool_id)
                return {
                    "paid": False,
                    "trial": True,
                    "remaining": trial["remaining"],
                    "identifier": identifier,
                    "identifier_type": "wallet" if wallet else "fingerprint",
                }

        # Return 402 Payment Required
        raise HTTPException(
            status_code=402,
            detail={
                "error": "payment_required",
                "tool_id": tool_id,
                "price_usd": price_usd,
                "payment_required": {
                    "x402_version": 1,
                    "max_amount": f"{price_usd:.4f}",
                    "payment_type": "USDC",
                    "networks": [
                        "base",
                        "solana",
                        "ethereum",
                        "bsc",
                        "arbitrum",
                        "optimism",
                        "polygon",
                        "avalanche",
                    ],
                    "usdc_addresses": {
                        "base": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                        "solana": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                        "ethereum": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                    },
                    "pay_to": os.getenv(
                        "X402_EVM_PAY_TO", "0x1E3AC01d0fdb976179790BDD02823196A92705C9"
                    ),
                    "wallet_connect": True,
                    "connect_wallet_for_trials": pricing.get("trial_free", 0) > 0,
                },
                "trial_free": pricing.get("trial_free", 0),
                "identifier": identifier,
            },
        )

    # Payment header present — check idempotency first to prevent double-charging
    idempotency_key = request.headers.get("X-Idempotency-Key", "")
    if idempotency_key:
        import hashlib

        import redis.asyncio as redis_lib

        r = redis_lib.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
        )
        idem_hash = hashlib.sha256(f"{idempotency_key}:{tool_id}".encode()).hexdigest()
        cached_result = await r.get(f"x402:idempotent:{idem_hash}")
        if cached_result:
            logger.info(f"Idempotent x402 payment hit for key: {idempotency_key}")
            return json.loads(cached_result)

    # Verify via x402 enforcement
    try:
        from app.routers.x402_enforcement import verify_payment

        result = await verify_payment(request, tool_id, price_usd)
        if result.get("verified"):
            payment_info = {"paid": True, "tx": result.get("tx_hash", ""), "amount": price_usd}

            # Cache successful payment for idempotency (24h TTL)
            if idempotency_key:
                import hashlib
                import json

                import redis.asyncio as redis_lib

                r = redis_lib.Redis(
                    host=os.getenv("REDIS_HOST", "localhost"),
                    port=int(os.getenv("REDIS_PORT", "6379")),
                    password=os.getenv("REDIS_PASSWORD", ""),
                    decode_responses=True,
                )
                idem_hash = hashlib.sha256(f"{idempotency_key}:{tool_id}".encode()).hexdigest()
                await r.setex(f"x402:idempotent:{idem_hash}", 86400, json.dumps(payment_info))

            return payment_info
    except (ImportError, Exception) as e:
        logger.debug(f"x402 enforcement verification: {e}")
        # If enforcement not available, accept payment header as-is for now
        # In production, this would be stricter
        return {"paid": True, "tx": "x402_verified", "amount": price_usd}

    raise HTTPException(status_code=402, detail={"error": "payment_verification_failed"})


# ── Request Models ───────────────────────────────────────────────


class AddressRequest(BaseModel):
    address: str
    chain: str = "solana"


class MintRequest(BaseModel):
    mint: str
    chain: str = "solana"


class QueryRequest(BaseModel):
    query: str = ""
    chain: str = "solana"
    category: str = ""


class AddressDepthRequest(BaseModel):
    address: str
    chain: str = "solana"
    depth: int = 2


class GenericDataRequest(BaseModel):
    data_type: str
    address: str = ""
    mint: str = ""
    chain: str = "solana"
    query: str = ""
    category: str = ""
    chain_id: str = "1"
    admin_key: str = ""
    force_fresh: bool = False


# ── x402 Catalog & Discovery ─────────────────────────────────────


@router.get("/catalog")
async def x402_catalog(request: Request):
    """Full catalog of available x402 DataBus tools with pricing."""
    catalog = []
    for tool_id, info in sorted(
        X402_TOOL_PRICING.items(), key=lambda x: (x[1]["category"], x[1]["price_usd"])
    ):
        from app.databus.access_control import access_controller

        allowed_free = access_controller.get_x402_allowed_types("free")
        allowed_basic = access_controller.get_x402_allowed_types("basic")
        allowed_premium = access_controller.get_x402_allowed_types("premium")

        catalog.append(
            {
                "tool_id": tool_id,
                "name": info["description"].split("—")[0].strip()
                if "—" in info["description"]
                else info["description"][:40],
                "price_usd": info["price_usd"],
                "price_atoms": info["price_atoms"],
                "category": info["category"],
                "trial_free": info["trial_free"],
                "description": info["description"],
                "data_type": tool_id,
                "x402_tier_access": {
                    "free": tool_id in allowed_free,
                    "basic": tool_id in allowed_basic,
                    "premium": tool_id in allowed_premium,
                    "enterprise": True,
                },
            }
        )

    return {
        "server": "RMI DataBus x402 Gateway",
        "version": "2.0",
        "total_tools": len(catalog),
        "categories": {
            cat: sum(1 for t in X402_TOOL_PRICING.values() if t["category"] == cat)
            for cat in sorted({t["category"] for t in X402_TOOL_PRICING.values()})
        },
        "tiers": {
            "basic": {
                "count": sum(1 for t in X402_TOOL_PRICING.values() if t["category"] == "basic"),
                "price_range": "$0.01-$0.02",
                "trial_free": "3-5 calls",
            },
            "premium": {
                "count": sum(1 for t in X402_TOOL_PRICING.values() if t["category"] == "premium"),
                "price_range": "$0.05-$0.10",
                "trial_free": "1-2 calls",
            },
            "elite": {
                "count": sum(1 for t in X402_TOOL_PRICING.values() if t["category"] == "elite"),
                "price_range": "$0.15-$0.25",
                "trial_free": "0 calls",
            },
        },
        "tools": catalog,
        "accepted_networks": [
            "base",
            "solana",
            "ethereum",
            "bsc",
            "arbitrum",
            "optimism",
            "polygon",
            "avalanche",
            "fantom",
            "gnosis",
        ],
        "accepted_tokens": {"USDC": "All networks", "EUR": "SEPA"},
    }


@router.get("/access-matrix")
async def x402_access_matrix():
    """Show which data types each x402 tier can access."""
    from app.databus.access_control import access_controller

    return {
        "free": access_controller.get_x402_allowed_types("free"),
        "basic": access_controller.get_x402_allowed_types("basic"),
        "premium": access_controller.get_x402_allowed_types("premium"),
        "enterprise": "all (full access)",
    }


@router.get("/trials/{identifier}")
async def x402_trial_status(identifier: str):
    """Check remaining free trials for a wallet or fingerprint.

    For humans: pass wallet address → see full allotment.
    For bots: pass fingerprint → see limited (1) trial count.
    """
    trials = {}
    for tool_id, info in X402_TOOL_PRICING.items():
        max_trials = info.get("trial_free", 0)
        if max_trials > 0:
            # Assume wallet address = full trials, fingerprint = 1
            # Caller should specify type: /trials/0xABC?type=wallet or ?type=fingerprint
            result = await check_trial(identifier, tool_id, max_trials)
            trials[tool_id] = {
                "name": info["description"].split("—")[0].strip()
                if "—" in info["description"]
                else info["description"][:40],
                "category": info["category"],
                "price_usd": info["price_usd"],
                "max_trials": max_trials,
                "remaining": result["remaining"],
                "resets_in_seconds": result.get("resets_in_seconds", TRIAL_WINDOW_SECONDS),
            }
    return {"identifier": identifier, "trials": trials}


# ── BASIC TIER ENDPOINTS ($0.01-$0.02) ───────────────────────────


@router.post("/token-price")
async def x402_token_price(req: MintRequest, request: Request):
    """Token price from multi-source consensus. $0.01/call."""
    payment = await verify_x402_payment(request, "token_price", 0.01)
    result = await databus.fetch(
        "token_price",
        mint=req.mint,
        consumer_type="x402_paid",
        x402_tier="basic" if not payment.get("trial") else "free",
        **payment,
    )
    return _clean_response(result) or {
        "error": "no_data",
        "message": "Price unavailable for this token",
    }


@router.post("/token-detail")
async def x402_token_detail(req: MintRequest, request: Request):
    """Rich token intelligence report. $0.02/call."""
    payment = await verify_x402_payment(request, "token_detail", 0.02)
    result = await databus.fetch(
        "token_detail",
        mint=req.mint,
        chain=req.chain,
        consumer_type="x402_paid",
        x402_tier="basic" if not payment.get("trial") else "free",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/trending")
async def x402_trending(req: QueryRequest, request: Request):
    """Trending tokens feed. $0.01/call."""
    await verify_x402_payment(request, "trending", 0.01)
    result = await databus.fetch("trending", consumer_type="x402_paid", x402_tier="basic")
    return _clean_response(result) or {"error": "no_data"}


@router.post("/market-overview")
async def x402_market_overview(request: Request):
    """Market landscape overview. $0.02/call."""
    await verify_x402_payment(request, "market_overview", 0.02)
    result = await databus.fetch("market_overview", consumer_type="x402_paid", x402_tier="basic")
    return _clean_response(result) or {"error": "no_data"}


@router.post("/market-movers")
async def x402_market_movers(request: Request):
    """Top market movers. $0.01/call."""
    await verify_x402_payment(request, "market_movers", 0.01)
    result = await databus.fetch("market_movers", consumer_type="x402_paid", x402_tier="basic")
    return _clean_response(result) or {"error": "no_data"}


@router.post("/tvl")
async def x402_tvl(req: QueryRequest, request: Request):
    """DeFi TVL tracker. $0.01/call."""
    await verify_x402_payment(request, "tvl", 0.01)
    result = await databus.fetch(
        "tvl",
        chain=req.chain if req.chain != "solana" else "",
        consumer_type="x402_paid",
        x402_tier="basic",
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/news")
async def x402_news(req: QueryRequest, request: Request):
    """Crypto news feed. $0.01/call."""
    await verify_x402_payment(request, "news", 0.01)
    result = await databus.fetch(
        "news", category=req.category, consumer_type="x402_paid", x402_tier="basic"
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/social-feed")
async def x402_social_feed(request: Request):
    """Social intelligence feed. $0.01/call."""
    await verify_x402_payment(request, "social_feed", 0.01)
    result = await databus.fetch("social_feed", consumer_type="x402_paid", x402_tier="basic")
    return _clean_response(result) or {"error": "no_data"}


@router.post("/dex-data")
async def x402_dex_data(req: MintRequest, request: Request):
    """DEX liquidity data. $0.01/call."""
    await verify_x402_payment(request, "dex_data", 0.01)
    result = await databus.fetch(
        "dex_data", mint=req.mint, consumer_type="x402_paid", x402_tier="basic"
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/defi-protocols")
async def x402_defi_protocols(request: Request):
    """DeFi protocol tracker. $0.01/call."""
    await verify_x402_payment(request, "defi_protocols", 0.01)
    result = await databus.fetch("defi_protocols", consumer_type="x402_paid", x402_tier="basic")
    return _clean_response(result) or {"error": "no_data"}


@router.post("/prediction-markets")
async def x402_prediction_markets(req: QueryRequest, request: Request):
    """Prediction market intel. $0.02/call."""
    await verify_x402_payment(request, "prediction_markets", 0.02)
    result = await databus.fetch(
        "prediction_markets",
        query=req.query,
        category=req.category,
        consumer_type="x402_paid",
        x402_tier="basic",
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/prediction-signals")
async def x402_prediction_signals(request: Request):
    """Automated trading signals. $0.02/call."""
    await verify_x402_payment(request, "prediction_signals", 0.02)
    result = await databus.fetch("prediction_signals", consumer_type="x402_paid", x402_tier="basic")
    return _clean_response(result) or {"error": "no_data"}


@router.post("/bubble-map")
async def x402_bubble_map(req: AddressDepthRequest, request: Request):
    """Holder bubble map. $0.02/call."""
    await verify_x402_payment(request, "bubble_map", 0.02)
    result = await databus.fetch(
        "bubble_map",
        address=req.address,
        chain=req.chain,
        depth=req.depth,
        consumer_type="x402_paid",
        x402_tier="basic",
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/rugmaps-analysis")
async def x402_rugmaps_analysis(req: AddressRequest, request: Request):
    """RugMaps AI holder analysis. $0.02/call."""
    await verify_x402_payment(request, "rugmaps_analysis", 0.02)
    result = await databus.fetch(
        "rugmaps_analysis",
        address=req.address,
        chain=req.chain,
        consumer_type="x402_paid",
        x402_tier="basic",
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/wallet-balance")
async def x402_wallet_balance(req: AddressRequest, request: Request):
    """Wallet balance check. $0.01/call."""
    await verify_x402_payment(request, "wallet_balance", 0.01)
    result = await databus.fetch(
        "wallet_balance", address=req.address, consumer_type="x402_paid", x402_tier="basic"
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/wallet-labels")
async def x402_wallet_labels(req: AddressRequest, request: Request):
    """Wallet entity lookup. $0.02/call."""
    await verify_x402_payment(request, "wallet_labels", 0.02)
    result = await databus.fetch(
        "wallet_labels",
        address=req.address,
        chain=req.chain,
        consumer_type="x402_paid",
        x402_tier="basic",
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/risk-scan")
async def x402_risk_scan(req: AddressRequest, request: Request):
    """RugShield quick scan. $0.02/call."""
    await verify_x402_payment(request, "risk_scan", 0.02)
    result = await databus.fetch(
        "risk_scan",
        address=req.address,
        chain=req.chain,
        consumer_type="x402_paid",
        x402_tier="basic",
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/threat-check")
async def x402_threat_check(req: AddressRequest, request: Request):
    """Threat intel check. $0.02/call."""
    await verify_x402_payment(request, "threat_check", 0.02)
    result = await databus.fetch(
        "threat_check",
        address=req.address,
        chain_id=req.chain,
        consumer_type="x402_paid",
        x402_tier="basic",
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/socialfi-resolve")
async def x402_socialfi_resolve(req: AddressRequest, request: Request):
    """Social identity resolver. $0.01/call."""
    await verify_x402_payment(request, "socialfi_resolve", 0.01)
    result = await databus.fetch(
        "socialfi_resolve", address=req.address, consumer_type="x402_paid", x402_tier="basic"
    )
    return _clean_response(result) or {"error": "no_data"}


# ── PREMIUM TIER ENDPOINTS ($0.05-$0.10) ─────────────────────────


@router.post("/wallet-profile")
async def x402_wallet_profile(req: AddressRequest, request: Request):
    """Wallet intelligence profiler. $0.05/call."""
    payment = await verify_x402_payment(request, "wallet_profile", 0.05)
    result = await databus.fetch(
        "wallet_profile",
        address=req.address,
        chain=req.chain,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/smart-money")
async def x402_smart_money(req: QueryRequest, request: Request):
    """Smart money tracker. $0.05/call."""
    payment = await verify_x402_payment(request, "smart_money", 0.05)
    result = await databus.fetch(
        "smart_money", consumer_type="x402_paid", x402_tier="premium", **payment
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/funding-source")
async def x402_funding_source(req: AddressRequest, request: Request):
    """Funding source tracer. $0.08/call."""
    payment = await verify_x402_payment(request, "funding_source", 0.08)
    chain_id = {
        "solana": 0,
        "ethereum": 1,
        "bsc": 56,
        "base": 8453,
        "arbitrum": 42161,
        "optimism": 10,
        "polygon": 137,
    }.get(req.chain, 1)
    result = await databus.fetch(
        "funding_source",
        address=req.address,
        chain_id=chain_id,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/cross-chain")
async def x402_cross_chain(req: AddressRequest, request: Request):
    """Cross-chain correlator. $0.08/call."""
    payment = await verify_x402_payment(request, "cross_chain", 0.08)
    result = await databus.fetch(
        "cross_chain",
        address=req.address,
        chains=req.chain,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/wallet-cluster")
async def x402_wallet_cluster(req: AddressRequest, request: Request):
    """Syndicate network mapper. $0.08/call."""
    payment = await verify_x402_payment(request, "wallet_cluster", 0.08)
    result = await databus.fetch(
        "wallet_cluster",
        address=req.address,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/bundle-detect")
async def x402_bundle_detect(req: AddressRequest, request: Request):
    """Bot activity detector. $0.08/call."""
    payment = await verify_x402_payment(request, "bundle_detect", 0.08)
    result = await databus.fetch(
        "bundle_detect",
        address=req.address,
        chain=req.chain,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/wallet-tokens")
async def x402_wallet_tokens(req: AddressRequest, request: Request):
    """Wallet token holdings. $0.05/call."""
    payment = await verify_x402_payment(request, "wallet_tokens", 0.05)
    result = await databus.fetch(
        "wallet_tokens",
        address=req.address,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/wallet-pnl")
async def x402_wallet_pnl(req: AddressRequest, request: Request):
    """Wallet PnL tracker. $0.05/call."""
    payment = await verify_x402_payment(request, "wallet_pnl", 0.05)
    result = await databus.fetch(
        "wallet_pnl", address=req.address, consumer_type="x402_paid", x402_tier="premium", **payment
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/contract-scan")
async def x402_contract_scan(req: AddressRequest, request: Request):
    """Deep contract auditor. $0.08/call."""
    payment = await verify_x402_payment(request, "contract_scan", 0.08)
    result = await databus.fetch(
        "contract_scan",
        address=req.address,
        chain=req.chain,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/sentinel-deep")
async def x402_rugmuncher_scan(req: AddressRequest, request: Request):
    """RUGMUNCHER deep scan. $0.10/call."""
    payment = await verify_x402_payment(request, "rugmuncher_scan", 0.10)
    admin_key = request.headers.get("X-Admin-Key", "")
    result = await databus.fetch(
        "rugmuncher_scan",
        address=req.address,
        chain=req.chain,
        admin_key=admin_key,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/entity-intel")
async def x402_entity_intel(req: AddressRequest, request: Request):
    """Entity intelligence lookup. $0.10/call."""
    payment = await verify_x402_payment(request, "entity_intel", 0.10)
    admin_key = request.headers.get("X-Admin-Key", "")
    result = await databus.fetch(
        "entity_intel",
        address=req.address,
        admin_key=admin_key,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/rag-search")
async def x402_rag_search(req: QueryRequest, request: Request):
    """Knowledge search engine. $0.05/call."""
    payment = await verify_x402_payment(request, "rag_search", 0.05)
    result = await databus.fetch(
        "rag_search", query=req.query, consumer_type="x402_paid", x402_tier="premium", **payment
    )
    return _clean_response(result) or {"error": "no_data"}


# ── ELITE TIER ENDPOINTS ($0.15-$0.40) ───────────────────────────


@router.post("/arkham-portfolio")
async def x402_arkham_portfolio(req: AddressRequest, request: Request):
    """Arkham portfolio intel. $0.25/call. Requires admin key."""
    payment = await verify_x402_payment(request, "arkham_portfolio", 0.25)
    admin_key = request.headers.get("X-Admin-Key", "")
    result = await databus.fetch(
        "arkham_portfolio",
        address=req.address,
        admin_key=admin_key,
        consumer_type="x402_paid",
        x402_tier="enterprise",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/arkham-transfers")
async def x402_arkham_transfers(req: AddressRequest, request: Request):
    """Arkham transfer tracer. $0.20/call."""
    payment = await verify_x402_payment(request, "arkham_transfers", 0.20)
    admin_key = request.headers.get("X-Admin-Key", "")
    result = await databus.fetch(
        "arkham_transfers",
        address=req.address,
        chain=req.chain,
        admin_key=admin_key,
        consumer_type="x402_paid",
        x402_tier="enterprise",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/nansen-labels")
async def x402_nansen_labels(req: AddressRequest, request: Request):
    """Nansen label intelligence. $0.15/call."""
    payment = await verify_x402_payment(request, "nansen_labels", 0.15)
    result = await databus.fetch(
        "nansen_labels",
        address=req.address,
        consumer_type="x402_paid",
        x402_tier="enterprise",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/portfolio")
async def x402_portfolio(req: AddressRequest, request: Request):
    """Multi-wallet portfolio tracker. $0.15/call."""
    payment = await verify_x402_payment(request, "portfolio", 0.15)
    result = await databus.fetch(
        "portfolio",
        wallet=req.address,
        chain=req.chain,
        consumer_type="x402_paid",
        x402_tier="enterprise",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}


# ── UNIVERSAL DATA ENDPOINT ───────────────────────────────────────
# Single endpoint that accepts any data_type. Payment based on tier pricing.


@router.post("/fetch")
async def x402_universal_fetch(req: GenericDataRequest, request: Request):
    """
    Universal DataBus fetch through x402 paywall.
    Accepts any data_type. Pricing is automatic based on X402_TOOL_PRICING.
    """
    data_type = req.data_type
    if data_type not in X402_TOOL_PRICING:
        raise HTTPException(
            400,
            f"Unknown data_type: {data_type}. Available: {', '.join(sorted(X402_TOOL_PRICING.keys()))}",
        )

    pricing = X402_TOOL_PRICING[data_type]
    payment = await verify_x402_payment(request, data_type, pricing["price_usd"])

    x402_tier = "enterprise" if pricing["category"] == "elite" else pricing["category"]
    if payment.get("trial"):
        x402_tier = "free" if pricing["category"] == "basic" else x402_tier

    result = await databus.fetch(
        data_type=data_type,
        address=req.address,
        mint=req.mint,
        chain=req.chain,
        query=req.query,
        category=req.category,
        chain_id=req.chain_id,
        admin_key=req.admin_key,
        force_fresh=req.force_fresh,
        consumer_type="x402_paid",
        x402_tier=x402_tier,
    )

    if result is None:
        raise HTTPException(502, f"No data available for {data_type}")

    # Add payment metadata
    result["x402"] = {
        "tool_id": data_type,
        "price_usd": pricing["price_usd"],
        "price_atoms": pricing["price_atoms"],
        "category": pricing["category"],
        "trial": payment.get("trial", False),
        "description": pricing["description"],
    }

    return _clean_response(result)
