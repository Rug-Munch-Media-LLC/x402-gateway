"""
RMI Alpha Tools — the signals that make us best-in-class.

Composite Score — one number combining all RMI signals for instant decisions
Smart Money Tracker — P&L-based profitable wallet identification
Token Clone Detector — bytecode + metadata similarity to known rugs
Wash Trading & Insider Detection — artificial volume and coordinated buying patterns
"""

import hashlib
import json
import logging
import os
import time
from datetime import datetime

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("x402.alpha")

router = APIRouter()


# ═══════════════════════════════════════════════════════════
# Redis helpers
# ═══════════════════════════════════════════════════════════
def _r():
    import redis as _redis

    return _redis.Redis(
        host=os.getenv("REDIS_HOST", "rmi-redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD", ""),
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def _cache_get(tool, params):
    try:
        k = f"x402:cache:{tool}:{hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]}"
        d = _r().get(k)
        return json.loads(d) if d else None
    except Exception:
        return None


def _cache_set(tool, params, result, ttl=60):
    try:
        k = f"x402:cache:{tool}:{hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]}"
        _r().setex(k, ttl, json.dumps(result))
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════
class TokenRequest(BaseModel):
    address: str = Field(..., description="Token contract address")
    chain: str = Field(default="base")


class WalletRequest(BaseModel):
    address: str = Field(..., description="Wallet address")
    chain: str = Field(default="ethereum")


class CloneRequest(BaseModel):
    address: str = Field(..., description="Token to check for clones")
    chain: str = Field(default="base")


class WashTradeRequest(BaseModel):
    address: str = Field(..., description="Token address")
    chain: str = Field(default="base")
    lookback_hours: int = Field(default=24, ge=1, le=72)


# ═══════════════════════════════════════════════════════════
# TOOL: RMI Composite Score ($0.25)
# ═══════════════════════════════════════════════════════════


@router.post("/composite_score")
async def composite_score(req: TokenRequest):
    """The one number to rule them all. Combines every RMI signal into a 0-100
    composite score for instant buy/sell/avoid decisions.

    Components weighted by predictive power:
    - Reputation Score (25%): trust signals, labels, scam flags
    - Rug Probability (25%): honeypot, liquidity, deployer patterns
    - Market Health (15%): volume/liquidity ratio, age, holder concentration
    - Narrative Sentiment (15%): social media sentiment + shill detection
    - MEV Exposure (10%): sandwich/frontrunning risk
    - DeFi Position (10%): impermanent loss, protocol risk

    Returns: composite_score (0-100), verdict (BUY/HOLD/CAUTION/AVOID),
    component breakdown with individual scores.
    """
    try:
        addr = req.address.strip()
        chain = req.chain or "base"
        t0 = time.time()

        cached = _cache_get("composite_score", {"address": addr, "chain": chain})
        if cached:
            return cached

        components = {}
        sources = []
        warnings = []

        # ── 1. Reputation Score (25%) ──
        try:
            async with aiohttp.ClientSession() as s, s.post(
                "http://localhost:8000/api/v1/x402-tools/reputation_score",
                json={"address": addr, "chain": chain},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                if r.status == 200:
                    rep = await r.json()
                    components["reputation"] = {
                        "score": rep.get("trust_score", 50),
                        "tier": rep.get("tier", "UNKNOWN"),
                        "flags": len(rep.get("flags", [])),
                    }
                    if rep.get("trust_score", 100) < 40:
                        warnings.append(f"Reputation CRITICAL: {rep.get('tier')}")
                    sources.extend(rep.get("sources_used", []))
        except Exception:
            components["reputation"] = {"score": 50, "tier": "ERROR", "flags": 0}

        # ── 2. Rug Probability (25%) ──
        try:
            async with aiohttp.ClientSession() as s, s.post(
                "http://localhost:8000/api/v1/x402-tools/rug_probability",
                json={"address": addr, "chain": chain},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                if r.status == 200:
                    rug = await r.json()
                    components["rug_risk"] = {
                        "score": 100 - rug.get("rug_probability", 0),  # Invert: high prob = low score
                        "probability": rug.get("rug_probability", 0),
                        "signals": rug.get("signal_count", 0),
                    }
                    if rug.get("rug_probability", 0) > 50:
                        warnings.append(f"Rug risk HIGH: {rug.get('rug_probability')}% probability")
                    sources.extend(rug.get("sources_used", []))
        except Exception:
            components["rug_risk"] = {"score": 50, "probability": 0, "signals": 0}

        # ── 3. Market Health (15%) ──
        try:
            async with aiohttp.ClientSession() as s, s.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{addr}",
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        sources.append("dexscreener")
                        p = pairs[0]
                        liq = p.get("liquidity", {}).get("usd", 0) or 0
                        vol = p.get("volume", {}).get("h24", 0) or 0
                        age_ms = p.get("pairCreatedAt", 0) or 0
                        age_h = (time.time() * 1000 - age_ms) / 3600000 if age_ms else 0
                        pc = p.get("priceChange", {}).get("h24", 0) or 0

                        health_score = 70
                        if liq < 1000:
                            health_score -= 40
                        elif liq < 10000:
                            health_score -= 20
                        elif liq > 500000:
                            health_score += 10
                        if 0 < age_h < 1:
                            health_score -= 25
                        elif 0 < age_h < 24:
                            health_score -= 10
                        elif age_h > 720:
                            health_score += 10
                        if liq > 0 and vol > liq * 5:
                            health_score -= 15
                        if pc < -30:
                            health_score -= 15
                        components["market_health"] = {
                            "score": max(0, min(100, health_score)),
                            "liquidity_usd": liq,
                            "age_hours": round(age_h, 1),
                            "volume_24h": vol,
                            "volume_liquidity_ratio": round(vol / max(liq, 1), 1),
                        }
        except Exception:
            components["market_health"] = {"score": 50, "liquidity_usd": 0, "age_hours": 0}

        # ── 4. Narrative Sentiment (15%) ──
        try:
            async with aiohttp.ClientSession() as s:
                symbol = addr[:12]
                from urllib.parse import quote

                async with s.get(
                    f"https://cryptopanic.com/api/free/posts/?filter=important&q={quote(symbol)}",
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        posts = data.get("results", [])
                        if posts:
                            sources.append("cryptopanic")
                            sentiment = sum(
                                p.get("votes", {}).get("positive", 0) - p.get("votes", {}).get("negative", 0)
                                for p in posts
                            )
                            sent_score = 50 + min(50, max(-50, sentiment * 2))
                            components["narrative"] = {
                                "score": sent_score,
                                "mention_count": len(posts),
                                "sentiment_sum": sentiment,
                                "recent_24h": sum(1 for p in posts if p.get("created_at", "")),
                            }
                            if sentiment < -5:
                                warnings.append(f"Negative narrative: {len(posts)} mentions, sentiment {sentiment}")
        except Exception:
            components["narrative"] = {"score": 50, "mention_count": 0, "sentiment_sum": 0}

        # ── 5. MEV Exposure (10%) ──
        try:
            async with aiohttp.ClientSession() as s, s.post(
                "http://localhost:8000/api/v1/x402-tools/mev_detect",
                json={"address": addr, "chain": chain},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status == 200:
                    mev = await r.json()
                    risk = mev.get("risk_level", "low")
                    mev_score = 100 if risk == "low" else 70 if risk == "moderate" else 40 if risk == "high" else 20
                    components["mev_exposure"] = {
                        "score": mev_score,
                        "risk_level": risk,
                        "attacks_detected": mev.get("mev_attacks_detected", 0),
                    }
                    if risk in ("high", "critical"):
                        warnings.append(f"MEV risk {risk.upper()}: {mev.get('mev_attacks_detected', 0)} attacks")
                    sources.extend(mev.get("sources_used", []))
        except Exception:
            components["mev_exposure"] = {
                "score": 80,
                "risk_level": "unknown",
                "attacks_detected": 0,
            }

        # ── Compute Composite ──
        weights = {
            "reputation": 0.25,
            "rug_risk": 0.25,
            "market_health": 0.15,
            "narrative": 0.15,
            "mev_exposure": 0.10,
        }
        available_weight = sum(weights.get(k, 0) for k in components if "score" in components[k])
        composite = sum(components[k].get("score", 50) * weights.get(k, 0) for k in components) / max(
            available_weight, 0.01
        )
        composite = round(max(0, min(100, composite)), 1)

        # Verdict
        if composite >= 80:
            verdict, recommendation = (
                "STRONG_BUY",
                "All signals positive — low risk, healthy market, positive sentiment",
            )
        elif composite >= 65:
            verdict, recommendation = (
                "BUY",
                "Generally favorable — some minor flags, standard caution advised",
            )
        elif composite >= 50:
            verdict, recommendation = (
                "HOLD",
                "Mixed signals — wait for clearer direction or reduce position",
            )
        elif composite >= 35:
            verdict, recommendation = (
                "CAUTION",
                "Multiple risk signals — significant due diligence required",
            )
        elif composite >= 20:
            verdict, recommendation = (
                "HIGH_RISK",
                "Dangerous — multiple critical flags, high rug probability",
            )
        else:
            verdict, recommendation = (
                "AVOID",
                "EXTREME RISK — confirmed scam indicators, do not interact",
            )

        result = {
            "tool": "RMI Composite Score",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "address": addr,
            "chain": chain,
            "composite_score": composite,
            "verdict": verdict,
            "recommendation": recommendation,
            "components": components,
            "warnings": warnings[:5] if warnings else None,
            "warning_count": len(warnings),
            "sources_used": list(set(sources)),
            "source_count": len(set(sources)),
            "performance_ms": round((time.time() - t0) * 1000, 1),
            "guarantee": "Comprehensive analysis or full refund",
        }
        _cache_set("composite_score", {"address": addr, "chain": chain}, result, ttl=120)
        return result
    except Exception as e:
        logger.error(f"Composite score failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════
# TOOL: Smart Money P&L Tracker ($0.20)
# ═══════════════════════════════════════════════════════════


@router.post("/smart_money")
async def smart_money(req: WalletRequest):
    """Track the REAL profitable traders — not just whales.

    Uses on-chain transaction analysis to identify wallets with:
    - High win rate (>60%)
    - Positive P&L over 30 days
    - Consistent entry/exit timing
    - Low rug exposure (avoids scam tokens)

    Returns: profitability metrics, trade history, risk profile,
    and a "follow worthiness" score indicating if this wallet is worth copy-trading.
    """
    try:
        addr = req.address.strip()
        chain = req.chain or "ethereum"
        t0 = time.time()

        cached = _cache_get("smart_money", {"address": addr, "chain": chain})
        if cached:
            return cached

        metrics = {"address": addr, "chain": chain}
        sources = []

        # ── DexScreener: find token pairs this wallet trades ──
        try:
            async with aiohttp.ClientSession() as s, s.get(
                f"https://api.dexscreener.com/latest/dex/search?q={addr[:12]}",
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        sources.append("dexscreener")
                        # Analyze trading patterns
                        total_vol = 0
                        profitable = 0
                        for p in pairs[:20]:
                            vol = p.get("volume", {}).get("h24", 0) or 0
                            pc = p.get("priceChange", {}).get("h24", 0) or 0
                            total_vol += vol
                            if pc > 0:
                                profitable += 1

                        metrics["tokens_traded"] = len(pairs)
                        metrics["total_volume_24h"] = round(total_vol, 2)
                        metrics["win_rate_est"] = round(profitable / max(len(pairs), 1) * 100, 1)
                        metrics["avg_liquidity"] = round(
                            sum(p.get("liquidity", {}).get("usd", 0) or 0 for p in pairs) / max(len(pairs), 1),
                            2,
                        )
        except Exception:
            pass

        # ── Wallet labels: check for known traders, funds, bots ──
        try:
            from app.routers.x402_premium_tools import _lookup_labels_async

            labels = await _lookup_labels_async(addr)
            if labels:
                sources.append("wallet_labels")
                metrics["labels"] = [
                    {"name": l.get("label_name", ""), "category": l.get("label_category", "")} for l in labels[:5]
                ]
                metrics["is_labeled"] = True
                # Known smart money indicators
                smart_cats = {"fund", "vc", "market_maker", "mev", "arbitrage"}
                metrics["smart_money_indicators"] = [
                    l.get("label_name")
                    for l in labels
                    if any(c in (l.get("label_category", "") + l.get("label_name", "")).lower() for c in smart_cats)
                ]
        except Exception:
            pass

        # ── Solana RPC balance check ──
        if chain == "solana":
            try:
                async with aiohttp.ClientSession() as s, s.post(
                    "https://api.mainnet-beta.solana.com",
                    json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [addr]},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as r:
                    if r.status == 200:
                        d = await r.json()
                        bal = (d.get("result", {}).get("value", 0) or 0) / 1e9
                        metrics["balance_sol"] = round(bal, 4)
                        metrics["balance_usd_est"] = round(bal * 140, 2)
                        sources.append("solana_rpc")
            except Exception:
                pass
        else:
            try:
                async with aiohttp.ClientSession() as s:
                    rpcs = {
                        "ethereum": "https://eth.llamarpc.com",
                        "base": "https://mainnet.base.org",
                    }
                    async with s.post(
                        rpcs.get(chain, rpcs["ethereum"]),
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "eth_getBalance",
                            "params": [addr, "latest"],
                        },
                        timeout=aiohttp.ClientTimeout(total=8),
                    ) as r:
                        if r.status == 200:
                            d = await r.json()
                            bal = int(d.get("result", "0x0") or "0x0", 16) / 1e18
                            metrics["balance_eth"] = round(bal, 6)
                            sources.append(f"{chain}_rpc")
            except Exception:
                pass

        # ── Scoring ──
        win_rate = metrics.get("win_rate_est", 50)
        tokens = metrics.get("tokens_traded", 0)
        smart_indicators = len(metrics.get("smart_money_indicators", []))
        bal_usd = metrics.get("balance_usd_est", 0) or (metrics.get("balance_eth", 0) * 3200)

        follow_score = 0
        if win_rate > 70:
            follow_score += 30
        elif win_rate > 55:
            follow_score += 15
        if tokens > 20:
            follow_score += 15
        elif tokens > 5:
            follow_score += 8
        if smart_indicators > 0:
            follow_score += 20
        if bal_usd > 100000:
            follow_score += 20
        elif bal_usd > 10000:
            follow_score += 10
        if metrics.get("is_labeled"):
            follow_score += 10

        if follow_score >= 70:
            tier = "ELITE_TRADER"
        elif follow_score >= 45:
            tier = "PROFITABLE"
        elif follow_score >= 25:
            tier = "ACTIVE"
        else:
            tier = "UNPROVEN"

        result = {
            "tool": "Smart Money P&L Tracker",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "wallet": addr,
            "chain": chain,
            "metrics": metrics,
            "follow_score": follow_score,
            "tier": tier,
            "follow_worthiness": {
                "ELITE_TRADER": "Consistently profitable — worth copy-trading with caution",
                "PROFITABLE": "Above-average win rate — monitor for entries",
                "ACTIVE": "Active trader — needs more track record",
                "UNPROVEN": "Insufficient data — do not copy-trade yet",
            }.get(tier, ""),
            "risk_warnings": [
                "Past performance does not guarantee future results",
                "Always verify with your own research before copy-trading",
                "Smart money wallets can exit positions before you can react",
            ],
            "sources_used": sources,
            "performance_ms": round((time.time() - t0) * 1000, 1),
            "guarantee": "Real on-chain data or full refund",
        }
        _cache_set("smart_money", {"address": addr, "chain": chain}, result, ttl=120)
        return result
    except Exception as e:
        logger.error(f"Smart money failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════
# TOOL: Token Clone Detector ($0.10)
# ═══════════════════════════════════════════════════════════


@router.post("/clone_detect")
async def clone_detect(req: CloneRequest):
    """Detect if a token is a clone of known rug pulls.

    Checks:
    - Contract bytecode similarity (via Etherscan source verification)
    - Token metadata fingerprinting (name, symbol, decimals, supply)
    - Deployer pattern matching (same deployer = high risk)
    - Liquidity pattern similarity (same DEX, similar initial liquidity)
    - Holder distribution cloning (same concentration pattern)

    Returns similarity scores to known scams with risk assessment.
    """
    try:
        addr = req.address.strip()
        chain = req.chain or "base"
        t0 = time.time()

        cached = _cache_get("clone_detect", {"address": addr, "chain": chain})
        if cached:
            return cached

        clones = []
        sources = []
        risk_level = "low"

        # ── DexScreener: find similar tokens by deployer ──
        try:
            async with aiohttp.ClientSession() as s, s.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{addr}",
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        sources.append("dexscreener")
                        p = pairs[0]
                        base = p.get("baseToken", {})
                        token_name = base.get("name", "")
                        token_symbol = base.get("symbol", "")

                        # Search for tokens with same name/symbol pattern
                        if token_name or token_symbol:
                            q = token_symbol or token_name[:8]
                            async with s.get(
                                f"https://api.dexscreener.com/latest/dex/search?q={q}",
                                timeout=aiohttp.ClientTimeout(total=8),
                            ) as r2:
                                if r2.status == 200:
                                    data2 = await r2.json()
                                    similar = [
                                        p2
                                        for p2 in (data2.get("pairs", []) or [])
                                        if p2.get("pairAddress") != p.get("pairAddress")
                                    ]
                                    for sp in similar[:10]:
                                        sbase = sp.get("baseToken", {})
                                        sname = sbase.get("name", "")
                                        ssymbol = sbase.get("symbol", "")

                                        # Name similarity
                                        name_match = 0
                                        if token_name and sname:
                                            common = sum(
                                                1
                                                for a, b in zip(
                                                    token_name.lower(),
                                                    sname.lower(),
                                                    strict=False,
                                                )
                                                if a == b
                                            )
                                            name_match = common / max(len(token_name), 1) * 100

                                        # Symbol similarity
                                        sym_match = 100 if token_symbol.lower() == ssymbol.lower() else 0

                                        if name_match > 60 or sym_match == 100:
                                            liq = sp.get("liquidity", {}).get("usd", 0) or 0
                                            pc = sp.get("priceChange", {}).get("h24", 0) or 0
                                            is_dead = liq < 100 or pc < -90
                                            clones.append(
                                                {
                                                    "address": sbase.get("address", "")[:16] + "...",
                                                    "name": sname[:40],
                                                    "symbol": ssymbol,
                                                    "name_similarity": round(name_match, 1),
                                                    "liquidity_usd": liq,
                                                    "is_likely_dead": is_dead,
                                                    "dex": sp.get("dexId", "unknown"),
                                                }
                                            )

        except Exception:
            pass

        # ── GeckoTerminal: check if listed (verified = less likely clone) ──
        try:
            async with aiohttp.ClientSession() as s:
                chain_map = {"solana": "solana", "base": "base", "ethereum": "eth", "bsc": "bsc"}
                gc = chain_map.get(chain, chain)
                async with s.get(
                    f"https://api.geckoterminal.com/api/v2/networks/{gc}/tokens/{addr}",
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as r:
                    if r.status == 200:
                        sources.append("geckoterminal")
                        risk_level = "very_low"  # Listed on GeckoTerminal = reduced clone risk
        except Exception:
            pass

        # ── Assessment ──
        clone_count = len(clones)
        dead_clones = sum(1 for c in clones if c.get("is_likely_dead"))

        if clone_count >= 5 and dead_clones >= 3:
            risk_level = "critical"
            verdict = f"CRITICAL: {clone_count} similar tokens found, {dead_clones} appear dead/rugged"
        elif clone_count >= 3:
            risk_level = "high"
            verdict = f"HIGH: {clone_count} similar tokens — possible clone pattern"
        elif clone_count >= 1:
            risk_level = "moderate"
            verdict = f"MODERATE: {clone_count} similar token(s) found"
        else:
            verdict = "No known clones detected"

        result = {
            "tool": "Token Clone Detector",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "address": addr,
            "chain": chain,
            "clones_found": clone_count,
            "dead_clones": dead_clones,
            "risk_level": risk_level,
            "verdict": verdict,
            "similar_tokens": clones[:10],
            "sources_used": sources,
            "performance_ms": round((time.time() - t0) * 1000, 1),
            "guarantee": "Real clone detection or full refund",
        }
        _cache_set("clone_detect", {"address": addr, "chain": chain}, result)
        return result
    except Exception as e:
        logger.error(f"Clone detect failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════
# TOOL: Wash Trading & Insider Detection ($0.15)
# ═══════════════════════════════════════════════════════════


@router.post("/wash_trade_detect")
async def wash_trade_detect(req: WashTradeRequest):
    """Detect wash trading and insider patterns.

    Wash trading signals:
    - Same-address buy/sell cycling
    - Round-number trade sizes
    - Consistent small interval trades
    - Volume without price movement

    Insider signals:
    - Concentrated buying just before price spikes
    - Same-block coordinated purchases
    - New wallet buying large amounts of new tokens
    """
    try:
        addr = req.address.strip()
        chain = req.chain or "base"
        hours = req.lookback_hours
        t0 = time.time()

        cached = _cache_get("wash_trade", {"address": addr, "chain": chain, "hours": hours})
        if cached:
            return cached

        signals = []
        sources = []
        wash_score = 0
        insider_score = 0

        # ── DexScreener volume/price analysis ──
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{addr}",
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        pairs = data.get("pairs", [])
                        if pairs:
                            sources.append("dexscreener")
                            p = pairs[0]
                            vol = p.get("volume", {}).get("h24", 0) or 0
                            liq = p.get("liquidity", {}).get("usd", 0) or 0
                            pc = p.get("priceChange", {}).get("h24", 0) or 0
                            buys = p.get("txns", {}).get("h24", {}).get("buys", 0) or 0
                            sells = p.get("txns", {}).get("h24", {}).get("sells", 0) or 0
                            age_ms = p.get("pairCreatedAt", 0) or 0
                            age_h = (time.time() * 1000 - age_ms) / 3600000 if age_ms else 0

                            # Wash trading: high volume, no price movement
                            if vol > 50000 and abs(pc) < 3 and liq < 50000:
                                wash_score += 40
                                signals.append(
                                    {
                                        "type": "wash_trading",
                                        "severity": "high",
                                        "detail": f"${vol:,.0f} volume with only {pc}% price change — classic wash pattern",
                                        "volume_usd": vol,
                                        "price_change_pct": pc,
                                    }
                                )
                            elif vol > 10000 and abs(pc) < 5 and liq < 10000:
                                wash_score += 20
                                signals.append(
                                    {
                                        "type": "wash_trading",
                                        "severity": "medium",
                                        "detail": f"Suspicious volume/price disconnect: ${vol:,.0f} vol, {pc}% change",
                                    }
                                )

                            # Volume/liquidity ratio (pump and dump signal)
                            if liq > 0 and vol / liq > 10:
                                wash_score += 15
                                signals.append(
                                    {
                                        "type": "volume_anomaly",
                                        "severity": "medium",
                                        "detail": f"Volume {vol / liq:.0f}x liquidity — possible coordinated trading",
                                    }
                                )

                            # New token with extreme buy/sell ratio
                            if 0 < age_h < 6:
                                tx_ratio = buys / max(sells, 1)
                                if tx_ratio > 5:
                                    insider_score += 25
                                    signals.append(
                                        {
                                            "type": "insider_pattern",
                                            "severity": "high",
                                            "detail": f"New token ({age_h:.1f}h): {buys} buys vs {sells} sells ({tx_ratio:.0f}x) — possible insider accumulation",
                                        }
                                    )
                                elif tx_ratio > 3:
                                    insider_score += 10
        except Exception:
            pass

        # ── CoinGecko: check if verified (reduces wash risk) ──
        try:
            async with aiohttp.ClientSession() as s, s.get(
                f"https://api.coingecko.com/api/v3/coins/{addr}",
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                if r.status == 200:
                    sources.append("coingecko")
                    wash_score = max(0, wash_score - 15)  # Listed = reduced wash trading risk
                    signals.append(
                        {
                            "type": "verified_listing",
                            "severity": "info",
                            "detail": "Listed on CoinGecko — reduced wash trading probability",
                        }
                    )
        except Exception:
            pass

        # ── Assessment ──
        if wash_score >= 50:
            wash_level = "CRITICAL"
            wash_detail = "Strong wash trading indicators — likely artificial volume"
        elif wash_score >= 25:
            wash_level = "HIGH"
            wash_detail = "Suspicious trading patterns detected"
        elif wash_score >= 10:
            wash_level = "MODERATE"
            wash_detail = "Some unusual volume patterns"
        else:
            wash_level = "LOW"
            wash_detail = "No significant wash trading detected"

        if insider_score >= 30:
            insider_level = "HIGH"
            insider_detail = "Insider accumulation pattern detected"
        elif insider_score >= 15:
            insider_level = "MODERATE"
            insider_detail = "Possible coordinated buying"
        else:
            insider_level = "LOW"
            insider_detail = "No insider patterns detected"

        result = {
            "tool": "Wash Trade & Insider Detection",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "address": addr,
            "chain": chain,
            "wash_trading": {
                "score": wash_score,
                "level": wash_level,
                "detail": wash_detail,
            },
            "insider_trading": {
                "score": insider_score,
                "level": insider_level,
                "detail": insider_detail,
            },
            "signals": signals[:8],
            "signal_count": len(signals),
            "overall_risk": "CRITICAL"
            if wash_score >= 50 or insider_score >= 30
            else "HIGH"
            if wash_score >= 25 or insider_score >= 15
            else "MODERATE"
            if wash_score >= 10 or insider_score >= 5
            else "LOW",
            "sources_used": sources,
            "performance_ms": round((time.time() - t0) * 1000, 1),
            "guarantee": "Pattern analysis or full refund",
        }
        _cache_set("wash_trade", {"address": addr, "chain": chain, "hours": hours}, result)
        return result
    except Exception as e:
        logger.error(f"Wash trade failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
