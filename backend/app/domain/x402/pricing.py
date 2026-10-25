"""The x402 price list: the one table every caller prices from."""

from __future__ import annotations

from typing import Any

PRICING: dict[str, dict[str, Any]] = {
    "get_token_risk": {
        "free_tier": True,
        "pro_cents": 1,
        "ent_cents": 1,
        "description": "Real-time token risk score",
    },
    "get_wallet_analysis": {
        "free_tier": True,
        "pro_cents": 1,
        "ent_cents": 1,
        "description": "Wallet activity + reputation",
    },
    "get_deployer_reputation": {
        "free_tier": False,
        "pro_cents": 2,
        "ent_cents": 1,
        "description": "Deployer reputation score",
    },
    "get_news_sentiment": {
        "free_tier": True,
        "pro_cents": 1,
        "ent_cents": 1,
        "description": "Latest news + sentiment",
    },
    "generate_report": {
        "free_tier": False,
        "pro_cents": 500,
        "ent_cents": 400,
        "description": "Full AI research report",
    },
    "query_catalog": {
        "free_tier": False,
        "pro_cents": 5,
        "ent_cents": 3,
        "description": "Natural language catalog query",
    },
    "find_similar_tokens": {
        "free_tier": False,
        "pro_cents": 3,
        "ent_cents": 2,
        "description": "Vector-similar tokens",
    },
    "resolve_entity": {
        "free_tier": False,
        "pro_cents": 10,
        "ent_cents": 5,
        "description": "Cross-chain entity resolution",
    },
    "search_rag": {
        "free_tier": True,
        "pro_cents": 1,
        "ent_cents": 1,
        "description": "RAG semantic search",
    },
    "bulk_ingest": {
        "free_tier": False,
        "pro_cents": 10,
        "ent_cents": 5,
        "description": "Bulk RAG ingestion",
    },
}


def get_tool_price_usd(tool: str) -> float:
    """Pro-tier price in USD; 0.0 for free tools."""
    entry = PRICING.get(tool, {})
    if entry.get("free_tier", False):
        return 0.0
    return entry.get("pro_cents", 0) / 100.0
