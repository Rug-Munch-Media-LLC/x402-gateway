"""
x402 Tool Response Enrichment Middleware
═══════════════════════════════════════════════════════════

Post-execution enrichment layer that annotates x402 tool responses
with wallet intelligence from the Memory Bank (393K labels, 155K addresses)
and live Etherscan V2 contract name lookups.

Architecture:
  1. Tool executes → raw result
  2. Extract addresses from result
  3. Batch ClickHouse lookup (label_name, label_category, is_sanctioned)
  4. Live Etherscan V2 contract name lookups for unknown addresses
  5. If high-value: RAG similarity search against known scam patterns
  6. Optionally: LLM-synthesize a human-readable enrichment paragraph
  7. Merge enrichment into response JSON

Caching: Redis x402:enrich:{addr} with 1hr TTL.
Opt-out: ?enrich=false in query params.
Always opt-in for premium tools, opt-out for speed-only callers.

Storage impact: ~500 Redis keys at steady state, 0 ClickHouse writes.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger("x402.enrich")

# ── Which tool categories get enrichment ────────────────────────
# Wallet-centric tools: enrich with labels, scam checking
# Token-centric tools: enrich with holder label analysis
# Market tools: skip (low overlap, not worth the lookup)
ENRICHABLE_CATEGORIES = {
    "wallet": [
        "whale",
        "smartmoney",
        "cluster",
        "insider",
        "copy_trade_finder",
        "wallet",
        "portfolio_tracker",
        "portfolio_aggregate",
        "wallet_pnl",
        "smart_money_alpha",
        "cross_chain_whale",
        "dormant_whale_alert",
        "full_wallet_dossier",
        "wallet_cluster_score",
        "wallet_drain_scanner",
        "wallet_label_registry",
        "whale_network_map",
        "whale_profile",
        "whale_scan",
        "whale_accumulation",
        "sniper_detect",
        "syndicate_scan",
        "syndicate_track",
        "wallet_graph",
        "deployer_history",
        "kol_performance",
        "insider_network",
        "airdrop_finder",
    ],
    "token": [
        "audit",
        "rugshield",
        "forensics",
        "comprehensive_audit",
        "honeypot_check",
        "rug_pull_predictor",
        "token_deep_dive",
        "token_comparison",
        "fresh_pair",
        "clone_detect",
        "launch",
        "launch_intel",
        "sniper_alert",
        "token_age",
        "token_distribution_health",
        "token_velocity",
        "token_watch_create",
        "token_watch_check",
        "token_watch_alerts",
        "token_watch_list",
        "presale_scanner",
        "fair_launch_detect",
        "listing_predictor",
        "arbitrage_scan",
        "scam_database",
        "protocol_risk",
        "liquidity_depth",
        "liquidity_migration",
        "wash_trading",
        "bundler_detect",
        "mev_alert",
        "mev_protection",
        "liquidity_flow",
    ],
    "security": [
        "urlcheck",
        "sentiment",
        "social_signal",
        "anomaly",
        "profile_flip",
        "contract_info",
        "contract_clone_check",
        "slither_audit",
        "storage_reader",
        "tx_decoder",
        "reentrancy_scanner",
        "phantom_mint_detect",
        "tw_profile",
        "tw_timeline",
        "tw_search",
        "bridge_security",
        "nft_wash_detector",
        "risk_monitor",
        "pulse",
        "fresh_pair",
        "sniper_alert",
    ],
    "market": [
        "market_overview",
        "chain_health",
        "defi_yield_scanner",
        "gas_forecast",
        "anomaly_detector",
        "launch_intel",
    ],
}

# Per-chain variants — strip _chain suffix and check base tool name
CHAIN_SUFFIXES = {
    "solana",
    "base",
    "ethereum",
    "bsc",
    "polygon",
    "arbitrum",
    "optimism",
    "avalanche",
    "fantom",
    "gnosis",
    "tron",
    "bitcoin",
}


def _is_enrichable(tool_name: str) -> str:
    """Check if tool is enrichable, handling per-chain variants."""
    if "_" in tool_name:
        parts = tool_name.rsplit("_", 1)
        if len(parts) == 2 and parts[1] in CHAIN_SUFFIXES:
            base_name = parts[0]
            for cat, tools in ENRICHABLE_CATEGORIES.items():
                if base_name in tools:
                    return cat
    for cat, tools in ENRICHABLE_CATEGORIES.items():
        if tool_name in tools:
            return cat
    return ""


MAX_BATCH_SIZE = 200  # Max addresses per ClickHouse batch query
ENRICH_CACHE_TTL = 3600  # 1 hour
SIMILARITY_THRESHOLD = 0.6  # Cosine similarity floor for RAG matches


@dataclass
class WalletLabel:
    """Lightweight label from ClickHouse lookup."""

    address: str
    label_name: str = ""
    label_category: str = ""
    label_subtype: str = ""
    source: str = ""
    is_sanctioned: bool = False


@dataclass
class EnrichmentResult:
    """Merged enrichment for all addresses in a tool response."""

    labels: dict[str, list[WalletLabel]] = field(default_factory=dict)
    sanctioned: list[str] = field(default_factory=list)
    risk_summary: str = ""
    similar_patterns: list[dict] = field(default_factory=list)
    lookup_ms: float = 0
    rag_ms: float = 0


def _redis(enrich_cache: dict | None = None):
    """Lazy Redis connection (sync client)."""
    import os

    import redis as _redis
    from dotenv import load_dotenv

    load_dotenv(override=True)
    return _redis.Redis(
        host=os.getenv("REDIS_HOST", "rmi-redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD", ""),
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def _ch():
    """Lazy ClickHouse connection."""
    import os

    from clickhouse_driver import Client

    Client(
        host=os.getenv("CH_HOST", "rmi-clickhouse"),
        port=int(os.getenv("CH_PORT", "9000")),
        user=os.getenv("CH_USER", "default"),
        password=os.getenv("CH_PASSWORD", "") or None,
        settings={"max_execution_time": 3},
    )


def extract_addresses(result: dict[str, Any], tool_name: str) -> list[str]:
    """
    Extract wallet/token addresses from a tool result for enrichment lookup.
    Handles common response patterns across all x402 tools.
    """
    addresses = set()
    if not isinstance(result, dict):
        return []

    # Direct fields
    for key in (
        "address",
        "wallet",
        "token_address",
        "contract_address",
        "pair_address",
        "creator",
        "owner",
        "payTo",
    ):
        val = result.get(key)
        if isinstance(val, str) and len(val) >= 32:
            addresses.add(val)

    # Nested lists (holder breakdown, top wallets, cluster members)
    for list_key in (
        "holders",
        "top_wallets",
        "top_holders",
        "cluster_members",
        "whale_wallets",
        "suspicious_wallets",
        "linked_addresses",
        "audits",
        "results",
    ):
        items = result.get(list_key, [])
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    addr = item.get("address") or item.get("wallet") or item.get("token_address")
                    if addr and isinstance(addr, str) and len(addr) >= 32:
                        addresses.add(addr)
                elif isinstance(item, str) and len(item) >= 32:
                    addresses.add(item)

    # Token comparisons have numbered entries
    for key, val in result.items():
        if isinstance(val, dict):
            addr = val.get("address") or val.get("token_address")
            if addr and isinstance(addr, str) and len(addr) >= 32:
                addresses.add(addr)

    return list(addresses)[:MAX_BATCH_SIZE]


def lookup_labels(addresses: list[str]) -> tuple[dict[str, list[WalletLabel]], float]:
    """
    Batch lookup wallet labels from ClickHouse.
    Returns: {address: [WalletLabel, ...]}, query_time_ms
    """
    if not addresses:
        return {}, 0

    t0 = time.time()
    results: dict[str, list[WalletLabel]] = {a: [] for a in addresses}

    try:
        ch = _ch()
        # Use IN clause for batch lookup
        placeholders = ", ".join(["(a)" for a in addresses])
        query = f"""
        SELECT address, label_name, label_category, label_subtype, source, is_sanctioned
        FROM wallet_memory.wallet_labels
        WHERE address IN ({placeholders})
        ORDER BY
            CASE label_category
                WHEN 'sanctioned' THEN 0
                WHEN 'phish-hack' THEN 1
                WHEN 'scam' THEN 2
                WHEN 'etherscan-phish-hack-list' THEN 3
                WHEN 'exploit' THEN 4
                WHEN 'heist' THEN 5
                ELSE 99
            END,
            loaded_at DESC
        LIMIT {MAX_BATCH_SIZE * 5}
        """
        rows = ch.execute(query, addresses)

        for row in rows:
            addr = row[0]
            label = WalletLabel(
                address=addr,
                label_name=row[1] or "",
                label_category=row[2] or "",
                label_subtype=row[3] or "",
                source=row[4] or "",
                is_sanctioned=bool(row[5]),
            )
            results[addr].append(label)

    except Exception as e:
        logger.warning(f"CH label lookup failed: {e}")
        return {}, (time.time() - t0) * 1000

    return results, (time.time() - t0) * 1000


def search_similar_patterns(
    addresses: list[str],
    r: Any = None,
) -> tuple[list[dict], float]:
    """
    Search for similar scam/wallet patterns using RAG embeddings.
    Falls back gracefully if RAG is unavailable.
    """
    t0 = time.time()
    patterns = []

    try:
        from app.rag_service import get_embedder, search_multi_collection

        embedder = get_embedder()
        if not embedder:
            return [], 0

        for addr in addresses[:10]:  # Max 10 RAG queries per call
            # Check cache first
            cache_key = f"x402:enrich:rag:{addr}"
            if r:
                cached = r.get(cache_key)
                if cached:
                    patterns.append(json.loads(cached))
                    continue

            try:
                # Generate embedding for this address
                emb = embedder.embed(addr)
                if not emb or not emb.vector:
                    continue

                # Multi-collection search
                hits = search_multi_collection(
                    emb.vector,
                    collections=["wallet_profiles", "known_scams"],
                    limit=3,
                    min_score=SIMILARITY_THRESHOLD,
                )
                if hits:
                    pattern = {
                        "address": addr,
                        "matches": [
                            {
                                "collection": h.get("collection", ""),
                                "score": round(h.get("score", 0), 3),
                                "type": h.get("metadata", {}).get("type", ""),
                                "description": h.get("metadata", {}).get("description", "")[:200],
                            }
                            for h in hits[:2]
                        ],
                    }
                    patterns.append(pattern)
                    if r:
                        r.setex(cache_key, ENRICH_CACHE_TTL, json.dumps(pattern))
            except Exception as e:
                logger.debug(f"RAG search failed for {addr[:10]}: {e}")
                continue

    except Exception as e:
        logger.debug(f"RAG enrichment unavailable: {e}")

    return patterns, (time.time() - t0) * 1000


def build_risk_summary(
    labels: dict[str, list[WalletLabel]],
    sanctioned: list[str],
    similar_patterns: list[dict],
) -> str:
    """Build a concise human-readable risk summary."""
    parts = []

    total_addrs = len(labels)
    labeled = sum(1 for v in labels.values() if v)
    if labeled:
        parts.append(f"{labeled}/{total_addrs} addresses have known labels")

    if sanctioned:
        parts.append(f"{len(sanctioned)} OFAC-sanctioned wallet(s)")

    scam_count = sum(
        1 for v in labels.values() for l in v if l.label_category in ("phish-hack", "scam", "etherscan-phish-hack-list")
    )
    if scam_count:
        parts.append(f"{scam_count} known phishing/scam address(es) detected")

    cex_count = sum(1 for v in labels.values() for l in v if l.label_category == "cex")
    if cex_count:
        parts.append(f"{cex_count} exchange wallet(s) identified")

    if similar_patterns:
        high_matches = [p for p in similar_patterns if p.get("matches")]
        if high_matches:
            parts.append(f"{len(high_matches)} address(es) match known scam patterns")

    return ". ".join(parts) + "." if parts else ""


def enrich_tool_response(
    tool_name: str,
    raw_result: dict[str, Any],
    request_params: dict | None = None,
    opt_out: bool = False,
) -> dict[str, Any]:
    """
    Main entry point: enrich an x402 tool response with wallet intelligence.

    Called after tool execution, before the response is sent to the client.
    Returns the enriched result dict (mutates in place, also returns for chaining).
    """
    if opt_out:
        return raw_result

    try:
        r = _redis()
    except Exception:
        r = None

    # Determine enrichment type
    category = _is_enrichable(tool_name)

    if not category:
        return raw_result  # Not an enrichable tool

    # 1. Extract addresses
    addresses = extract_addresses(raw_result, tool_name)
    if not addresses:
        return raw_result

    # 2. Batch ClickHouse label lookup
    labels, ch_ms = lookup_labels(addresses)

    # 3. Identify sanctioned wallets
    sanctioned = [a for a, lbls in labels.items() if any(l.is_sanctioned for l in lbls)]

    # 4. RAG similarity search (only for wallet-centric tools)
    similar_patterns = []
    rag_ms = 0
    if category in ("wallet", "security"):
        similar_patterns, rag_ms = search_similar_patterns(addresses[:10], r)

    # 5. Build enrichment result
    enrichment = EnrichmentResult(
        labels=labels,
        sanctioned=sanctioned,
        risk_summary=build_risk_summary(labels, sanctioned, similar_patterns),
        similar_patterns=similar_patterns,
        lookup_ms=ch_ms,
        rag_ms=rag_ms,
    )

    # 5b. Scam pattern detection (wired from rag_service)
    scam_flags = []
    try:
        import asyncio

        from app.rag_service import detect_scam_patterns

        for addr in addresses[:5]:
            try:
                result = asyncio.run(detect_scam_patterns({"address": addr, "chain": "ethereum"}, 0.65))
                if result and result.get("risk_score", 0) > 0:
                    scam_flags.append(
                        {
                            "address": addr[:10] + "...",
                            "risk_score": result.get("risk_score", 0),
                            "patterns": result.get("patterns", [])[:3],
                        }
                    )
            except Exception:
                pass
    except Exception:
        pass

    # 5c. LLM-synthesized summary (DeepSeek Flash, $0.0003/call)
    llm_summary = ""
    if enrichment.risk_summary and scam_flags:
        llm_summary = _synthesize_enrichment(enrichment.risk_summary, scam_flags, tool_name)

    # 5d. Cron intel injection: recent RMI intelligence briefings
    intel_context = _load_recent_intel(r)
    if intel_context:
        raw_result["_intel"] = intel_context

    # 5e. Confidence scoring: quantify data quality for every response
    try:
        from app.routers.x402_advanced_tools import compute_confidence

        raw_result["_confidence"] = compute_confidence(raw_result)
    except Exception:
        pass

    # 6. Merge into response
    raw_result["_enrichment"] = {
        "source": "wallet_memory_bank",
        "labels_found": sum(1 for v in labels.values() if v),
        "addresses_checked": len(addresses),
        "sanctioned": sanction_count if (sanction_count := len(sanctioned)) > 0 else None,
        "risk_summary": enrichment.risk_summary or None,
        "llm_summary": llm_summary or None,
        "scam_flags": scam_flags if scam_flags else None,
        "label_details": {
            a: [
                {
                    "category": l.label_category,
                    "name": l.label_name,
                    "source": l.source,
                    "sanctioned": l.is_sanctioned,
                }
                for l in lbls
            ]
            for a, lbls in labels.items()
            if lbls
        }
        if any(v for v in labels.values())
        else None,
        "similar_patterns": similar_patterns if similar_patterns else None,
        "performance_ms": round(enrichment.lookup_ms + enrichment.rag_ms, 1),
    }

    # Clean up None values
    enrich = raw_result["_enrichment"]
    for k in list(enrich.keys()):
        if (
            enrich[k] is None
            or (isinstance(enrich[k], dict) and not enrich[k])
            or (isinstance(enrich[k], list) and not enrich[k])
        ):
            del enrich[k]

    if not enrich.get("label_details") and not enrich.get("risk_summary"):
        enrich["note"] = "No wallet labels found for queried addresses"

    # 7. Alert hooks for high-risk findings
    if sanction_count > 0:
        _fire_enrichment_alert(tool_name, "sanctioned", sanctioned, r)
    if scam_flags:
        _fire_enrichment_alert(tool_name, "scam_pattern", [s["address"] for s in scam_flags], r)

    # 8. Track addresses for RAG discovery pipeline
    if addresses:
        try:
            for addr in addresses[:5]:
                r.lpush(
                    "x402:recent:addresses",
                    json.dumps(
                        {
                            "address": addr,
                            "tool": tool_name,
                            "timestamp": datetime.utcnow().isoformat(),
                        }
                    ),
                )
            r.ltrim("x402:recent:addresses", 0, 999)
        except Exception:
            pass

    return raw_result


# ── Cron Intel Injection ─────────────────────────────────────


def _load_recent_intel(r=None) -> dict | None:
    """Load the latest RMI intel briefing from Redis for injection into tool results.

    Cron jobs (Daily Briefing, Intel Digest) write summaries to x402:intel:latest.
    Returns None if no recent intel available (older than 12h).
    """
    try:
        if not r:
            r = _redis()
        data = r.get("x402:intel:latest")
        if not data:
            return None
        intel = json.loads(data)
        age_h = (time.time() - intel.get("timestamp", 0)) / 3600
        if age_h > 12:
            return None  # Stale
        return {
            "source": "rmi_cron_intel",
            "briefing": intel.get("summary", "")[:500],
            "age_hours": round(age_h, 1),
            "scanner_alerts": intel.get("alerts", 0),
            "tokens_scanned": intel.get("tokens_scanned", 0),
            "timestamp": intel.get("timestamp", 0),
        }
    except Exception:
        return None


# ── LLM Synthesis ────────────────────────────────────────────


def _synthesize_enrichment(risk_summary: str, scam_flags: list, tool_name: str) -> str:
    """Use DeepSeek Flash to synthesize a human-readable enrichment paragraph."""
    try:
        import json
        import os
        import urllib.request

        key_b64 = os.getenv("LLM_API_KEY_B64", "")
        if not key_b64:
            return ""
        import base64

        api_key = base64.b64decode(key_b64).decode()

        prompt = (
            f"A crypto security tool '{tool_name}' analyzed some addresses and found: "
            f"{risk_summary} "
            f"Scam patterns detected: {json.dumps(scam_flags)[:500]}. "
            f"Write ONE sentence (max 80 chars) summarizing the key risk finding concisely."
        )
        body = json.dumps(
            {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 60,
                "temperature": 0.3,
            }
        ).encode()
        req = urllib.request.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        r = urllib.request.urlopen(req, timeout=5)
        d = json.loads(r.read())
        return d["choices"][0]["message"]["content"].strip().strip('"')
    except Exception:
        return ""  # Best-effort — never block enrichment on LLM failure


# ── Alert Hooks ──────────────────────────────────────────────


def _fire_enrichment_alert(tool_name: str, alert_type: str, data: list, r=None):
    """Fire alert when enrichment finds high-risk addresses."""
    try:
        if not r:
            r = _redis()
        alert = json.dumps(
            {
                "tool": tool_name,
                "type": alert_type,
                "data": data,
                "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
            }
        )
        r.lpush("x402:alerts:high_risk", alert)
        r.ltrim("x402:alerts:high_risk", 0, 999)  # Keep last 1000
        logger.warning(f"ENRICHMENT ALERT: {alert_type} in {tool_name}: {data}")
    except Exception:
        pass


# ── Cross-Chain Label Propagation ────────────────────────────


def propagate_labels_cross_chain():
    """
    One-shot cross-chain propagation: copy phishing/scam/sanctioned labels
    to all chains where the same address exists. Runs on startup + cron.
    """
    try:
        ch = _ch()
        # Find addresses labeled as scam/phishing/sanctioned on one chain
        # and copy those labels to all other chains where the address exists
        count = ch.execute("""
            INSERT INTO wallet_memory.wallet_labels
            (address, chain_id, label_name, label_category, label_subtype, source, is_sanctioned, loaded_at)
            SELECT DISTINCT
                a.address,
                a.chain_id AS chain_id,
                scam.label_name,
                scam.label_category,
                scam.label_subtype || ' (cross-chain from ' || scam.chain_id || ')',
                'cross_chain_propagation',
                scam.is_sanctioned,
                now()
            FROM wallet_memory.wallet_labels AS scam
            JOIN wallet_memory.wallet_labels AS a
                ON a.address = scam.address AND a.chain_id != scam.chain_id
            WHERE scam.label_category IN ('phish-hack','scam','sanctioned','etherscan-phish-hack-list')
              AND a.chain_id NOT IN ('','unknown')
              AND scam.chain_id NOT IN ('','unknown')
            LIMIT 100000
        """)
        logger.info(f"Cross-chain propagation: {count} labels propagated")
        return count
    except Exception as e:
        logger.warning(f"Cross-chain propagation failed: {e}")
        return 0


# ── Lightweight: cache-only enrichment for repeated queries ──


def get_cached_enrichment(address: str) -> dict | None:
    """Check if an address has cached enrichment data."""
    try:
        r = _redis()
        cached = r.get(f"x402:enrich:{address}")
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    return None


# ── Etherscan V2 Live Contract Name Lookups ──


def _get_etherscan_key() -> str:
    """Load Etherscan API key from .env. Returns empty string if not configured."""
    try:
        import os

        from dotenv import load_dotenv

        load_dotenv("/app/.env", override=True)
        return os.getenv("ETHERSCAN_API_KEY", "")
    except Exception:
        return ""


def lookup_contract_names(addresses: list[str], chain: str = "ethereum") -> dict[str, str]:
    """
    Live Etherscan V2 contract name lookups for unknown addresses.
    Returns {address: contract_name} dict.
    Rate-limited to 5/sec (free tier). Skips addresses already in CH labels.
    """
    key = _get_etherscan_key()
    if not key:
        return {}

    # Map chain names to Etherscan chain IDs
    CHAIN_MAP = {
        "ethereum": 1,
        "eth": 1,
        "bsc": 56,
        "polygon": 137,
        "matic": 137,
        "arbitrum": 42161,
        "arb": 42161,
        "optimism": 10,
        "op": 10,
        "base": 8453,
        "avalanche": 43114,
        "avax": 43114,
        "fantom": 250,
        "ftm": 250,
        "gnosis": 100,
    }
    cid = CHAIN_MAP.get(chain.lower(), 1)

    results = {}
    import json
    import time
    import urllib.parse
    import urllib.request

    for addr in addresses[:20]:  # Max 20 per call (free tier 5/sec + timeout budget)
        try:
            params = {
                "chainid": cid,
                "module": "contract",
                "action": "getsourcecode",
                "address": addr,
                "apikey": key,
            }
            url = f"https://api.etherscan.io/v2/api?{urllib.parse.urlencode(params)}"
            r = urllib.request.urlopen(url, timeout=5)
            d = json.loads(r.read())
            if d.get("status") == "1" and d.get("result"):
                contract = d["result"][0]
                name = contract.get("ContractName", "")
                if name and name != addr:
                    results[addr] = name
        except Exception:
            pass
        time.sleep(0.25)  # Respect 5/sec rate limit

    return results


def preload_enrichment_cache(addresses: list[str]):
    """
    Background preload: fetch and cache enrichment for hot addresses.
    Intended for high-traffic addresses (top wallets, trending tokens).
    """
    for addr in addresses[:50]:
        try:
            r = _redis()
            if r.exists(f"x402:enrich:{addr}"):
                continue
            result, _ = lookup_labels([addr])
            labels = result.get(addr, [])
            if labels:
                r.setex(
                    f"x402:enrich:{addr}",
                    1800,
                    json.dumps({"labels": [{"category": l.label_category, "name": l.label_name} for l in labels]}),
                )
        except Exception:
            continue
