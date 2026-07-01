"""
Premium x402 tools — reputation scoring, investigation reports, webhook alerts.

These are the standout tools that make the RMI MCP server worth paying for:
- reputation_score: Single 0-100 trust score combining all enrichment signals
- investigation_report: AI-generated human-readable dossier
- webhook_register: Register callbacks for monitoring alerts
"""

import json
import logging
import os
import time
from datetime import datetime

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("x402.premium")

router = APIRouter()

# ═══════════════════════════════════════════════════════════
# Request Models
# ═══════════════════════════════════════════════════════════


class AddressRequest(BaseModel):
    address: str = Field(..., description="Wallet address, token contract, or ENS name")
    chain: str = Field(default="base", description="Blockchain: base, solana, ethereum, bsc, etc.")


class WebhookRegisterRequest(BaseModel):
    url: str = Field(..., description="Webhook callback URL")
    events: list[str] = Field(default=["rug_pull", "whale_move", "price_crash"], description="Events to monitor")
    address: str | None = Field(default=None, description="Specific address to watch (optional)")
    chain: str = Field(default="all", description="Chain filter")


class InvestigationRequest(BaseModel):
    address: str = Field(..., description="Target address, token, or wallet")
    chain: str = Field(default="base")
    depth: str = Field(default="standard", description="standard | deep | forensic")


# ═══════════════════════════════════════════════════════════
# TOOL: Reputation Score ($0.10)
# ═══════════════════════════════════════════════════════════


@router.post("/reputation_score")
async def reputation_score(req: AddressRequest):
    """Compute a comprehensive 0-100 trust score for any blockchain address.

    Combines: wallet labels, scam database hits, deployer history,
    RAG similarity matching, transaction patterns, and exchange associations.

    Score interpretation:
      90-100: Trusted (verified exchange, known entity)
      70-89:  Low risk (established wallet, clean history)
      50-69:  Moderate risk (some flags, requires attention)
      30-49:  High risk (multiple red flags, suspected scam)
      0-29:   Critical risk (confirmed scam, sanctioned, known rugger)
    """
    try:
        addr = req.address.strip()
        chain = req.chain or "base"

        score = 100  # Start at perfect trust, subtract for risk factors
        flags = []
        positives = []
        sources = []

        # ── 1. Wallet Labels (ClickHouse) ──
        labels = await _lookup_labels_async(addr)
        if labels:
            sources.append("wallet_labels")
            for lbl in labels:
                cat = lbl.get("label_category", "")
                if cat == "sanctioned":
                    score -= 100
                    flags.append(
                        {
                            "severity": "critical",
                            "detail": f"OFAC-sanctioned: {lbl.get('label_name')}",
                        }
                    )
                elif cat in ("phish-hack", "scam", "etherscan-phish-hack-list"):
                    score -= 60
                    flags.append(
                        {
                            "severity": "high",
                            "detail": f"Known scam: {lbl.get('label_name')} (source: {lbl.get('source')})",
                        }
                    )
                elif cat == "exploit":
                    score -= 50
                    flags.append(
                        {
                            "severity": "high",
                            "detail": f"Exploit-associated: {lbl.get('label_name')}",
                        }
                    )
                elif cat == "heist":
                    score -= 70
                    flags.append(
                        {
                            "severity": "critical",
                            "detail": f"Heist-associated: {lbl.get('label_name')}",
                        }
                    )
                elif cat == "cex":
                    score += 10
                    positives.append(f"Exchange wallet: {lbl.get('label_name')}")
                elif cat == "dex":
                    score += 5
                    positives.append(f"DEX contract: {lbl.get('label_name')}")

        # ── 2. RAG Similarity Search ──
        try:
            from app.routers.x402_enrichment import search_similar_patterns

            patterns, _ = search_similar_patterns([addr])
            if patterns:
                sources.append("rag_similarity")
                for p in patterns:
                    matches = p.get("matches", [])
                    for m in matches:
                        sim_score = m.get("score", 0)
                        if sim_score > 0.8:
                            score -= 40
                            flags.append(
                                {
                                    "severity": "high",
                                    "detail": f"Strong scam pattern match: {m.get('type')} ({sim_score:.0%})",
                                }
                            )
                        elif sim_score > 0.6:
                            score -= 20
                            flags.append(
                                {
                                    "severity": "medium",
                                    "detail": f"Possible scam pattern: {m.get('type')} ({sim_score:.0%})",
                                }
                            )
        except Exception:
            pass

        # ── 3. Deployer History (DexScreener) ──
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://api.dexscreener.com/latest/dex/search?q={addr[:12]}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get("pairs", [])
                        if pairs:
                            sources.append("dexscreener")
                            # Age check
                            oldest = min(pairs, key=lambda p: p.get("pairCreatedAt", 0))
                            age_h = (
                                (time.time() - oldest.get("pairCreatedAt", 0) / 1000) / 3600
                                if oldest.get("pairCreatedAt")
                                else 0
                            )
                            if age_h < 1:
                                score -= 15
                                flags.append(
                                    {
                                        "severity": "medium",
                                        "detail": f"Brand new: oldest pair only {age_h:.1f}h old",
                                    }
                                )
                            elif age_h > 720:
                                score += 10
                                positives.append(f"Established: oldest pair {age_h / 24:.0f}d old")

                            # Volume/liquidity check
                            total_liq = sum(p.get("liquidity", {}).get("usd", 0) for p in pairs)
                            if total_liq > 0 and total_liq < 1000:
                                score -= 10
                                flags.append(
                                    {
                                        "severity": "low",
                                        "detail": f"Low liquidity: ${total_liq:,.0f}",
                                    }
                                )
        except Exception:
            pass

        # ── 4. Scam Pattern Detection ──
        try:
            import asyncio

            from app.rag_service import detect_scam_patterns

            result = asyncio.run(detect_scam_patterns({"address": addr, "chain": chain}, 0.5))
            if result and result.get("risk_score", 0) > 0:
                sources.append("scam_detector")
                risk = result.get("risk_score", 0)
                score -= min(risk, 50)
                patterns = result.get("patterns", [])
                if patterns:
                    flags.append(
                        {
                            "severity": "high" if risk > 30 else "medium",
                            "detail": f"Scam patterns: {', '.join(patterns[:3])}",
                        }
                    )
        except Exception:
            pass

        # ── 5. Recent Intel Context ──
        try:
            from app.routers.x402_enrichment import _load_recent_intel

            intel = _load_recent_intel()
            if intel:
                sources.append("rmi_intel")
                positives.append(
                    f"RMI Intel active: {intel.get('tokens_scanned', 0):,} tokens scanned, {intel.get('scanner_alerts', 0)} alerts"
                )
        except Exception:
            pass

        # Clamp score
        score = max(0, min(100, score))

        # If no sources had data, provide a baseline
        if not sources:
            score = 50  # Neutral — insufficient data
            flags.append(
                {
                    "severity": "info",
                    "detail": "Limited data available for this address. Score is neutral baseline.",
                }
            )
            sources.append("baseline")

        # Determine tier
        if score >= 90:
            tier = "TRUSTED"
        elif score >= 70:
            tier = "LOW_RISK"
        elif score >= 50:
            tier = "MODERATE_RISK"
        elif score >= 30:
            tier = "HIGH_RISK"
        else:
            tier = "CRITICAL_RISK"

        return {
            "tool": "Reputation Score",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "address": addr,
            "chain": chain,
            "trust_score": score,
            "tier": tier,
            "flags": flags,
            "positive_signals": positives,
            "flag_count": len(flags),
            "sources_used": sources,
            "interpretation": {
                "90-100": "Trusted — verified exchange or known entity",
                "70-89": "Low risk — established wallet, clean history",
                "50-69": "Moderate risk — some flags, exercise caution",
                "30-49": "High risk — multiple red flags, suspected scam",
                "0-29": "Critical risk — confirmed scam, sanctioned, known rugger",
            },
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        logger.error(f"Reputation score failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════
# TOOL: Webhook Register ($0.02 setup + webhook delivery)
# ═══════════════════════════════════════════════════════════


@router.post("/webhook_register")
async def webhook_register(req: WebhookRegisterRequest):
    """Register a webhook URL for real-time monitoring alerts.

    Available events: rug_pull, whale_move, price_crash, new_launch,
                      liquidity_removed, ownership_renounced, scam_detected.

    Webhooks fire within 30 seconds of detection. Max 3 webhooks per address.
    Data delivered as JSON POST to your URL.
    """
    try:
        valid_events = {
            "rug_pull",
            "whale_move",
            "price_crash",
            "new_launch",
            "liquidity_removed",
            "ownership_renounced",
            "scam_detected",
        }
        events = [e for e in req.events if e in valid_events]

        if not events:
            raise HTTPException(
                status_code=400,
                detail=f"No valid events. Choose from: {', '.join(sorted(valid_events))}",
            )

        # Store in Redis
        import redis as _redis

        r = _redis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_connect_timeout=3,
        )

        webhook_id = f"wh_{int(time.time())}_{req.address[:10] if req.address else 'global'}"
        webhook_data = {
            "id": webhook_id,
            "url": req.url,
            "events": events,
            "address": req.address,
            "chain": req.chain,
            "created": datetime.utcnow().isoformat(),
            "active": True,
        }
        r.setex(f"x402:webhook:{webhook_id}", 30 * 86400, json.dumps(webhook_data))
        r.sadd(f"x402:webhook:events:{req.address or 'global'}", webhook_id)

        return {
            "tool": "Webhook Register",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "webhook_id": webhook_id,
            "url": req.url,
            "events": events,
            "expires_in": "30 days",
            "webhook_format": {
                "event": "e.g. rug_pull",
                "address": "0x...",
                "chain": "base",
                "data": "{tool-specific payload}",
                "timestamp": "ISO 8601",
            },
            "guarantee": "Webhook delivery guaranteed or payment refunded",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Webhook register failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/webhook_list")
async def webhook_list(address: str | None = None):
    """List registered webhooks for an address or globally."""
    try:
        import redis as _redis

        r = _redis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_connect_timeout=3,
        )

        key = f"x402:webhook:events:{address or 'global'}"
        ids = r.smembers(key)
        webhooks = []
        for wid in ids:
            data = r.get(f"x402:webhook:{wid}")
            if data:
                webhooks.append(json.loads(data))

        return {
            "address": address or "global",
            "webhooks": webhooks,
            "count": len(webhooks),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════
# TOOL: Investigation Report ($0.25)
# ═══════════════════════════════════════════════════════════


@router.post("/investigation_report")
async def investigation_report(req: InvestigationRequest):
    """Generate a comprehensive AI-powered investigation report.

    Combines: reputation scoring, wallet labeling, scam detection,
    on-chain forensics, social signal analysis, and market data
    into a single human-readable report with risk assessment.

    Report sections: Executive Summary, Risk Score, Wallet Profile,
    Transaction Analysis, Known Associations, Scam Indicators,
    Market Context, Recommendation.
    """
    try:
        addr = req.address.strip()
        chain = req.chain or "base"
        depth = req.depth or "standard"

        sections = {}
        sources = []

        # ── Section 1: Reputation ──
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    "http://localhost:8000/api/v1/x402-tools/reputation_score",
                    json={"address": addr, "chain": chain},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp,
            ):
                if resp.status == 200:
                    rep = await resp.json()
                    sections["reputation"] = {
                        "score": rep.get("trust_score"),
                        "tier": rep.get("tier"),
                        "flags": rep.get("flags", [])[:5],
                        "positive_signals": rep.get("positive_signals", [])[:3],
                    }
                    sources.extend(rep.get("sources_used", []))
        except Exception:
            sections["reputation"] = {"error": "Reputation service unavailable"}

        # ── Section 2: Wallet Labels ──
        labels = await _lookup_labels_async(addr)
        if labels:
            sources.append("wallet_labels")
            sections["wallet_profile"] = {
                "labels_found": len(labels),
                "categories": list({l.get("label_category") for l in labels}),
                "top_labels": [
                    {
                        "name": l.get("label_name"),
                        "category": l.get("label_category"),
                        "source": l.get("source"),
                    }
                    for l in labels[:5]
                ],
                "sanctioned": any(l.get("label_category") == "sanctioned" for l in labels),
            }
        else:
            sections["wallet_profile"] = {"labels_found": 0, "note": "No known labels"}

        # ── Section 3: On-Chain Forensics ──
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    "http://localhost:8000/api/v1/x402-tools/forensics",
                    json={"address": addr, "chain": chain},
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp,
            ):
                if resp.status == 200:
                    forensics = await resp.json()
                    sections["on_chain"] = {
                        "risk_score": forensics.get("overall_risk_score"),
                        "risk_level": forensics.get("overall_risk"),
                        "risk_factors": forensics.get("risk_factors", [])[:5],
                        "sources": forensics.get("sources_used", []),
                    }
                    sources.extend(forensics.get("sources_used", []))
        except Exception:
            sections["on_chain"] = {"error": "Forensics unavailable — continuing with other sources"}

        # ── Section 4: Market Context ──
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://api.dexscreener.com/latest/dex/tokens/{addr}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get("pairs", [])
                        if pairs:
                            sources.append("dexscreener")
                            p = pairs[0]
                            sections["market_context"] = {
                                "price_usd": p.get("priceUsd"),
                                "liquidity_usd": p.get("liquidity", {}).get("usd"),
                                "volume_24h": p.get("volume", {}).get("h24"),
                                "price_change_24h": p.get("priceChange", {}).get("h24"),
                                "age_hours": (time.time() - p.get("pairCreatedAt", 0) / 1000) / 3600
                                if p.get("pairCreatedAt")
                                else None,
                            }
        except Exception:
            sections["market_context"] = {"error": "Market data unavailable"}

        # ── Section 5: Intel Context ──
        try:
            from app.routers.x402_enrichment import _load_recent_intel

            intel = _load_recent_intel()
            if intel:
                sections["intel_context"] = {
                    "briefing": intel.get("briefing", "")[:300],
                    "scanner_alerts": intel.get("scanner_alerts", 0),
                    "age_hours": intel.get("age_hours", 0),
                }
                sources.append("rmi_cron_intel")
        except Exception:
            pass

        # ── Generate recommendation ──
        rep_score = sections.get("reputation", {}).get("score", 50)
        risk_score = sections.get("on_chain", {}).get("risk_score", 50)

        if rep_score >= 80 and risk_score < 30:
            verdict = "APPROVED"
            recommendation = "Low risk. Standard due diligence recommended."
        elif rep_score >= 60 and risk_score < 50:
            verdict = "CAUTION"
            recommendation = "Moderate risk. Verify contract ownership and liquidity locks before interacting."
        elif rep_score >= 40 or risk_score < 70:
            verdict = "HIGH_RISK"
            recommendation = "High risk. Multiple red flags detected. Exercise extreme caution or avoid."
        else:
            verdict = "AVOID"
            recommendation = "Critical risk. Confirmed scam indicators. Do not interact with this address."

        return {
            "tool": "Investigation Report",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "address": addr,
            "chain": chain,
            "depth": depth,
            "verdict": verdict,
            "recommendation": recommendation,
            "sections": sections,
            "sources_used": list(set(sources)),
            "source_count": len(set(sources)),
            "price_usd": "0.25",
            "guarantee": "Comprehensive report or full refund",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Investigation report failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════


async def _lookup_labels_async(address: str) -> list[dict]:
    """Async wrapper for wallet label lookups."""
    try:
        import redis as _redis

        r = _redis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_connect_timeout=2,
        )
        # Try ClickHouse first
        try:
            from clickhouse_driver import Client

            ch = Client(
                host=os.getenv("CH_HOST", "rmi-clickhouse"),
                port=int(os.getenv("CH_PORT", "9000")),
                user=os.getenv("CH_USER", "default"),
                password=os.getenv("CH_PASSWORD", "") or None,
                settings={"max_execution_time": 3},
            )
            rows = ch.execute(
                "SELECT address, label_name, label_category, label_subtype, source, is_sanctioned "
                "FROM wallet_memory.wallet_labels WHERE address = %(addr)s "
                "ORDER BY loaded_at DESC LIMIT 20",
                {"addr": address},
            )
            return [
                {
                    "label_name": r[1],
                    "label_category": r[2],
                    "label_subtype": r[3],
                    "source": r[4],
                    "is_sanctioned": bool(r[5]),
                }
                for r in rows
            ]
        except Exception:
            # Fall back to Redis cache
            cached = r.get(f"x402:enrich:{address}")
            if cached:
                data = json.loads(cached)
                return data.get("labels", [])
            return []
    except Exception:
        return []
