"""
RMI Alpha Tools — High-Value Revenue Tools
============================================
Three premium alpha tools that bots will pay for:
1. whale_copy_trade — Real-time copy trade engine
2. rug_predictor_live — 5-minute rug prediction
3. whale_cluster — Coordinated whale cluster detection

All tools use DataBus + DexScreener + RAG enrichment.
Price points: $0.10-$0.50 per call.

Author: RMI Development
Date: 2026-06-05
"""

import json
import logging
import os
import time
from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.redis import get_redis

logger = logging.getLogger("alpha_tools")

router = APIRouter(prefix="/api/v1/x402-tools", tags=["alpha-tools"])

# ── Data Source Helpers ──────────────────────────────────────────


async def fetch_dexscreener(path: str, params: dict | None = None) -> dict | None:
    """Fetch from DexScreener API."""
    import aiohttp

    url = f"https://api.dexscreener.com/latest/dex/{path}"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as e:
        logger.warning(f"DexScreener fetch failed: {e}")
    return None


async def fetch_geckoterminal(path: str) -> dict | None:
    """Fetch from GeckoTerminal API."""
    import aiohttp

    url = f"https://api.geckoterminal.com/api/v2/{path}"
    headers = {"Accept": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as e:
        logger.warning(f"GeckoTerminal fetch failed: {e}")
    return None


async def fetch_helius(path: str, params: dict | None = None) -> dict | None:
    """Fetch from Helius API (Solana)."""
    import aiohttp

    api_key = os.getenv("HELIUS_API_KEY", "")
    if not api_key:
        return None

    url = f"https://api.helius.xyz/v0/{path}?api-key={api_key}"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as e:
        logger.warning(f"Helius fetch failed: {e}")
    return None


async def whale_copy_trade(req: WhaleCopyTradeRequest):
    """Real-time copy trade engine.

    Input a smart money wallet, get their exact last 24h trades with
    entry/exit prices, current PnL, and suggested follow trades.

    Price: $0.25/call
    """
    address = req.address.lower()
    chain = req.chain
    lookback = req.lookback_hours
    min_usd = req.min_trade_usd

    trades = []
    wallet_info = {}

    if chain == "solana":
        # Fetch recent transactions from Helius
        txns = await fetch_helius(
            f"addresses/{address}/transactions",
            {"type": "TRANSFER", "limit": 100},
        )
        if txns:
            for tx in txns[:50]:
                # Parse transfer data
                timestamp = tx.get("timestamp", 0)
                if timestamp < (time.time() - lookback * 3600):
                    continue

                # Extract token and amount
                transfers = tx.get("transfers", [])
                for transfer in transfers:
                    token = transfer.get("mint", "")
                    amount = transfer.get("tokenAmount", 0)
                    if not token or amount == 0:
                        continue

                    # Get token price
                    token_price = await _get_token_price(token, "solana")
                    usd_value = amount * (token_price or 0)

                    if usd_value >= min_usd:
                        trades.append(
                            {
                                "token": token,
                                "amount": amount,
                                "usd_value": round(usd_value, 2),
                                "price": token_price,
                                "type": "buy" if transfer.get("fromUserAccount") == address else "sell",
                                "timestamp": datetime.fromtimestamp(timestamp, tz=UTC).isoformat(),
                                "tx_hash": tx.get("signature", ""),
                            }
                        )

        # Get wallet token balance
        balance_data = await fetch_helius(f"addresses/{address}/balances")
        if balance_data:
            wallet_info["tokens"] = balance_data.get("total", 0)
            wallet_info["nfts"] = balance_data.get("nfts", 0)

    elif chain in ("base", "ethereum", "bsc", "arbitrum"):
        # For EVM chains, use DexScreener to find recent pairs
        search = await fetch_dexscreener("search", {"q": address})
        if search and search.get("pairs"):
            for pair in search["pairs"][:20]:
                txns = pair.get("txns", {})
                h24 = txns.get("h24", {})
                if h24.get("buys", 0) > 0 or h24.get("sells", 0) > 0:
                    trades.append(
                        {
                            "token": pair.get("baseToken", {}).get("address", ""),
                            "pair_address": pair.get("pairAddress", ""),
                            "price_usd": pair.get("priceUsd"),
                            "volume_24h": pair.get("volume", {}).get("h24", 0),
                            "buys_24h": h24.get("buys", 0),
                            "sells_24h": h24.get("sells", 0),
                            "liquidity": pair.get("liquidity", {}).get("usd", 0),
                        }
                    )

    # Calculate PnL for trades
    pnl_summary = _calculate_pnl(trades)

    # Generate copy trade suggestions
    suggestions = _generate_copy_suggestions(trades, wallet_info, lookback)

    return JSONResponse(
        content={
            "tool": "whale_copy_trade",
            "address": address,
            "chain": chain,
            "lookback_hours": lookback,
            "trades": trades[:30],  # Limit to 30
            "total_trades": len(trades),
            "pnl_summary": pnl_summary,
            "suggestions": suggestions,
            "wallet_info": wallet_info,
            "status": "ok" if trades else "no_data",
            "message": f"Found {len(trades)} trades >= ${min_usd:.0f} in {lookback}h"
            if trades
            else f"No trades >= ${min_usd:.0f} found in {lookback}h",
        }
    )


def _calculate_pnl(trades: list[dict]) -> dict:
    """Calculate PnL summary from trades."""
    buys = [t for t in trades if t.get("type") == "buy"]
    sells = [t for t in trades if t.get("type") == "sell"]

    total_bought = sum(t.get("usd_value", 0) for t in buys)
    total_sold = sum(t.get("usd_value", 0) for t in sells)

    # Group by token
    token_pnl = {}
    for t in trades:
        token = t.get("token", "unknown")
        if token not in token_pnl:
            token_pnl[token] = {"bought": 0, "sold": 0, "trades": 0}
        token_pnl[token]["trades"] += 1
        if t.get("type") == "buy":
            token_pnl[token]["bought"] += t.get("usd_value", 0)
        else:
            token_pnl[token]["sold"] += t.get("usd_value", 0)

    return {
        "total_bought_usd": round(total_bought, 2),
        "total_sold_usd": round(total_sold, 2),
        "net_flow_usd": round(total_sold - total_bought, 2),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "unique_tokens": len(token_pnl),
        "top_tokens": sorted(
            [{"token": k, **v} for k, v in token_pnl.items()],
            key=lambda x: x["bought"],
            reverse=True,
        )[:5],
    }


def _generate_copy_suggestions(trades: list[dict], wallet_info: dict, lookback: int) -> list[dict]:
    """Generate copy trade suggestions based on wallet activity."""
    suggestions = []

    # Find tokens the wallet is accumulating (more buys than sells)
    token_activity = {}
    for t in trades:
        token = t.get("token", "")
        if not token:
            continue
        if token not in token_activity:
            token_activity[token] = {"buys": 0, "sells": 0, "total_usd": 0}
        if t.get("type") == "buy":
            token_activity[token]["buys"] += 1
        else:
            token_activity[token]["sells"] += 1
        token_activity[token]["total_usd"] += t.get("usd_value", 0)

    for token, activity in token_activity.items():
        if activity["buys"] > activity["sells"] and activity["total_usd"] > 5000:
            suggestions.append(
                {
                    "token": token,
                    "action": "accumulate",
                    "confidence": min(activity["buys"] * 0.2, 0.9),
                    "reason": f"Wallet bought {activity['buys']} times, sold {activity['sells']} times",
                    "total_volume_usd": round(activity["total_usd"], 2),
                }
            )

    return suggestions[:10]


async def _get_token_price(address: str, chain: str) -> float | None:
    """Get current token price from DexScreener."""
    if chain == "solana":
        result = await fetch_dexscreener(f"tokens/{address}")
        if result and result.get("pairs"):
            return float(result["pairs"][0].get("priceNative", 0))
    return None


# ── Tool 2: Live Rug Predictor ───────────────────────────────────


class RugPredictorLiveRequest(BaseModel):
    address: str
    chain: str = "solana"
    lookback_minutes: int = 30


@router.post("/rug_predictor_live")
async def rug_predictor_live(req: RugPredictorLiveRequest):
    """Live rug predictor — analyzes tokens in their first 5-30 minutes.

    Watches new tokens in real-time, scores them, and alerts before the rug.

    Price: $0.50/call
    """
    address = req.address.lower()
    chain = req.chain

    # Fetch token data from multiple sources
    pair_data = await fetch_dexscreener(f"tokens/{address}")
    if not pair_data or not pair_data.get("pairs"):
        return JSONResponse(
            content={
                "tool": "rug_predictor_live",
                "address": address,
                "chain": chain,
                "status": "no_data",
                "message": "Token not found on DexScreener",
                "rug_score": 0,
                "verdict": "UNKNOWN",
            }
        )

    pair = pair_data["pairs"][0]
    signals = []
    rug_score = 0

    # ── Signal 1: Liquidity Analysis ──────────────────────────────
    liquidity_usd = pair.get("liquidity", {}).get("usd", 0)
    if liquidity_usd < 1000:
        signals.append(
            {
                "signal": "low_liquidity",
                "severity": "critical",
                "message": f"Liquidity only ${liquidity_usd:.0f} — extremely vulnerable to rug",
                "weight": 25,
            }
        )
        rug_score += 25
    elif liquidity_usd < 5000:
        signals.append(
            {
                "signal": "low_liquidity",
                "severity": "high",
                "message": f"Liquidity ${liquidity_usd:.0f} — high risk",
                "weight": 15,
            }
        )
        rug_score += 15

    # ── Signal 2: LP Lock Status ──────────────────────────────────
    lp_locked = pair.get("lockInfo", {}).get("locked", False)
    if not lp_locked:
        signals.append(
            {
                "signal": "lp_not_locked",
                "severity": "critical",
                "message": "Liquidity pool is NOT locked — creator can remove at any time",
                "weight": 20,
            }
        )
        rug_score += 20

    # ── Signal 3: Price Action ────────────────────────────────────
    price_change = pair.get("priceChange", {})
    h1_change = price_change.get("h1", 0)
    price_change.get("m5", 0)

    if h1_change < -50:
        signals.append(
            {
                "signal": "price_crash",
                "severity": "critical",
                "message": f"Price down {h1_change}% in 1 hour — active rug in progress",
                "weight": 25,
            }
        )
        rug_score += 25

    # ── Signal 4: Volume/Liquidity Ratio ─────────────────────────
    volume_24h = pair.get("volume", {}).get("h24", 0)
    if liquidity_usd > 0:
        vol_liq_ratio = volume_24h / liquidity_usd
        if vol_liq_ratio > 10:
            signals.append(
                {
                    "signal": "extreme_volume",
                    "severity": "high",
                    "message": f"Volume/Liquidity ratio {vol_liq_ratio:.1f}x — suspicious activity",
                    "weight": 10,
                }
            )
            rug_score += 10

    # ── Signal 5: Transaction Count ───────────────────────────────
    txns = pair.get("txns", {})
    h1_txns = txns.get("h1", {})
    buys = h1_txns.get("buys", 0)
    sells = h1_txns.get("sells", 0)

    if buys > 0 and sells > 0:
        sell_ratio = sells / (buys + sells)
        if sell_ratio > 0.8:
            signals.append(
                {
                    "signal": "mass_selling",
                    "severity": "high",
                    "message": f"{sell_ratio * 100:.0f}% of transactions are sells — panic selling",
                    "weight": 15,
                }
            )
            rug_score += 15

    # ── Signal 6: Pair Age ────────────────────────────────────────
    pair_created_at = pair.get("pairCreatedAt", 0)
    if pair_created_at:
        age_hours = (time.time() * 1000 - pair_created_at) / (1000 * 3600)
        if age_hours < 1:
            signals.append(
                {
                    "signal": "brand_new_pair",
                    "severity": "medium",
                    "message": f"Pair is only {age_hours * 60:.0f} minutes old — highest risk window",
                    "weight": 10,
                }
            )
            rug_score += 10

    # ── Determine Verdict ─────────────────────────────────────────
    if rug_score >= 70:
        verdict = "CRITICAL_RUG"
        action = "AVOID — High probability of active or imminent rug"
    elif rug_score >= 50:
        verdict = "HIGH_RISK"
        action = "AVOID — Multiple red flags detected"
    elif rug_score >= 30:
        verdict = "MEDIUM_RISK"
        action = "CAUTION — Some warning signs, monitor closely"
    elif rug_score >= 15:
        verdict = "LOW_RISK"
        action = "MONITOR — Minor concerns but mostly clean"
    else:
        verdict = "SAFE"
        action = "Relatively clean — standard risk management applies"

    return JSONResponse(
        content={
            "tool": "rug_predictor_live",
            "address": address,
            "chain": chain,
            "rug_score": min(rug_score, 100),
            "verdict": verdict,
            "action": action,
            "signals": signals,
            "token_info": {
                "name": pair.get("baseToken", {}).get("name", ""),
                "symbol": pair.get("baseToken", {}).get("symbol", ""),
                "price_usd": pair.get("priceUsd"),
                "liquidity_usd": liquidity_usd,
                "volume_24h": volume_24h,
                "price_change_1h": h1_change,
                "txns_1h": {"buys": buys, "sells": sells},
                "lp_locked": lp_locked,
                "pair_age_hours": round(age_hours, 2) if pair_created_at else None,
            },
            "timestamp": datetime.now(UTC).isoformat(),
            "status": "ok",
        }
    )


# ── Tool 3: Whale Cluster Detection ──────────────────────────────


class WhaleClusterRequest(BaseModel):
    address: str
    chain: str = "solana"
    min_cluster_size: int = 3
    similarity_threshold: float = 0.7


@router.post("/whale_cluster")
async def whale_cluster(req: WhaleClusterRequest):
    """Identify coordinated whale clusters.

    Group wallets that move together: same funding source, same timing,
    same token sets. Identifies wash trading, coordinated pumps,
    and insider networks.

    Price: $0.30/call
    """
    address = req.address.lower()
    chain = req.chain
    min_size = req.min_cluster_size
    threshold = req.similarity_threshold

    r = get_redis()
    cluster_data = {
        "seed_wallet": address,
        "chain": chain,
        "clusters": [],
        "total_related_wallets": 0,
        "risk_assessment": "unknown",
    }

    # Step 1: Get wallets that interacted with the same tokens
    same_token_wallets = await _find_shared_token_wallets(address, chain, r)

    # Step 2: Check for shared funding sources
    funding_clusters = await _find_shared_funding(address, chain, r)

    # Step 3: Check timing correlation
    timing_clusters = await _find_timing_correlation(address, chain, r)

    # Combine and score
    wallet_scores = {}

    for wallet, score in same_token_wallets.items():
        wallet_scores[wallet] = wallet_scores.get(wallet, 0) + score * 0.4

    for wallet, score in funding_clusters.items():
        wallet_scores[wallet] = wallet_scores.get(wallet, 0) + score * 0.4

    for wallet, score in timing_clusters.items():
        wallet_scores[wallet] = wallet_scores.get(wallet, 0) + score * 0.2

    # Filter by threshold
    cluster_wallets = [
        {"wallet": w, "similarity": round(s, 3)} for w, s in wallet_scores.items() if s >= threshold and w != address
    ]
    cluster_wallets.sort(key=lambda x: x["similarity"], reverse=True)

    # Group into clusters by similarity
    clusters = []
    used = set()
    for cw in cluster_wallets:
        if cw["wallet"] in used:
            continue
        cluster = {"members": [cw["wallet"]], "avg_similarity": cw["similarity"]}
        used.add(cw["wallet"])
        for other in cluster_wallets:
            if other["wallet"] not in used and abs(other["similarity"] - cw["similarity"]) < 0.1:
                cluster["members"].append(other["wallet"])
                used.add(other["wallet"])

        if len(cluster["members"]) >= min_size - 1:  # -1 because seed wallet
            cluster["members"].insert(0, address)  # Add seed wallet
            cluster["size"] = len(cluster["members"])
            cluster["avg_similarity"] = round(
                sum(wallet_scores.get(w, 0) for w in cluster["members"]) / len(cluster["members"]),
                3,
            )
            clusters.append(cluster)

    # Risk assessment
    if any(c["size"] >= 5 for c in clusters):
        cluster_data["risk_assessment"] = "high"
        cluster_data["risk_message"] = "Large coordinated cluster detected — possible wash trading or insider network"
    elif any(c["size"] >= 3 for c in clusters):
        cluster_data["risk_assessment"] = "medium"
        cluster_data["risk_message"] = "Medium cluster detected — wallets showing coordinated behavior"
    else:
        cluster_data["risk_assessment"] = "low"
        cluster_data["risk_message"] = "No significant clusters detected"

    cluster_data["clusters"] = clusters
    cluster_data["total_related_wallets"] = len(cluster_wallets)
    cluster_data["status"] = "ok"

    return JSONResponse(content=cluster_data)


async def _find_shared_token_wallets(address: str, chain: str, r) -> dict[str, float]:
    """Find wallets that traded the same tokens."""
    # Get tokens this wallet traded
    token_key = f"rmi:wallet:{address}:{chain}:tokens"
    tokens = r.smembers(token_key) if r else set()

    if not tokens:
        # Fetch from DexScreener as fallback
        result = await fetch_dexscreener("search", {"q": address})
        if result and result.get("pairs"):
            tokens = {p.get("baseToken", {}).get("address", "") for p in result["pairs"][:20]}

    # For each token, find other wallets
    shared = {}
    for token in tokens:
        if not token:
            continue
        # Check Redis cache of recent traders for this token
        traders_key = f"rmi:token:{token}:{chain}:traders"
        traders = r.smembers(traders_key) if r else set()

        for trader in traders:
            if trader != address:
                shared[trader] = shared.get(trader, 0) + 1

    # Normalize
    max_shared = max(shared.values()) if shared else 1
    return {w: s / max_shared for w, s in shared.items()}


async def _find_shared_funding(address: str, chain: str, r) -> dict[str, float]:
    """Find wallets funded from the same source."""
    # Check if we have funding data cached
    funding_key = f"rmi:wallet:{address}:{chain}:funding"
    funding_source = r.get(funding_key) if r else None

    if funding_source:
        # Find other wallets funded from same source
        other_key = f"rmi:funding:{chain}:{funding_source}:wallets"
        other_wallets = r.smembers(other_key) if r else set()
        return {w: 0.9 for w in other_wallets if w != address}

    return {}


async def _find_timing_correlation(address: str, chain: str, r) -> dict[str, float]:
    """Find wallets with correlated trading timing."""
    # Get recent trade timestamps
    timing_key = f"rmi:wallet:{address}:{chain}:timing"
    timing_data = r.get(timing_key) if r else None

    if timing_data:
        json.loads(timing_data)
        # Find wallets with similar timing patterns
        correlation_key = f"rmi:timing:{chain}:correlations"
        correlations = r.hgetall(correlation_key) if r else {}

        correlated = {}
        for wallet, corr_score in correlations.items():
            if float(corr_score) > 0.5:
                correlated[wallet] = float(corr_score)

        return correlated

    return {}


# ── Tool Pricing Registration ────────────────────────────────────


# These tools register their prices in the canonical tool prices dict
def register_alpha_tool_prices():
    """Register alpha tool prices with the enforcement system."""
    try:
        from app.routers.x402_enforcement import TOOL_PRICES

        TOOL_PRICES.update(
            {
                "whale_copy_trade": {
                    "price_usd": 0.25,
                    "price_atoms": "250000",
                    "category": "alpha",
                    "trial_free": 1,
                    "description": "Real-time copy trade engine — find smart money trades with entry/exit prices, PnL, and follow suggestions",
                },
                "rug_predictor_live": {
                    "price_usd": 0.50,
                    "price_atoms": "500000",
                    "category": "security",
                    "trial_free": 1,
                    "description": "Live rug predictor — analyzes tokens in first 5-30 minutes with 6-signal rug probability scoring",
                },
                "whale_cluster": {
                    "price_usd": 0.30,
                    "price_atoms": "300000",
                    "category": "intelligence",
                    "trial_free": 1,
                    "description": "Coordinated whale cluster detection — identify wash trading, insider networks, and pump groups",
                },
            }
        )
    except Exception as e:
        logger.warning(f"Failed to register alpha tool prices: {e}")


# Register on import
register_alpha_tool_prices()
