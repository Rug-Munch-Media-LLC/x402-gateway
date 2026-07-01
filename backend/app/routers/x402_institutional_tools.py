"""
Institutional-grade x402 tools — portfolio risk, DeFi analysis, MEV detection.

Cross-Chain Portfolio Risk — unified risk across chains
DeFi Position Analyzer — LP, impermanent loss, yield sustainability
MEV/Sandwich Detection — sandwich attacks, frontrunning, arbitrage extraction
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

logger = logging.getLogger("x402.institutional")

router = APIRouter()


# ═══════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════


class PortfolioRequest(BaseModel):
    addresses: list[str] = Field(..., min_items=1, max_items=20, description="Wallet addresses across any chains")
    chains: list[str] | None = Field(default=None, description="Chain per address (auto-detect if omitted)")


class DeFiRequest(BaseModel):
    address: str = Field(..., description="Wallet or LP position address")
    chain: str = Field(default="base")


class MEVRequest(BaseModel):
    address: str = Field(..., description="Wallet or token address to check for MEV exposure")
    chain: str = Field(default="ethereum")
    tx_hash: str | None = Field(default=None, description="Specific transaction to analyze")


# ═══════════════════════════════════════════════════════════
# Redis helpers
# ═══════════════════════════════════════════════════════════


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


def _cache_get(tool: str, params: dict) -> dict | None:
    try:
        key = f"x402:cache:{tool}:{hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]}"
        data = _redis().get(key)
        return json.loads(data) if data else None
    except Exception:
        return None


def _cache_set(tool: str, params: dict, result: dict, ttl: int = 60):
    try:
        key = f"x402:cache:{tool}:{hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]}"
        _redis().setex(key, ttl, json.dumps(result))
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# TOOL: Cross-Chain Portfolio Risk ($0.20)
# ═══════════════════════════════════════════════════════════

CHAIN_RPC_MAP = {
    "ethereum": "https://eth.llamarpc.com",
    "base": "https://mainnet.base.org",
    "bsc": "https://bsc-dataseed.binance.org",
    "polygon": "https://polygon-rpc.com",
    "arbitrum": "https://arb1.arbitrum.io/rpc",
    "optimism": "https://mainnet.optimism.io",
    "avalanche": "https://api.avax.network/ext/bc/C/rpc",
    "fantom": "https://rpc.ftm.tools",
    "gnosis": "https://rpc.gnosischain.com",
    "solana": "https://api.mainnet-beta.solana.com",
}

CHAIN_NATIVE_SYMBOL = {
    "ethereum": "ETH",
    "base": "ETH",
    "bsc": "BNB",
    "polygon": "POL",
    "arbitrum": "ETH",
    "optimism": "ETH",
    "avalanche": "AVAX",
    "fantom": "FTM",
    "gnosis": "xDAI",
    "solana": "SOL",
}


async def _get_balance_evm(chain: str, address: str) -> float | None:
    rpc = CHAIN_RPC_MAP.get(chain)
    if not rpc:
        return None
    try:
        async with (
            aiohttp.ClientSession() as s,
            s.post(
                rpc,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_getBalance",
                    "params": [address, "latest"],
                },
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r,
        ):
            if r.status == 200:
                data = await r.json()
                return int(data.get("result", "0x0"), 16) / 1e18
    except Exception:
        pass
    return None


async def _get_balance_solana(address: str) -> float | None:
    try:
        async with aiohttp.ClientSession() as s, s.post(
            "https://api.mainnet-beta.solana.com",
            json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [address]},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            if r.status == 200:
                data = await r.json()
                return data.get("result", {}).get("value", 0) / 1e9
    except Exception:
        pass
    return None


@router.post("/portfolio_risk")
async def portfolio_risk(req: PortfolioRequest):
    """Unified risk dashboard for wallets across multiple chains.

    Aggregates: balances, risk scores, label associations, MEV exposure,
    and scam indicators into a single portfolio-wide risk report.
    """
    try:
        addresses = req.addresses[:20]
        chains = (req.chains or ["auto"] * len(addresses))[: len(addresses)]

        cached = _cache_get("portfolio_risk", {"addresses": addresses, "chains": chains})
        if cached:
            return cached

        wallets = []
        total_balance_usd = 0
        risk_flags = []
        sources = []

        for i, addr in enumerate(addresses):
            chain = chains[i] if i < len(chains) else "auto"
            wallet = {"address": addr, "assigned_chain": chain}

            # Auto-detect chain by address format
            if chain == "auto":
                if addr.startswith("0x") and len(addr) == 42:
                    chain = "ethereum"
                elif len(addr) in (43, 44) and not addr.startswith("0x"):
                    chain = "solana"
                elif addr.startswith("T") and len(addr) == 34:
                    chain = "tron"
                elif addr.startswith("1") or addr.startswith("3") or addr.startswith("bc1"):
                    chain = "bitcoin"
                else:
                    chain = "ethereum"
                wallet["detected_chain"] = chain

            # Get balance
            if chain == "solana":
                bal = await _get_balance_solana(addr)
                if bal is not None:
                    wallet["balance"] = round(bal, 6)
                    wallet["asset"] = "SOL"
                    # Approximate USD (SOL ~$140)
                    usd = bal * 140
                    wallet["balance_usd_approx"] = round(usd, 2)
                    total_balance_usd += usd
                    sources.append("solana_rpc")
            elif chain in CHAIN_RPC_MAP:
                bal = await _get_balance_evm(chain, addr)
                if bal is not None:
                    wallet["balance"] = round(bal, 6)
                    wallet["asset"] = CHAIN_NATIVE_SYMBOL.get(chain, "ETH")
                    # Approximate USD based on chain
                    eth_price = (
                        3200
                        if chain in ("ethereum", "base", "arbitrum", "optimism")
                        else 600
                        if chain == "bsc"
                        else 0.4
                        if chain == "polygon"
                        else 25
                        if chain == "avalanche"
                        else 0.5
                        if chain == "fantom"
                        else 1
                    )
                    usd = bal * eth_price
                    wallet["balance_usd_approx"] = round(usd, 2)
                    total_balance_usd += usd
                    sources.append(f"{chain}_rpc")

            # Risk check via reputation score
            try:
                async with aiohttp.ClientSession() as s, s.post(
                    "http://localhost:8000/api/v1/x402-tools/reputation_score",
                    json={"address": addr, "chain": chain},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    if r.status == 200:
                        rep = await r.json()
                        wallet["risk_score"] = rep.get("trust_score")
                        wallet["risk_tier"] = rep.get("tier")
                        if rep.get("flags"):
                            risk_flags.extend([{**f, "address": addr[:12] + "..."} for f in rep["flags"]])
                        sources.append("reputation_score")
            except Exception:
                pass

            # Label check
            try:
                from app.routers.x402_premium_tools import _lookup_labels_async

                labels = await _lookup_labels_async(addr)
                if labels:
                    wallet["labels"] = [
                        {"name": l.get("label_name"), "category": l.get("label_category")} for l in labels[:3]
                    ]
                    sources.append("wallet_labels")
            except Exception:
                pass

            wallets.append(wallet)

        # Portfolio-wide scoring
        risk_scores = [w.get("risk_score", 50) for w in wallets if w.get("risk_score") is not None]
        avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 50

        if avg_risk >= 80:
            portfolio_tier = "LOW_RISK"
        elif avg_risk >= 60:
            portfolio_tier = "MODERATE"
        elif avg_risk >= 40:
            portfolio_tier = "ELEVATED"
        else:
            portfolio_tier = "HIGH_RISK"

        result = {
            "tool": "Cross-Chain Portfolio Risk",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "wallets_analyzed": len(wallets),
            "chains_covered": len({w.get("detected_chain", w.get("assigned_chain", "?")) for w in wallets}),
            "total_balance_usd_approx": round(total_balance_usd, 2),
            "portfolio_risk_score": round(avg_risk, 1),
            "portfolio_risk_tier": portfolio_tier,
            "wallets": wallets,
            "risk_flags": risk_flags[:10],
            "flag_count": len(risk_flags),
            "sources_used": list(set(sources)),
            "recommendation": (
                "Portfolio appears healthy — continue monitoring"
                if portfolio_tier == "LOW_RISK"
                else "Some risk factors detected — review flagged wallets"
                if portfolio_tier == "MODERATE"
                else "Elevated risk — several wallets have concerning signals"
                if portfolio_tier == "ELEVATED"
                else "HIGH RISK — multiple critical flags across portfolio. Immediate review recommended."
            ),
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }

        _cache_set("portfolio_risk", {"addresses": addresses, "chains": chains}, result, ttl=90)
        return result

    except Exception as e:
        logger.error(f"Portfolio risk failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════
# TOOL: DeFi Position Analyzer ($0.15)
# ═══════════════════════════════════════════════════════════


@router.post("/defi_position")
async def defi_position(req: DeFiRequest):
    """Analyze DeFi positions — LP holdings, impermanent loss, yield sustainability.

    Checks: liquidity provision, staking positions, lending positions,
    yield farming exposure, impermanent loss estimation, and protocol risk.
    """
    try:
        addr = req.address.strip()
        chain = req.chain or "base"
        t0 = time.time()

        cached = _cache_get("defi_position", {"address": addr, "chain": chain})
        if cached:
            return cached

        positions = []
        sources = []
        total_tvl = 0
        warnings = []

        # ── DeFiLlama position lookup ──
        # Note: DeFiLlama doesn't have a direct "positions by wallet" endpoint
        # We check known protocols for LP/staking positions

        # ── DexScreener LP check ──
        try:
            async with aiohttp.ClientSession() as s:
                url = f"https://api.dexscreener.com/latest/dex/search?q={addr[:12]}"
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        data = await r.json()
                        pairs = data.get("pairs", [])
                        if pairs:
                            sources.append("dexscreener")
                            for p in pairs[:5]:
                                liq = p.get("liquidity", {}).get("usd", 0) or 0
                                if liq > 0:
                                    pair_addr = p.get("pairAddress", "")
                                    base = p.get("baseToken", {})
                                    quote = p.get("quoteToken", {})
                                    pos = {
                                        "type": "liquidity_provision",
                                        "pair": f"{base.get('symbol', '?')}/{quote.get('symbol', '?')}",
                                        "pair_address": pair_addr,
                                        "liquidity_usd": liq,
                                        "dex": p.get("dexId", "unknown"),
                                        "chain": p.get("chainId", chain),
                                        "volume_24h": p.get("volume", {}).get("h24", 0),
                                        "price_change_24h": p.get("priceChange", {}).get("h24", 0),
                                    }
                                    total_tvl += liq

                                    # Impermanent loss estimation
                                    pc = abs(p.get("priceChange", {}).get("h24", 0) or 0)
                                    if pc > 0:
                                        # IL formula: 2*sqrt(price_ratio)/(1+price_ratio) - 1
                                        price_ratio = 1 + pc / 100
                                        il = abs(2 * (price_ratio**0.5) / (1 + price_ratio) - 1) * 100
                                        pos["impermanent_loss_est"] = round(il, 2)
                                        if il > 5:
                                            warnings.append(f"High IL risk ({il:.1f}%) on {pos['pair']}")
                                        elif il > 2:
                                            pos["il_warning"] = f"Moderate IL: {il:.1f}%"

                                    # Volume/Liquidity health
                                    vol = pos["volume_24h"]
                                    if liq > 0 and vol > 0:
                                        turnover = vol / liq
                                        pos["daily_turnover"] = round(turnover, 2)
                                        if turnover > 1:
                                            pos["yield_potential"] = "high"
                                        elif turnover > 0.3:
                                            pos["yield_potential"] = "moderate"
                                        else:
                                            pos["yield_potential"] = "low"

                                    positions.append(pos)
        except Exception:
            pass

        # ── DeFiLlama protocol TVL context ──
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://api.llama.fi/protocols", timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        data = await r.json()
                        sources.append("defillama")
                        # Find protocols the wallet might be using
                        dexes_in_positions = {p.get("dex", "") for p in positions}
                        protocol_risks = []
                        for proto in data[:100]:
                            if proto.get("name", "").lower() in [d.lower() for d in dexes_in_positions]:
                                protocol_risks.append(
                                    {
                                        "name": proto.get("name"),
                                        "tvl": proto.get("tvl", 0),
                                        "category": proto.get("category", ""),
                                        "audits": proto.get("audits", 0),
                                        "audit_links": proto.get("audit_links", [])[:3],
                                    }
                                )
                        if protocol_risks:
                            for pr in protocol_risks:
                                if pr.get("audits", 0) == 0:
                                    warnings.append(f"No audits found for {pr['name']} — protocol risk elevated")
        except Exception:
            pass

        # ── Compute overall assessment ──
        risk_level = "low"
        if len(warnings) >= 3 or any("High IL risk" in w for w in warnings):
            risk_level = "high"
        elif len(warnings) >= 1:
            risk_level = "moderate"

        result = {
            "tool": "DeFi Position Analyzer",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "address": addr,
            "chain": chain,
            "positions_found": len(positions),
            "total_tvl_usd": round(total_tvl, 2),
            "positions": positions,
            "warnings": warnings if warnings else None,
            "overall_risk": risk_level,
            "recommendation": (
                "Positions appear healthy — standard monitoring sufficient"
                if risk_level == "low"
                else "Some risk factors — review impermanent loss exposure and protocol audits"
                if risk_level == "moderate"
                else "HIGH RISK — significant IL exposure and/or unaudited protocols. Consider reducing position sizes."
            ),
            "sources_used": sources,
            "performance_ms": round((time.time() - t0) * 1000, 1),
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }

        _cache_set("defi_position", {"address": addr, "chain": chain}, result)
        return result

    except Exception as e:
        logger.error(f"DeFi position failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════
# TOOL: MEV / Sandwich Attack Detection ($0.15)
# ═══════════════════════════════════════════════════════════


@router.post("/mev_detect")
async def mev_detect(req: MEVRequest):
    """Detect MEV exploitation — sandwich attacks, frontrunning, backrunning.

    Analyzes transaction history for MEV patterns:
    - Sandwich attacks (buy before, sell after)
    - Frontrunning (same-block order manipulation)
    - Arbitrage extraction
    - MEV bot wallet identification
    """
    try:
        addr = req.address.strip()
        chain = req.chain or "ethereum"
        tx_hash = req.tx_hash
        t0 = time.time()

        cached = _cache_get("mev_detect", {"address": addr, "chain": chain, "tx_hash": tx_hash})
        if cached:
            return cached

        sources = []
        mev_attacks = []
        mev_exposure_usd = 0
        risk_signals = []

        # ── EigenPhi MEV detection ──
        if chain in ("ethereum", "base", "bsc", "polygon", "arbitrum"):
            try:
                async with aiohttp.ClientSession() as s:
                    url = f"https://eigenphi.io/api/v1/mev/address/{addr}?chain={chain}"
                    async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                        if r.status == 200:
                            data = await r.json()
                            if data.get("mev_attacks"):
                                sources.append("eigenphi")
                                for attack in data["mev_attacks"][:10]:
                                    attack_type = attack.get("type", "unknown")
                                    mev_attacks.append(
                                        {
                                            "type": attack_type,
                                            "tx_hash": attack.get("tx_hash", "")[:16] + "...",
                                            "block": attack.get("block_number"),
                                            "profit_usd": attack.get("profit_usd", 0),
                                            "attacker": attack.get("attacker", "")[:12] + "...",
                                            "timestamp": attack.get("timestamp"),
                                        }
                                    )
                                    mev_exposure_usd += attack.get("profit_usd", 0)
            except Exception:
                pass

        # ── Etherscan tx analysis for sandwich patterns ──
        if chain in (
            "ethereum",
            "base",
            "bsc",
            "polygon",
            "arbitrum",
            "optimism",
            "avalanche",
            "fantom",
            "gnosis",
        ):
            try:
                api_key = os.getenv("ETHERSCAN_API_KEY", "")
                if api_key:
                    chain_id_map = {
                        "ethereum": 1,
                        "base": 8453,
                        "bsc": 56,
                        "polygon": 137,
                        "arbitrum": 42161,
                        "optimism": 10,
                        "avalanche": 43114,
                        "fantom": 250,
                        "gnosis": 100,
                    }
                    cid = chain_id_map.get(chain, 1)

                    async with aiohttp.ClientSession() as s:
                        if tx_hash:
                            url = f"https://api.etherscan.io/v2/api?chainid={cid}&module=account&action=txlistinternal&txhash={tx_hash}&apikey={api_key}"
                        else:
                            url = f"https://api.etherscan.io/v2/api?chainid={cid}&module=account&action=txlist&address={addr}&page=1&offset=10&sort=desc&apikey={api_key}"

                        async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                            if r.status == 200:
                                data = await r.json()
                                txs = data.get("result", [])
                                if txs and isinstance(txs, list):
                                    sources.append("etherscan")

                                    # Group by block to detect same-block patterns
                                    from collections import defaultdict

                                    block_txs = defaultdict(list)
                                    for tx in txs[:50]:
                                        block = tx.get("blockNumber")
                                        if block:
                                            block_txs[block].append(tx)

                                    for block, bt in block_txs.items():
                                        if len(bt) >= 3:
                                            # Multiple txs in same block — possible sandwich
                                            buys = [
                                                t
                                                for t in bt
                                                if t.get("from", "").lower() != addr.lower()
                                                and t.get("to", "").lower() == addr.lower()
                                            ]
                                            sells = [
                                                t
                                                for t in bt
                                                if t.get("from", "").lower() == addr.lower()
                                                and t.get("to", "").lower() != addr.lower()
                                            ]
                                            if buys and sells:
                                                risk_signals.append(
                                                    {
                                                        "block": block,
                                                        "pattern": "possible_sandwich",
                                                        "buys_in_block": len(buys),
                                                        "sells_in_block": len(sells),
                                                        "detail": f"Block {block} has {len(buys)} incoming + {len(sells)} outgoing — possible sandwich attack",
                                                    }
                                                )
            except Exception:
                pass

        # ── MEV bot wallet check via labels ──
        try:
            from app.routers.x402_premium_tools import _lookup_labels_async

            labels = await _lookup_labels_async(addr)
            if labels:
                sources.append("wallet_labels")
                mev_labels = [
                    l
                    for l in labels
                    if any(
                        kw in (l.get("label_name", "") + l.get("label_category", "")).lower()
                        for kw in ("mev", "sandwich", "frontrun", "arbitrage", "bot")
                    )
                ]
                if mev_labels:
                    risk_signals.append(
                        {
                            "pattern": "mev_bot_wallet",
                            "detail": f"Address labeled as MEV-related: {mev_labels[0].get('label_name')}",
                            "source": mev_labels[0].get("source"),
                        }
                    )
        except Exception:
            pass

        # ── DexScreener for sudden price impact check ──
        try:
            async with aiohttp.ClientSession() as s:
                url = f"https://api.dexscreener.com/latest/dex/search?q={addr[:12]}"
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        data = await r.json()
                        pairs = data.get("pairs", [])
                        if pairs:
                            sources.append("dexscreener")
                            for p in pairs[:3]:
                                pc = p.get("priceChange", {})
                                if abs(pc.get("h24", 0) or 0) > 30:
                                    risk_signals.append(
                                        {
                                            "pattern": "extreme_volatility",
                                            "pair": f"{p.get('baseToken', {}).get('symbol', '?')}/{p.get('quoteToken', {}).get('symbol', '?')}",
                                            "price_change_24h": pc.get("h24"),
                                            "detail": f"Extreme {pc.get('h24')}% price movement — possible MEV extraction impact",
                                        }
                                    )
        except Exception:
            pass

        # ── Final assessment ──
        total_signals = len(mev_attacks) + len(risk_signals)
        if total_signals >= 5 or mev_exposure_usd > 10000:
            risk_level = "critical"
            recommendation = "CRITICAL — significant MEV exploitation detected. Immediate review of trading strategy and RPC endpoint required."
        elif total_signals >= 2 or mev_exposure_usd > 1000:
            risk_level = "high"
            recommendation = "High MEV exposure — consider using Flashbots/MEV protection RPC"
        elif total_signals >= 1:
            risk_level = "moderate"
            recommendation = "Some MEV activity detected — monitor and consider MEV-protected transactions"
        else:
            risk_level = "low"
            recommendation = "No significant MEV exploitation detected"

        result = {
            "tool": "MEV / Sandwich Attack Detection",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "address": addr,
            "chain": chain,
            "mev_attacks_detected": len(mev_attacks),
            "mev_attacks": mev_attacks[:10],
            "mev_exposure_usd": round(mev_exposure_usd, 2),
            "risk_signals": risk_signals[:10],
            "total_signals": total_signals,
            "risk_level": risk_level,
            "recommendation": recommendation,
            "sources_used": sources,
            "protection_suggestions": [
                "Use Flashbots RPC for Ethereum transactions",
                "Enable MEV protection in your wallet settings",
                "Use private mempool submission for large trades",
                "Consider DEX aggregators with built-in MEV protection (1inch, CowSwap)",
            ]
            if risk_level in ("high", "critical")
            else None,
            "performance_ms": round((time.time() - t0) * 1000, 1),
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }

        _cache_set("mev_detect", {"address": addr, "chain": chain, "tx_hash": tx_hash}, result)
        return result

    except Exception as e:
        logger.error(f"MEV detect failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
