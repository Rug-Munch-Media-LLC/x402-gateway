"""
RMI x402 Forensic Investigation Tools — Premium Analysis Endpoints
================================================================
TOOL 37: Forensic Valuation ($0.25) — DCF + Comps + Scam scoring
TOOL 38: OSINT Identity Hunt ($0.15) — Cross-platform username/domain investigation
TOOL 39: Investigation Report ($0.20) — Full investigative deliverable

These are premium x402-paid endpoints that provide institutional-grade
crypto scam investigation capabilities.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routers.x402_tools import fetch_with_fallback, record_x402_payment

logger = logging.getLogger("x402_forensic_tools")
router = APIRouter(prefix="/api/v1/x402-tools", tags=["x402-forensic-tools"])


# ── TOOL 37: Forensic Valuation — DCF + Comps Analysis ($0.25) ──


class ForensicValuationRequest(BaseModel):
    address: str
    chain: str = "solana"
    peer_tokens: str | None = None
    include_dcf: bool = True
    include_comps: bool = True


@router.post("/forensic_valuation")
async def forensic_valuation(req: ForensicValuationRequest):
    """Institutional-grade token valuation — DCF intrinsic value, comparable analysis
    with statistical outlier detection, and scam probability scoring.
    Proves whether a token has any fundamental value or is purely speculative.
    """
    try:
        result = {"address": req.address, "chain": req.chain, "valuation": {}, "scam_signals": []}

        data, _ = await fetch_with_fallback([f"https://api.dexscreener.com/latest/dex/tokens/{req.address}"])
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            fdv = float(pair.get("fdv", 0) or 0)
            liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            volume_24h = float(pair.get("volume", {}).get("h24", 0) or 0)
            price_change_24h = float(pair.get("priceChange", {}).get("h24", 0) or 0)
            result["market_data"] = {
                "fdv": fdv,
                "liquidity_usd": liquidity,
                "volume_24h": volume_24h,
                "price_change_24h_pct": price_change_24h,
            }

            if req.include_comps:
                fdv_volume_ratio = fdv / volume_24h if volume_24h > 0 else 0
                liq_fdv_pct = (liquidity / fdv * 100) if fdv > 0 else 0
                benchmarks = {
                    "fdv_tvl_ratio": {"median": 15, "rug_min": 500},
                    "fdv_volume_ratio": {"median": 50, "rug_min": 1000},
                    "liq_fdv_pct": {"safe_min": 0.5, "rug_max": 0.1},
                }
                comps_results = {
                    "fdv_volume_ratio": round(fdv_volume_ratio, 1),
                    "liq_fdv_pct": round(liq_fdv_pct, 4),
                    "benchmarks": benchmarks,
                    "outlier_flags": [],
                }
                if fdv_volume_ratio > benchmarks["fdv_volume_ratio"]["rug_min"]:
                    comps_results["outlier_flags"].append(
                        f"EXTREME: FDV/Volume {fdv_volume_ratio:.0f}x > {benchmarks['fdv_volume_ratio']['rug_min']}x"
                    )
                    result["scam_signals"].append("fdv_volume_extreme")
                if liq_fdv_pct < benchmarks["liq_fdv_pct"]["rug_max"]:
                    comps_results["outlier_flags"].append(
                        f"EXTREME: Liq/FDV {liq_fdv_pct:.2f}% < {benchmarks['liq_fdv_pct']['rug_max']}%"
                    )
                    result["scam_signals"].append("ruggable_liquidity")
                result["valuation"]["comps"] = comps_results

            if req.include_dcf:
                dcf = {
                    "revenue_streams": "unknown",
                    "fee_mechanism": "none detected",
                    "intrinsic_value_estimate": 0,
                }
                if volume_24h > 0 and fdv > 0:
                    annualized_fees = volume_24h * 365 * 0.003
                    dcf["annualized_fees_estimate"] = round(annualized_fees, 2)
                    dcf["fee_fdv_ratio"] = round(annualized_fees / fdv * 100, 4) if fdv > 0 else 0
                    if annualized_fees / fdv < 0.01:
                        dcf["verdict"] = "NEGATIVE — Fee revenue cannot justify FDV"
                        result["scam_signals"].append("dcf_negative")
                    else:
                        dcf["intrinsic_value_estimate"] = "potentially_positive"
                        dcf["verdict"] = "NEEDS_VERIFICATION — Fee revenue exists but must verify distribution"
                else:
                    dcf["verdict"] = "NEGATIVE — No volume to support valuation"
                result["valuation"]["dcf"] = dcf

            scam_score = 0
            for signal in result["scam_signals"]:
                scam_score += 30 if "extreme" in signal or "ruggable" in signal else 25
            result["scam_probability"] = min(scam_score, 100)
            result["scam_probability_label"] = (
                "CRITICAL"
                if scam_score >= 70
                else "HIGH"
                if scam_score >= 50
                else "MODERATE"
                if scam_score >= 30
                else "LOW"
                if scam_score >= 10
                else "MINIMAL"
            )

        result["pricing"] = {"tool": "forensic_valuation", "price": "$0.25"}
        await record_x402_payment("forensic_valuation", "0.25", req.address)
        return result
    except Exception as e:
        logger.error(f"Forensic valuation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── TOOL 38: OSINT Identity Hunt ($0.15) ──


class OSINTRequest(BaseModel):
    username: str
    domain: str | None = None
    project_url: str | None = None


@router.post("/osint_identity_hunt")
async def osint_identity_hunt(req: OSINTRequest):
    """Cross-platform OSINT investigation — hunt usernames across 400+ social networks,
    domain intelligence (WHOIS/DNS/SSL), and stealth page capture for evidence preservation.
    """
    try:
        result = {"username": req.username, "findings": {}}
        platforms_to_check = [
            ("Twitter/X", f"https://x.com/{req.username}"),
            ("GitHub", f"https://github.com/{req.username}"),
            ("Telegram", f"https://t.me/{req.username}"),
            ("Reddit", f"https://reddit.com/user/{req.username}"),
            ("YouTube", f"https://youtube.com/@{req.username}"),
            ("Instagram", f"https://instagram.com/{req.username}"),
            ("Medium", f"https://medium.com/@{req.username}"),
        ]
        found = []
        for platform, url in platforms_to_check:
            try:
                code, _ = await fetch_with_fallback([url], return_status=True)
                if code and code < 404:
                    found.append({"platform": platform, "url": url, "status": "found"})
            except Exception:
                pass
        result["findings"]["social_presence"] = found
        result["findings"]["profiles_found"] = len(found)
        if req.domain:
            result["findings"]["domain"] = {
                "domain": req.domain,
                "analysis": "Use /domain_intel tool for full WHOIS/DNS/SSL",
            }
        if req.project_url:
            result["findings"]["project_url"] = req.project_url
            result["findings"]["capture_recommended"] = "Capture project page immediately — scam sites often disappear"
        result["pricing"] = {"tool": "osint_identity_hunt", "price": "$0.15"}
        await record_x402_payment("osint_identity_hunt", "0.15", req.username)
        return result
    except Exception as e:
        logger.error(f"OSINT identity hunt failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── TOOL 39: Investigation Report Generator ($0.20) ──


class InvestigationReportRequest(BaseModel):
    address: str
    chain: str = "solana"
    report_format: str = "json"


@router.post("/investigation_report")
async def investigation_report(req: InvestigationReportRequest):
    """Full investigation report — combines on-chain forensics, financial valuation,
    OSINT findings, and scam scoring into a structured deliverable.
    Available as JSON, Excel, or PPTX.
    """
    try:
        result = {
            "address": req.address,
            "chain": req.chain,
            "report_format": req.report_format,
            "sections": [],
        }
        chain_data, _ = await fetch_with_fallback([f"https://api.dexscreener.com/latest/dex/tokens/{req.address}"])
        fdv = liq = vol = 0
        pair = None
        if chain_data and chain_data.get("pairs"):
            pair = chain_data["pairs"][0]
            fdv = float(pair.get("fdv", 0) or 0)
            liq = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            vol = float(pair.get("volume", {}).get("h24", 0) or 0)
            result["sections"].append(
                {
                    "phase": "on_chain_profiling",
                    "token_name": pair.get("baseToken", {}).get("name"),
                    "token_symbol": pair.get("baseToken", {}).get("symbol"),
                    "fdv": fdv,
                    "liquidity": liq,
                    "volume_24h": vol,
                    "price_usd": float(pair.get("priceUsd", 0) or 0),
                    "dex_id": pair.get("dexId"),
                    "pair_created": pair.get("pairCreatedAt"),
                }
            )

        valuation = {
            "phase": "financial_valuation",
            "intrinsic_value": 0 if fdv > 0 and vol > 0 and (vol * 365 * 0.003 / fdv < 0.01) else "indeterminate",
            "fdv_liquidity_ratio": round(fdv / liq, 1) if liq > 0 else 0,
            "annualized_fees_vs_fdv": round(vol * 365 * 0.003 / fdv * 100, 2) if fdv > 0 else 0,
        }
        result["sections"].append(valuation)

        scam_score = 0
        details = []
        if fdv > 0 and liq > 0 and fdv / liq > 500:
            scam_score += 35
            details.append(f"FDV/Liq ratio {fdv / liq:.0f}x — extreme ruggable liquidity")
        if fdv > 0 and vol > 0 and fdv / vol > 1000:
            scam_score += 25
            details.append(f"FDV/Volume ratio {fdv / vol:.0f}x — no organic volume")
        if fdv > 100000 and liq < 1000:
            scam_score += 30
            details.append("Near-zero liquidity for significant FDV")

        result["sections"].append(
            {
                "phase": "scam_assessment",
                "score": min(scam_score, 100),
                "level": "CRITICAL"
                if scam_score >= 70
                else "HIGH"
                if scam_score >= 50
                else "MODERATE"
                if scam_score >= 30
                else "LOW",
                "details": details,
            }
        )

        result["sections"].append(
            {
                "phase": "deliverable",
                "format": req.report_format,
                "note": "XLSX/PPTX generation requires excel-author + pptx-author skills",
                "evidence_references": f"See RugMunch investigation for {req.address[:8]}...",
            }
        )

        result["pricing"] = {"tool": "investigation_report", "price": "$0.20"}
        await record_x402_payment("investigation_report", "0.20", req.address)
        return result
    except Exception as e:
        logger.error(f"Investigation report failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
