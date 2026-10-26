"""Isolated steps of x402 response enrichment.

Extracted from ``x402_enrichment.enrich_tool_response`` so each step stays under
the size and complexity ceilings. Nothing here imports the enrichment router:
the caller injects its helpers (alert hook), so the dependency is one-way.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.routers.x402_external_errors import CACHE_FAILURES, SCAM_FAILURES

logger = logging.getLogger("x402.enrich.ops")

RECENT_ADDRESSES_KEY = "x402:recent:addresses"
RECENT_ADDRESSES_CAP = 999
SCAM_ADDRESS_LIMIT = 5
TRACK_ADDRESS_LIMIT = 5


def collect_scam_flags(addresses: list[str]) -> list[dict[str, Any]]:
    """Run the RAG scam detector over the first addresses; return the hits."""
    import asyncio

    flags: list[dict[str, Any]] = []
    try:
        from app.rag_service import detect_scam_patterns

        for addr in addresses[:SCAM_ADDRESS_LIMIT]:
            try:
                result = asyncio.run(
                    detect_scam_patterns({"address": addr, "chain": "ethereum"}, 0.65)
                )
                if result and result.get("risk_score", 0) > 0:
                    flags.append(
                        {
                            "address": addr[:10] + "...",
                            "risk_score": result.get("risk_score", 0),
                            "patterns": result.get("patterns", [])[:3],
                        }
                    )
            except SCAM_FAILURES as exc:
                logger.warning(
                    "scam_detection_failed address=%s error=%s", addr[:10], exc
                )
    except (ImportError, AttributeError, RuntimeError) as exc:
        logger.warning("scam_detection_unavailable error=%s", exc)
    return flags


def _label_details(labels: dict) -> dict:
    """Per-address label rows, dropping addresses with no labels."""
    return {
        addr: [
            {
                "category": lbl.label_category,
                "name": lbl.label_name,
                "source": lbl.source,
                "sanctioned": lbl.is_sanctioned,
            }
            for lbl in lbls
        ]
        for addr, lbls in labels.items()
        if lbls
    }


def build_envelope(
    enrichment, addresses: list[str], llm_summary: str, scam_flags: list[dict]
) -> dict[str, Any]:
    """Assemble the ``_enrichment`` payload (pre-prune) from the result object."""
    return {
        "source": "wallet_memory_bank",
        "labels_found": sum(1 for found in enrichment.labels.values() if found),
        "addresses_checked": len(addresses),
        "sanctioned": len(enrichment.sanctioned) or None,
        "risk_summary": enrichment.risk_summary or None,
        "llm_summary": llm_summary or None,
        "scam_flags": scam_flags or None,
        "label_details": _label_details(enrichment.labels) or None,
        "similar_patterns": enrichment.similar_patterns or None,
        "performance_ms": round(enrichment.lookup_ms + enrichment.rag_ms, 1),
    }


def prune_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Drop empty/None fields and annotate a label-less response."""
    for key in list(envelope):
        value = envelope[key]
        if (
            value is None
            or (isinstance(value, dict) and not value)
            or (isinstance(value, list) and not value)
        ):
            del envelope[key]
    if not envelope.get("label_details") and not envelope.get("risk_summary"):
        envelope["note"] = "No wallet labels found for queried addresses"
    return envelope


def _recent_row(address: str, tool_name: str) -> str:
    return json.dumps(
        {
            "address": address,
            "tool": tool_name,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )


def apply_side_effects(
    tool_name: str,
    alerts: dict[str, list[str]],
    addresses: list[str],
    r,
    fire_alert,
) -> None:
    """Fire high-risk alerts and record addresses for RAG discovery."""
    for kind, targets in alerts.items():
        if targets:
            fire_alert(tool_name, kind, targets, r)
    if not addresses:
        return
    try:
        for addr in addresses[:TRACK_ADDRESS_LIMIT]:
            r.lpush(RECENT_ADDRESSES_KEY, _recent_row(addr, tool_name))
        r.ltrim(RECENT_ADDRESSES_KEY, 0, RECENT_ADDRESSES_CAP)
    except CACHE_FAILURES as exc:
        logger.warning("recent_addresses_track_failed error=%s", exc)
