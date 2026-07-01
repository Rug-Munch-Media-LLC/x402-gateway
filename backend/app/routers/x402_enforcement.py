"""
RMI x402 Payment Enforcement Middleware
========================================
Intercepts /api/v1/x402-tools/* requests, verifies x402 payment headers.
Returns 402 Payment Required when no valid payment is provided.

ARCHITECTURE (May 23, 2026 — Multi-Facilitator):
- Smart router auto-picks best facilitator per chain/token
- 7 active facilitators: Coinbase CDP, PayAI, Cloudflare x402,
  AsterPay (EUR/SEPA), Primev (fee-free ETH),
  x402-rs (self-hosted), EIP-7702 (universal EVM)
- 3 OFFLINE facilitators (NXDOMAIN): Pieverse, MERX TRON, Satoshi
- 11 payment chains: Base, Solana, Ethereum, BSC,
  Arbitrum, Optimism, Polygon, Avalanche, Fantom, Gnosis, SEPA/EUR
- TRON and Bitcoin chains disabled (sole facilitators dead)
- Fallback: old PaymentVerifier if router unavailable

Author: RMI Development
Date: 2026-05-23
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis as _redis_types
from fastapi import Request, Response
from fastapi.responses import JSONResponse

# ── Imports added 2026-06-25: fix missing deps referenced in middleware ──
from app.core.redis import get_redis
from app.routers.protection import get_client_id

logger = logging.getLogger("x402_enforcement")


# ── Trial tracking (added 2026-06-25) ─────────────────────────────────
# Tracks per-client free-trial usage per tool in Redis.
# Returns (can_use: bool, remaining: int). consume=True decrements.
TRIAL_REDIS_PREFIX = "x402:trial:"
TRIAL_WINDOW_SECONDS = 86400  # 24h rolling window


def check_trial(tool_id: str, client_id: str, max_trials: int | None = None) -> tuple:
    """Check (and optionally consume) a trial credit for this client/tool.

    Returns (can_use: bool, remaining: int).
    If max_trials is None, reads default from TOOL_PRICES (trial_free field).
    """
    if not client_id:
        return False, 0
    if max_trials is None:
        max_trials = TOOL_PRICES.get(tool_id, {}).get("trial_free", 3)
    r = get_redis()
    if not r:
        return True, max_trials  # Redis down → fail open (don't block)
    key = f"{TRIAL_REDIS_PREFIX}{client_id}:{tool_id}"
    try:
        used = int(r.get(key) or 0)
        if used >= max_trials:
            return False, 0
        # Atomically increment (consume one trial)
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, TRIAL_WINDOW_SECONDS)
        results = pipe.execute()
        new_count = results[0]
        return True, max(0, max_trials - new_count)
    except Exception:
        return True, max_trials


logger = logging.getLogger("x402_enforcement_v2")

# ── Discovery endpoint cache ──
_discovery_cache: dict | None = None
_discovery_cache_time: float = 0
DISCOVERY_CACHE_TTL = 300  # 5 minutes

# Security headers for all x402 responses
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Cache-Control": "no-store",
}

# ── x402 SDK Verifier (lazy init) ──
VERIFIER = None  # Kept for backward compat — always None now
_FACILITATOR_CONFIGS = None
_TOKEN_METADATA = None
_verifier_loaded = False


def _ensure_verifier():
    """Initialize the x402 SDK verifier singleton."""
    global _verifier_loaded
    if _verifier_loaded:
        return True
    _verifier_loaded = True
    try:
        from app.routers.x402_verifier import get_verifier

        get_verifier()  # Initializes on first call
        logger.info("x402 enforcement: SDK verifier initialized")
        return True
    except Exception as e:
        logger.error(f"x402 enforcement: SDK verifier init failed: {e}")
        return False


# ── Multi-chain USDC configs ──
# PAYMENT chains: Base and Solana use facilitators (fast, federated verification).
# All other EVM chains use self-verification via Etherscan/Alchemy on-chain checks.
# Same EVM wallet works across all chains — user pays on whichever has USDC.
# This is a competitive advantage: most x402 gateways only take Base.
CHAIN_USDC = {
    # ── Facilitator-verified chains (instant/direct) ──
    "base": {
        "network": "eip155:8453",
        "chain_id": 8453,
        "usdc": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "facilitator",
        "facilitators": ["coinbase_cdp", "payai"],
    },
    "solana": {
        "network": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
        "chain_id": None,
        "usdc": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "name": "USD Coin",
        "version": "2",
        "method": "payai",
        "verify": "facilitator",
        "facilitators": ["payai"],
    },
    "bsc": {
        "network": "eip155:56",
        "chain_id": 56,
        "usdc": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "facilitator",
        "facilitators": ["eip7702"],
    },  # pieverse REMOVED — OFFLINE (api.pieverse.xyz NXDOMAIN)
    "ethereum": {
        "network": "eip155:1",
        "chain_id": 1,
        "usdc": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "facilitator",
        "facilitators": ["primev", "payai", "cloudflare_x402", "eip7702"],
    },
    "tron": {
        "network": "tron:mainnet",
        "chain_id": None,
        "usdc": "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8",
        "name": "USD Coin (TRC20)",
        "version": "2",
        "method": "tron_selfverify",
        "verify": "facilitator",
        "facilitators": ["tron_selfverify"],
        "tokens": {
            "USDT": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
            "USDD": "TPYmHEhy5n8TCEfZGqW2rPbmgh1fGqNBPa",
        },
    },
    "bitcoin": {
        "network": "bitcoin:mainnet",
        "chain_id": None,
        "usdc": "",
        "name": "Bitcoin",
        "version": "2",
        "method": "bitcoin_selfverify",
        "verify": "facilitator",
        "facilitators": ["bitcoin_selfverify"],
        "tokens": {"BTC": "native"},
    },
    # ── Self-verified EVM chains (EIP-7702 universal) ──
    "arbitrum": {
        "network": "eip155:42161",
        "chain_id": 42161,
        "usdc": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    "optimism": {
        "network": "eip155:10",
        "chain_id": 10,
        "usdc": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    "polygon": {
        "network": "eip155:137",
        "chain_id": 137,
        "usdc": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    "avalanche": {
        "network": "eip155:43114",
        "chain_id": 43114,
        "usdc": "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    "fantom": {
        "network": "eip155:250",
        "chain_id": 250,
        "usdc": "0x04068DA6C83AFCFA0e13ba15A6696662335D5B75",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    "gnosis": {
        "network": "eip155:100",
        "chain_id": 100,
        "usdc": "0xDDAfbb505ad214D7b80b1f830fcCc89B60fb7A83",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    # ── SEPA/EUR (AsterPay fiat off-ramp) ──
    "sepa": {
        "network": "sepa:eur",
        "chain_id": None,
        "usdc": "",
        "name": "Euro",
        "version": "1",
        "method": "asterpay",
        "verify": "facilitator",
        "facilitators": ["asterpay"],
        "tokens": {"EUR": "fiat"},
    },
}

# Pay-to addresses — pulled dynamically from WalletManagerV2
# Fallback: env vars or legacy hardcoded addresses


def _get_pay_to_address(chain: str) -> str:
    """Get active x402 payment address from WalletManagerV2, with fallbacks."""
    try:
        from app.walletpress_manager import get_wallet_manager_v2

        mgr = get_wallet_manager_v2(os.getenv("WALLET_VAULT_PASSWORD", ""))
        for w in mgr._wallets.values():
            if w.chain == chain and w.x402_enabled and w.status == "active":
                return w.address
    except Exception:
        pass
    # Fallback: env vars
    if chain == "eth":
        return os.getenv("X402_EVM_PAY_TO", "0x1E3AC01d0fdb976179790BDD02823196A92705C9")
    if chain == "sol":
        return os.getenv("X402_SOL_PAY_TO", "Gix4P9AmwcZRGzr2hCEME5m2QAvY86dBfm8c7e7MpFzv")
    # Try to find any EVM wallet for EVM chains
    if chain in ("base", "polygon", "arbitrum", "optimism", "avalanche", "bsc", "fantom", "gnosis"):
        return _get_pay_to_address("eth")
    return ""


# Deferred — wallet_manager_v2 pulls bip_utils+monero (~450ms)
EVM_PAY_TO = None
SOL_PAY_TO = None
_pay_addresses_loaded = False


def _ensure_pay_addresses():
    global EVM_PAY_TO, SOL_PAY_TO, _pay_addresses_loaded
    if _pay_addresses_loaded:
        return
    _pay_addresses_loaded = True
    EVM_PAY_TO = _get_pay_to_address("eth")
    SOL_PAY_TO = _get_pay_to_address("sol")


# ── Tool pricing (parsed from gateway configs) ──
TOOL_PRICES: dict[str, dict[str, Any]] = {}


def _load_tool_prices():
    """Load tool prices from canonical dict (single source of truth).

    1. Load from CANONICAL_TOOL_PRICES (127 tools, always present)
    2. Enrich from gateway configs if available (adds chain variants)
    3. NO circular catalog API fallback — that was the bug
    """
    import os
    import re

    # ─── 1. Load canonical base (ALWAYS works, no circular dependency) ────
    from app.canonical_tools import CANONICAL_TOOL_PRICES

    TOOL_PRICES.update(CANONICAL_TOOL_PRICES)

    # ─── 2. Enrich from gateway configs (adds chain-specific variants) ────────
    # Try multiple paths: Docker mount, host path, OpenClaw backup
    gateway_base = None
    for candidate in [
        "/app/x402-gateway",
        "/root/backend/x402-gateway",
        "/srv/rmi/backend/x402-gateway",
        "/root/.openclaw/backend/x402-gateway",
    ]:
        if os.path.isdir(candidate):
            gateway_base = candidate
            break

    if gateway_base and os.path.exists(gateway_base):
        logger.info(f"Loading tool prices from gateway configs at {gateway_base}")
        for chain_dir in os.listdir(gateway_base):
            index_path = os.path.join(gateway_base, chain_dir, "index.ts")
            if not os.path.exists(index_path):
                continue
            with open(index_path) as f:
                content = f.read()

            # Flexible regex to extract key-value pairs from tool definitions
            tool_pattern = r"(\w+):\s*\{([^}]+)\}"
            for m in re.finditer(tool_pattern, content):
                tool_id = m.group(1)
                body = m.group(2)
                if "name:" not in body or "price:" not in body:
                    continue

                # Extract individual fields
                name_match = re.search(r'name:\s*"([^"]+)"', body)
                price_match = re.search(r'price:\s*"\$([^"]+)"', body)
                atoms_match = re.search(r'priceAtomic:\s*"([^"]+)"', body)
                cat_match = re.search(r'category:\s*"([^"]+)"', body)
                trial_match = re.search(r"trialFree:\s*(\d+)", body)

                if all([name_match, price_match, atoms_match, cat_match, trial_match]):
                    TOOL_PRICES[tool_id] = {
                        "price_usd": float(price_match.group(1)),  # type: ignore[union-attr]
                        "price_atoms": atoms_match.group(1),  # type: ignore[union-attr]
                        "category": (cat_match.group(1) or "").lower(),  # type: ignore[union-attr]
                        "trial_free": int(trial_match.group(1)),  # type: ignore[union-attr]
                        "description": name_match.group(1) or "",  # type: ignore[union-attr]
                    }

    # ─── 2. Add manual pricing for new tools ────────────────────────────────
    _NEW_TOOL_PRICES = {
        "forensic_valuation": {
            "price_usd": 0.25,
            "price_atoms": "250000",
            "category": "premium",
            "trial_free": 1,
            "description": "Institutional-grade token valuation — DCF intrinsic value, comparable analysis with outlier detection, scam probability scoring",
        },
        "osint_identity_hunt": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "premium",
            "trial_free": 2,
            "description": "Cross-platform OSINT investigation — hunt usernames across 400+ networks, domain intelligence, stealth page capture",
        },
        "investigation_report": {
            "price_usd": 0.20,
            "price_atoms": "200000",
            "category": "premium",
            "trial_free": 1,
            "description": "Full investigation report — on-chain forensics, financial valuation, OSINT findings, scam scoring in one deliverable",
        },
        "whale_accumulation": {
            "price_usd": 0.10,
            "price_atoms": "100000",
            "category": "intelligence",
            "trial_free": 2,
            "description": "Accumulation Pattern Detector — detect stealth accumulation by large holders before price impact. Identifies quiet buying patterns and wallet funding sequences.",
        },
        "social_engineering": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "premium",
            "trial_free": 1,
            "description": "Social Engineering & Identity Fraud Detector — detect fake teams, AI-generated profiles, phishing domains, copied whitepapers, and social engineering campaigns.",
        },
        "forensic_pack": {
            "price_usd": 0.35,
            "price_atoms": "350000",
            "category": "bundle",
            "trial_free": 1,
            "description": "Forensic Investigation Pack — valuation + OSINT + report at 33% discount",
        },
        "token_watch_create": {
            "price_usd": 0.05,
            "price_atoms": "50000",
            "category": "monitoring",
            "trial_free": 3,
            "description": "Set token monitoring watch — alerts when LP drops, price changes, or rug indicators detected",
        },
        "token_watch_check": {
            "price_usd": 0.03,
            "price_atoms": "30000",
            "category": "monitoring",
            "trial_free": 5,
            "description": "One-shot token status check — current LP, price, volume, and rug risk warnings",
        },
    }
    TOOL_PRICES.update(_NEW_TOOL_PRICES)

    # ─── 3. NO MORE circular catalog API fallback ─────────────────────────
    # Removed: was causing circular dependency (catalog reads from TOOL_PRICES,
    # which loads from catalog). Canonical_tools.py is now the source of truth.


# ── Add new tools from x402_tools.py that aren't in gateway configs yet ──
try:
    _NEW_TOOL_PRICES = {
        "forensic_valuation": {
            "price_usd": 0.25,
            "price_atoms": "250000",
            "category": "premium",
            "trial_free": 1,
            "description": "Institutional-grade token valuation — DCF intrinsic value, comparable analysis with outlier detection, scam probability scoring",
        },
        "osint_identity_hunt": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "premium",
            "trial_free": 2,
            "description": "Cross-platform OSINT investigation — hunt usernames across 400+ networks, domain intelligence, stealth page capture",
        },
        "investigation_report": {
            "price_usd": 0.20,
            "price_atoms": "200000",
            "category": "premium",
            "trial_free": 1,
            "description": "Full investigation report — on-chain forensics, financial valuation, OSINT findings, scam scoring in one deliverable",
        },
        "whale_accumulation": {
            "price_usd": 0.10,
            "price_atoms": "100000",
            "category": "intelligence",
            "trial_free": 2,
            "description": "Accumulation Pattern Detector — detect stealth accumulation by large holders before price impact. Identifies quiet buying patterns and wallet funding sequences.",
        },
        "social_engineering": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "premium",
            "trial_free": 1,
            "description": "Social Engineering & Identity Fraud Detector — detect fake teams, AI-generated profiles, phishing domains, copied whitepapers, and social engineering campaigns.",
        },
        "forensic_pack": {
            "price_usd": 0.35,
            "price_atoms": "350000",
            "category": "bundle",
            "trial_free": 1,
            "description": "Forensic Investigation Pack — valuation + OSINT + report at 33% discount",
        },
        # API / meta tools — must be in TOOL_PRICES at startup (not loaded from catalog)
        "catalog": {
            "price_usd": 0.00,
            "price_atoms": "0",
            "category": "api",
            "trial_free": 999,
            "description": "Browse available tools, pricing, and chain support",
        },
        "smart_money_alpha": {
            "price_usd": 0.01,
            "price_atoms": "10000",
            "category": "intelligence",
            "trial_free": 3,
            "description": "Smart money alpha signals — track wallets that consistently outperform the market",
        },
        "meme_vibe_score": {
            "price_usd": 0.01,
            "price_atoms": "10000",
            "category": "social",
            "trial_free": 3,
            "description": "Meme token vibe scoring — sentiment, community strength, and virality analysis",
        },
        "mcp-proxy": {
            "price_usd": 0.01,
            "price_atoms": "10000",
            "category": "api",
            "trial_free": 5,
            "description": "MCP protocol proxy — route tool calls through the x402 payment layer",
        },
        "human-execute": {
            "price_usd": 0.02,
            "price_atoms": "20000",
            "category": "api",
            "trial_free": 2,
            "description": "Human-in-the-loop execution — wallet-based payment for manual crypto investigation tasks",
        },
        # Premium standout tools
        "reputation_score": {
            "price_usd": 0.10,
            "price_atoms": "100000",
            "category": "premium",
            "trial_free": 1,
            "description": "Comprehensive 0-100 trust score combining wallet labels, scam databases, deployer history, and RAG similarity matching",
        },
        "webhook_register": {
            "price_usd": 0.02,
            "price_atoms": "20000",
            "category": "monitoring",
            "trial_free": 2,
            "description": "Register webhook URL for real-time monitoring alerts — rug pulls, whale moves, price crashes",
        },
        "webhook_list": {
            "price_usd": 0.00,
            "price_atoms": "0",
            "category": "monitoring",
            "trial_free": 999,
            "description": "List registered webhooks for an address",
        },
        "investigation_report": {
            "price_usd": 0.25,
            "price_atoms": "250000",
            "category": "premium",
            "trial_free": 1,
            "description": "AI-generated comprehensive investigation report — reputation, forensics, market context, recommendation",
        },
        # Advanced standout tools
        "rug_probability": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "premium",
            "trial_free": 1,
            "description": "Predictive rug pull probability 0-100 — honeypot + liquidity + deployer + social signals",
        },
        "history": {
            "price_usd": 0.08,
            "price_atoms": "80000",
            "category": "analysis",
            "trial_free": 2,
            "description": "Historical scanner time-series — risk/liquidity/volume/price trends over hours",
        },
        "narrative": {
            "price_usd": 0.05,
            "price_atoms": "50000",
            "category": "social",
            "trial_free": 3,
            "description": "Market narrative engine — what is the market saying about this token RIGHT NOW",
        },
        # Institutional tools
        "portfolio_risk": {
            "price_usd": 0.20,
            "price_atoms": "200000",
            "category": "premium",
            "trial_free": 1,
            "description": "Cross-chain portfolio risk dashboard — unified risk across multiple wallets and chains",
        },
        "defi_position": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "defi",
            "trial_free": 1,
            "description": "DeFi position analyzer — LP holdings, impermanent loss estimation, yield sustainability, protocol risk",
        },
        "liquidation_cascade": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "defi",
            "trial_free": 1,
            "description": "Liquidation Cascade Risk Analyzer — cross-chain DeFi position monitoring, health factor computation, and cascade scenario simulation for Aave/Compound positions",
        },
        "mev_detect": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "security",
            "trial_free": 2,
            "description": "MEV/Sandwich attack detection — sandwich attacks, frontrunning, arbitrage extraction, MEV bot identification",
        },
        # Alpha tools
        "composite_score": {
            "price_usd": 0.25,
            "price_atoms": "250000",
            "category": "premium",
            "trial_free": 1,
            "description": "RMI Composite Score — one number combining ALL signals for instant buy/sell/avoid decisions",
        },
        "smart_money": {
            "price_usd": 0.20,
            "price_atoms": "200000",
            "category": "intelligence",
            "trial_free": 1,
            "description": "Smart Money P&L Tracker — real profitability-based wallet tracking, find the actual profitable traders",
        },
        "clone_detect": {
            "price_usd": 0.10,
            "price_atoms": "100000",
            "category": "security",
            "trial_free": 2,
            "description": "Token Clone Detector — find tokens cloned from known rug pulls by name, symbol, and deployer patterns",
        },
        "wash_trade_detect": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "security",
            "trial_free": 2,
            "description": "Wash Trading & Insider Detection — artificial volume, coordinated buying, insider accumulation patterns",
        },
        # RIP: Rug Pull Imminence Predictor
        "rug_imminence": {
            "price_usd": 0.20,
            "price_atoms": "200000",
            "category": "security",
            "trial_free": 1,
            "description": "Rug Pull Imminence Predictor — AI-powered early warning system fusing 7 signals to predict imminent rug pulls before they happen",
        },
        "cross_chain_whale": {
            "price_usd": 0.08,
            "price_atoms": "80000",
            "category": "intelligence",
            "trial_free": 2,
            "description": "Track whale wallets across multiple blockchains simultaneously — maps cross-chain capital flows, bridge migrations, and multi-network positioning of top holders",
        },
    }
    TOOL_PRICES.update(_NEW_TOOL_PRICES)
except Exception as e:
    logger.warning(f"x402 enforcement: could not add new tool prices: {e}")

# ── TOOL_PRICES lazy loading (deferred — saves ~1.5s cold start) ──
_tool_prices_extras_loaded = False


def _ensure_tool_prices():
    """Load expanded tools, bundles, and DataBus pricing on first use."""
    global _tool_prices_extras_loaded
    if _tool_prices_extras_loaded:
        return
    _tool_prices_extras_loaded = True

    # Expanded tools: 44 new specialized tools + 80 per-chain variants
    try:
        from app.routers._expanded_tools import ADDITIONAL_TOOLS as _EXPANDED_TOOLS

        TOOL_PRICES.update(_EXPANDED_TOOLS)
        logger.info(f"x402 enforcement: loaded {len(_EXPANDED_TOOLS)} expanded tools")
    except Exception as e:
        logger.warning(f"x402 enforcement: could not load expanded tools: {e}")

    # x402_tools.py BUNDLES dict as fallback
    try:
        from app.routers.x402_tools import BUNDLES as _TOOLS_BUNDLES

        for bid, b in _TOOLS_BUNDLES.items():
            if bid not in TOOL_PRICES and b.get("category") == "bundle":
                TOOL_PRICES[bid] = {
                    "price_usd": b.get("bundle_price_usd", 0.01),
                    "price_atoms": b.get("bundle_price_atoms", "10000"),
                    "category": "bundle",
                    "trial_free": b.get("trial_free", 1),
                    "description": b.get("name", bid),
                }
    except Exception as e:
        logger.warning(f"x402 enforcement: could not load x402_tools bundles: {e}")

    # DataBus-powered tools
    try:
        from app.routers.x402_databus_tools import X402_TOOL_PRICING as _DATABUS_PRICING

        TOOL_PRICES.update(_DATABUS_PRICING)
        logger.info(f"x402 enforcement: loaded {len(_DATABUS_PRICING)} DataBus-powered tools")
    except Exception as e:
        logger.warning(f"x402 enforcement: could not load DataBus tools: {e}")

    # Clean up corrupted tool IDs
    _bad_keys = [k for k in TOOL_PRICES if "{" in k or "/" in k or " " in k]
    if _bad_keys:
        logger.warning(f"Removing corrupted tool IDs: {_bad_keys}")
    for _k in _bad_keys:
        del TOOL_PRICES[_k]


# ── Load all tool prices from gateway configs ──
_load_tool_prices()

# ── Clean up corrupted tool IDs (template variables, paths with slashes/braces) ──
# (bad-key cleanup moved into _ensure_tool_prices())

# ── Chain display names (used in human-readable messages) ──
CHAIN_NAMES = {
    "base": "Base",
    "solana": "Solana",
    "ethereum": "Ethereum",
    "bsc": "BNB Chain",
    "tron": "TRON",
    "bitcoin": "Bitcoin",
    "arbitrum": "Arbitrum",
    "optimism": "Optimism",
    "polygon": "Polygon",
    "avalanche": "Avalanche",
    "fantom": "Fantom",
    "gnosis": "Gnosis",
    "sepa": "SEPA (EUR)",
}

# ── x402 v2 spec helpers ──
import base64 as _base64


def _build_accepts_list(tool_id: str, pricing: dict) -> list:
    """Build x402 v2 spec 'accepts' array from TOOL_PRICES and CHAIN_USDC.
    _ensure_pay_addresses()

    This produces the canonical PaymentRequirements format that official
    x402 SDKs (@x402/fetch, x402 Python, x402 MCP) parse via PAYMENT-REQUIRED
    header or response body.
    """
    accepts = []
    for chain_key, cfg in CHAIN_USDC.items():
        method = cfg["method"]

        # Determine pay-to address based on chain
        if method == "payai":
            pay_to = SOL_PAY_TO
        elif method == "bitcoin_selfverify":
            pay_to = os.getenv("X402_BTC_PAY_TO", "")
        elif method == "tron_selfverify":
            pay_to = os.getenv("X402_TRON_PAY_TO", "")
        elif method == "asterpay":
            pay_to = os.getenv("ASTERPAY_SEPA_IBAN", "")
        else:
            pay_to = EVM_PAY_TO

        # Determine asset (primary token for the chain)
        asset = cfg.get("usdc", "")
        if not asset and "tokens" in cfg:
            first_token = next(iter(cfg["tokens"].values()), "")
            asset = first_token if first_token != "native" else ""

        extra = {
            "name": cfg["name"],
            "version": cfg["version"],
            "tool": tool_id,
            "chain": chain_key,
        }

        if method == "local_eip712" and cfg.get("chain_id"):
            extra["domain"] = {
                "name": cfg["name"],
                "version": cfg["version"],
                "chainId": cfg["chain_id"],
                "verifyingContract": pay_to,
            }
        elif method == "payai":
            extra["feePayer"] = "2wKupLR9q6wXYppw8Gr2NvWxKBUqm4PPJKkQfoxHDBg4"
        elif method == "tron_selfverify":
            extra["tronNetwork"] = "mainnet"
            extra["trc20Tokens"] = cfg.get("tokens", {})
        elif method == "bitcoin_selfverify":
            extra["paymentNetwork"] = "bitcoin"
            extra["settlementChains"] = ["base", "solana"]
        elif method == "asterpay":
            extra["currency"] = "EUR"
            extra["sepa"] = True

        requirement = {
            "scheme": "exact",
            "network": cfg["network"],
            "asset": asset,
            "amount": pricing["price_atoms"],
            "payTo": pay_to,
            "maxTimeoutSeconds": 180,
            "extra": extra,
        }

        # Add supported tokens for multi-token chains
        if "tokens" in cfg:
            requirement["supportedTokens"] = list(cfg["tokens"].keys())

        accepts.append(requirement)

    return accepts


def _build_bazaar_extension(tool_id: str, pricing: dict, accepts: list) -> dict:
    """Build x402 v2 bazaar extension for facilitator discovery indexing.

    Per x402 v2 bazaar extension spec, the extension follows the standard v2
    pattern: {info: {...}, schema: {...}}.

    - info: Contains the actual discovery data (HTTP method, parameters, output format)
    - schema: JSON Schema (Draft 2020-12) that validates the structure of info

    Per the bazaar spec:
    - input.type: always "http"
    - input.method: HTTP method (GET for tool_id ending in _info/_list, POST for others)
    - output.type: always "json"
    """
    # Map our categories to bazaar-standard categories
    CATEGORY_MAP = {
        "security": "security",
        "intelligence": "intelligence",
        "market": "market-data",
        "analysis": "analytics",
        "social": "social",
        "launchpad": "launchpad",
        "bundle": "bundles",
        "defi": "defi",
        "premium": "premium",
        "api": "api",
        "variant": "crypto",
    }
    category = pricing.get("category", "analysis")
    bazaar_category = CATEGORY_MAP.get(category, category)

    # Determine HTTP method: GET for read-only queries, POST for tools that execute
    read_only_suffixes = ("_info", "_list", "_check", "_scan", "_status", "_health")
    is_read_only = tool_id.endswith(read_only_suffixes)
    http_method = "GET" if is_read_only else "POST"

    # Build input/output from the first accepted payment method
    first_accept = accepts[0] if accepts else {}
    first_accept.get("extra", {})

    # Build inputSchema matching the bazaar spec's info structure
    info_input: dict[str, Any] = {
        "type": "http",
        "method": http_method,
    }

    if http_method == "GET":
        # Query params for GET: address and optional chain
        info_input["queryParams"] = {
            "address": {
                "type": "string",
                "description": f"Blockchain address or identifier for {tool_id}",
            },
            "chain": {"type": "string", "description": "Blockchain to query (default: base)"},
        }
    else:
        # Body params for POST: address and chain
        info_input["bodyType"] = "json"
        info_input["body"] = {
            "address": {
                "type": "string",
                "description": f"Blockchain address or identifier for {tool_id}",
            },
            "chain": {"type": "string", "description": "Blockchain to query (default: base)"},
        }

    info = {
        "input": info_input,
        "output": {
            "type": "json",
            "example": {
                "data": {"result": "tool output"},
                "sources_used": ["source1", "source2"],
            },
        },
        "category": bazaar_category,
        "tags": ["crypto", "security", "blockchain", tool_id],
        "description": pricing.get("description", f"{tool_id} — Rug Munch Intelligence"),
    }

    # Schema that validates the info structure per bazaar spec
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "input": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "const": "http"},
                    "method": {
                        "type": "string",
                        "enum": ["GET", "HEAD", "DELETE"]
                        if http_method == "GET"
                        else ["POST", "PUT", "PATCH"],
                    },
                    "queryParams": {"type": "object"}
                    if http_method == "GET"
                    else {"type": "object"},
                    "bodyType": {"type": "string", "enum": ["json", "form-data", "text"]},
                    "body": {"type": "object"},
                },
                "required": ["type", "method"],
                "additionalProperties": False,
            },
            "output": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "example": {"type": "object"},
                },
                "required": ["type"],
            },
            "category": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "description": {"type": "string"},
        },
        "required": ["input"],
    }

    return {
        "info": info,
        "schema": schema,
    }


def _build_resource_info(tool_id: str, pricing: dict) -> dict:
    """Build x402 v2 ResourceInfo object for PaymentRequired response.

    Per x402 v2 spec section 5.1.2, ResourceInfo has only three fields:
    - url (required): URL of the protected resource
    - description (optional): Human-readable description
    - mimeType (optional): MIME type of the expected response

    NOTE: serviceName, tags, and iconUrl are NOT part of the spec's ResourceInfo.
    The v2 spec does NOT include serviceName/tags/iconUrl at the resource level.
    We preserve them as legacy compat fields in x402.resource_metadata for our
    own internal use, but they must not appear in the spec-compliant ResourceInfo.
    """
    description = pricing.get("description", f"Rug Munch Intelligence — {tool_id}")
    # Per spec: max 200 chars for description
    if len(description) > 200:
        description = description[:197] + "..."

    return {
        "url": f"https://mcp.rugmunch.io/api/v1/x402-tools/{tool_id}",
        "description": description,
        "mimeType": "application/json",
    }


# ── 402 response builder ──
def build_402_response(tool_id: str, client_id: str = "") -> JSONResponse:
    """Build an x402 v2 compliant Payment Required response.

    Returns both:
    - V2 spec format (x402Version, resource, accepts) for official SDK compatibility
    - Legacy format (error, tool, price, x402.requirements) for backward compat
    - PAYMENT-REQUIRED base64 header for @x402/fetch and x402 Python SDK

    The v2 spec requires:
    - Top-level x402Version: 2
    - Top-level resource: {url, description, mimeType, serviceName, tags, iconUrl}
    - Top-level accepts: [{scheme, network, asset, amount, payTo, maxTimeoutSeconds, extra}]
    - PAYMENT-REQUIRED header: base64 JSON of {x402Version, resource, accepts}
    - Optional extensions: {bazaar: {discoverable, category, tags, inputSchema, outputSchema}}
    """
    # Ensure pay-to addresses are loaded before building 402 response (added 2026-06-25)
    _ensure_pay_addresses()
    pricing = TOOL_PRICES.get(tool_id, {"price_usd": 0.01, "price_atoms": "10000", "trial_free": 3})
    trial_free = pricing.get("trial_free", 3)

    # Check actual remaining trials for this client
    remaining = 0
    if client_id:
        _can_use, remaining = check_trial(tool_id, client_id)

    # Build v2 spec accepts array (shared by both body and header)
    accepts = _build_accepts_list(tool_id, pricing)

    # Build v2 spec resource object
    resource = _build_resource_info(tool_id, pricing)

    # Build bazaar extension for discovery indexing
    bazaar = _build_bazaar_extension(tool_id, pricing, accepts)

    # ── V2 spec body format (what official SDKs parse) ──
    v2_body = {
        "x402Version": 2,
        "error": "PAYMENT-SIGNATURE header is required",
        "resource": resource,
        "accepts": accepts,
        "extensions": {
            "bazaar": bazaar,
        },
        # ── Legacy compat fields (not in spec but needed for our gateway clients) ──
        "error_legacy": "Payment Required",
        "tool": tool_id,
        "price": f"${pricing['price_usd']:.2f}",
        "trial_free": trial_free,
        "trial_remaining": remaining,
        "trial_used": remaining <= 0,
        "wallet_required": remaining == -1,
        "message": (
            f"Connect a wallet to continue. Your 1 free trial is used — link MetaMask or Phantom to get {trial_free} free calls per tool. From ${pricing['price_usd']:.2f}/call after that."
            if remaining == -1
            else f"All {trial_free} free trial{'s' if trial_free != 1 else ''} used. Pay {pricing['price_atoms']} atoms to use {tool_id}. Pay on {', '.join(CHAIN_NAMES.get(c, c) for c in CHAIN_USDC)}."
        ),
        "accepted_chains": list(CHAIN_USDC.keys()),
        "chain_details": {
            k: {
                "network": v["network"],
                "facilitators": v.get("facilitators", []),
                "tokens": list(v.get("tokens", {"USDC": v.get("usdc", "")}).keys()),
            }
            for k, v in CHAIN_USDC.items()
        },
        # ── Legacy compat: nested requirements for our Cloudflare workers ──
        "x402": {
            "version": "2",
            "requirements": accepts,  # Same array, different field name for backward compat
        },
    }

    # ── Build PAYMENT-REQUIRED header (base64 JSON of v2 spec PaymentRequired) ──
    payment_required_header_obj = {
        "x402Version": 2,
        "error": "PAYMENT-SIGNATURE header is required",
        "resource": resource,
        "accepts": accepts,
        "extensions": {"bazaar": bazaar},
    }
    payment_required_b64 = _base64.b64encode(
        json.dumps(payment_required_header_obj, separators=(",", ":")).encode()
    ).decode("ascii")

    return JSONResponse(
        status_code=402,
        content=v2_body,
        headers={
            "PAYMENT-REQUIRED": payment_required_b64,
            "X-Paywall-Version": "2",
            "Content-Type": "application/json",
            **SECURITY_HEADERS,
        },
    )


# ── Payment header parser (supports v1 x-pay and v2 PAYMENT-SIGNATURE) ──
def parse_x_pay_header(header_value: str) -> dict | None:
    """Parse payment payload from x-pay / PAYMENT-SIGNATURE header.

    Supports three formats:
    1. v2 spec: base64-encoded JSON (PAYMENT-SIGNATURE header)
    2. v1 legacy: "x402 <json>" or "X-Pay: <json>" (x-pay header)
    3. Plain JSON
    """
    if not header_value or not header_value.strip():
        return None
    try:
        stripped = header_value.strip()

        # v2 spec: PAYMENT-SIGNATURE is base64-encoded JSON
        # Try base64 decode first (will fail fast if it's not base64)
        try:
            decoded = _base64.b64decode(stripped).decode("utf-8")
            if decoded.startswith("{"):
                payload = json.loads(decoded)
                # v2 format: normalize PaymentPayload to our expected structure
                # v2 PaymentPayload has: x402Version, resource, accepted, payload, extensions
                # Our verifier expects: accepted (dict), payload (with signature/authorization)
                if "x402Version" in payload:
                    # v2 PaymentPayload — our verifier already handles this format
                    # It extracts network from payload.accepted and routes to facilitator
                    return payload
                return payload
        except Exception:
            pass  # Not base64, try other formats

        # v1 legacy: "x402 <json>" or "X-Pay: <json>"
        if stripped.startswith("x402 "):
            payload_str = stripped[5:].strip()
        elif stripped.startswith("X-Pay: "):
            payload_str = stripped[7:].strip()
        else:
            payload_str = stripped

        if payload_str.startswith("{"):
            return json.loads(payload_str)

        return None
    except Exception as e:
        logger.warning(f"Failed to parse payment header: {e}")
        return None


# ── Verification via Facilitator Router ──
async def verify_payment_via_router(payload: dict) -> dict:
    """Verify x402 payment payload through the multi-facilitator smart router.

    Flow:
    1. Parse payload → extract network (chain) and asset (token)
    2. Map network to chain_key (e.g. 'eip155:8453' → 'base', 'tron:mainnet' → 'tron')
    3. Determine token symbol from asset address
    4. Route to best facilitator via FacilitatorRouter.verify()
    5. Fall back to old verify_payment() if router not available
    """
    accepted = payload.get("accepted", {})
    network = accepted.get("network", "")
    asset_address = accepted.get("asset", "")

    # Map network → chain_key
    chain_key = None
    token_symbol = "USDC"

    for ck, cfg in CHAIN_USDC.items():
        if cfg["network"] == network:
            chain_key = ck
            # Detect token from asset address
            if asset_address:
                token_symbol = _detect_token_from_asset(asset_address, cfg)
            break

    if not chain_key:
        # Unknown network — fall back to old verifier
        from app.routers.x402_middleware import PaymentVerifier

        verifier = PaymentVerifier()
        return await verifier.verify_payment(
            json.dumps(payload),
            network_key="base",
        )

    # Try the smart router first
    try:
        from app.facilitators.router import get_facilitator_router

        router = get_facilitator_router()

        # Build requirements from payload
        requirements = {
            "x402Version": payload.get("x402Version", 2),
            "resource": payload.get("resource", {}),
            "accepts": [
                {
                    "scheme": accepted.get("scheme", "exact"),
                    "network": network,
                    "asset": asset_address,
                    "amount": accepted.get("amount", ""),
                    "payTo": accepted.get("payTo", ""),
                    "maxTimeoutSeconds": accepted.get("maxTimeoutSeconds", 180),
                    "extra": accepted.get("extra", {}),
                }
            ],
        }

        result = await router.verify(
            payload=payload,
            chain_key=chain_key,
            token_symbol=token_symbol,
            requirements=requirements,
        )

        if result.verified:
            logger.info(
                f"Router verified payment via {result.facilitator}: "
                f"chain={chain_key} token={token_symbol} amount={result.amount}"
            )
            return {
                "verified": True,
                "reason": result.reason,
                "tx_hash": result.tx_hash,
                "payer": result.payer,
                "amount": result.amount,
                "chain": chain_key,
                "token": token_symbol,
                "facilitator": result.facilitator,
                "method": f"router:{result.facilitator}",
            }
        else:
            logger.warning(
                f"Router rejected payment for {chain_key}/{token_symbol}: {result.reason}"
            )
            return {
                "verified": False,
                "reason": result.reason,
                "chain": chain_key,
                "token": token_symbol,
            }

    except ImportError:
        logger.debug("Facilitator router not available — falling back to old verifier")
    except Exception as e:
        logger.error(f"Router verification error: {e} — falling back to old verifier")

    # Fallback: old verification logic
    return await verify_payment(payload)


def _detect_token_from_asset(asset_address: str, chain_cfg: dict) -> str:
    """Detect token symbol from asset address using chain config."""
    asset_lower = asset_address.lower()

    # Check USDC
    if chain_cfg.get("usdc", "").lower() == asset_lower:
        return "USDC"

    # Check additional tokens
    tokens = chain_cfg.get("tokens", {})
    for symbol, addr in tokens.items():
        if isinstance(addr, str) and addr.lower() == asset_lower:
            return symbol

    # Heuristic detection
    if "TR7NH" in asset_lower:
        return "USDT"  # USDT on TRC20
    if "TEkxi" in asset_lower:
        return "USDC"  # USDC on TRC20
    if "TPYm" in asset_lower:
        return "USDD"  # USDD on TRC20
    if asset_lower == "native" or asset_address == "BTC":
        return "BTC"

    return "USDC"  # Default


# ── Original verification logic (fallback) ──
async def verify_payment(payload: dict) -> dict:
    """Verify x402 payment using the official SDK.

    Uses x402ResourceServer + HTTPFacilitatorClient for cryptographic verification.
    Falls back to on-chain self-verification for chains without facilitator support.
    """
    accepted = payload.get("accepted", {})
    network = accepted.get("network", "")

    # Step 1: SDK verification (EIP-712 via facilitator)
    try:
        from app.routers.x402_verifier import get_verifier

        v = get_verifier()
        result = await v.verify_payment(payload, network=network)

        if result.get("verified"):
            logger.info(
                "sdk_verification_success",
                extra={
                    "network": network,
                    "payer": str(result.get("payer", ""))[:16],
                },
            )
            return result

        logger.warning(
            "sdk_verification_failed",
            extra={"network": network, "reason": result.get("reason", "unknown")},
        )
    except ImportError:
        logger.error("x402_verifier_not_available")
        return {"verified": False, "reason": "x402 SDK verifier not available"}
    except Exception as e:
        logger.error("sdk_verification_error", extra={"error": str(e)[:200]})

    # Step 2: Self-verification fallback (on-chain tx receipt check)
    chain_cfg = None
    for ck, cc in CHAIN_USDC.items():
        if cc["network"] == network:
            chain_cfg = cc
            break

    if chain_cfg and chain_cfg.get("verify") == "self":
        return await self_verify_evm_usdc(payload, chain_cfg.get("name", "").lower(), chain_cfg)

    return {"verified": False, "reason": f"Verification failed for network: {network}"}


async def self_verify_evm_usdc(payload: dict, chain_key: str, chain_cfg: dict) -> dict:
    """Self-verify a USDC transfer on EVM chains without a facilitator.
    _ensure_pay_addresses()

    How it works:
    1. Extract tx hash, network, sender from the x402 payload
    2. Look up the transaction receipt on Etherscan/BSCScan/etc via eth_getTransactionReceipt
    3. Decode ERC-20 Transfer events from receipt logs (topic0 = keccak256("Transfer(address,address,uint256)"))
    4. Verify: correct USDC contract, 'to' matches PAY_TO, amount matches expected price in atoms
    5. Mark the tx as spent in Redis with 86400s TTL (prevent double-use, 24h window)
    """
    # ERC-20 Transfer event signature: keccak256("Transfer(address,address,uint256)")
    TRANSFER_EVENT_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

    try:
        accepted = payload.get("accepted", {})
        tx_hash = payload.get("txHash") or payload.get("signature") or accepted.get("transaction")
        payer = payload.get("payer") or payload.get("from") or accepted.get("payer")
        amount_atoms = accepted.get("amount") or payload.get("amount")

        if not tx_hash:
            return {"verified": False, "reason": "No tx hash in payment payload"}

        # Prevent double-spend: check Redis first
        r = get_redis()
        if r:
            spent_key = f"x402:spent_tx:{tx_hash}"
            if r.get(spent_key):
                return {"verified": False, "reason": f"Transaction {tx_hash[:16]}... already used"}

        # Resolve expected amount from tool pricing if not in payload
        tool_id = (
            accepted.get("extra", {}).get("tool")
            or payload.get("tool")
            or accepted.get("resource", "").split("/")[-1]
            if accepted.get("resource")
            else None
        )
        _ensure_tool_prices()
        if not amount_atoms and tool_id and tool_id in TOOL_PRICES:
            amount_atoms = TOOL_PRICES[tool_id].get("price_atoms")

        # ── Blockscout API v2 (free, no API key needed) ──
        # Maps chain_id → Blockscout base URL
        chain_id = chain_cfg.get("chain_id")
        blockscout_urls = {
            1: "https://eth.blockscout.com",
            42161: "https://arbitrum.blockscout.com",
            10: "https://optimism.blockscout.com",
            137: "https://polygon.blockscout.com",
            8453: "https://base.blockscout.com",
            100: "https://gnosis.blockscout.com",
            43114: "https://avalanche.blockscout.com",
            250: "https://fantom.blockscout.com",
            56: "https://bsc.blockscout.com",
        }

        blockscout_base = blockscout_urls.get(chain_id)
        if not blockscout_base:
            return {"verified": False, "reason": f"No Blockscout URL for chain_id {chain_id}"}

        import httpx

        # Blockscout v2 API: /api/v2/transactions/{tx_hash}
        tx_url = f"{blockscout_base}/api/v2/transactions/{tx_hash}"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(tx_url)
            if resp.status_code != 200:
                return {
                    "verified": False,
                    "reason": f"TX {tx_hash[:16]}... not found on {chain_key} (HTTP {resp.status_code})",
                }
            tx_data = resp.json()

        # Check transaction status
        tx_status = tx_data.get("status") or tx_data.get("result")
        if tx_status != "ok" and tx_status != "1":
            return {
                "verified": False,
                "reason": f"TX {tx_hash[:16]}... failed on-chain (status={tx_status})",
            }

        # Decode ERC-20 Transfer events from receipt logs
        our_address = (EVM_PAY_TO or "").lower()
        usdc_address = chain_cfg["usdc"].lower()
        expected_amount = str(amount_atoms) if amount_atoms else None

        logs = tx_data.get("decoded_input", {}).get("logs") or tx_data.get("logs") or []
        if not logs:
            # Try alternate: fetch receipt directly
            receipt_url = f"{blockscout_base}/api?module=transaction&action=gettxreceiptstatus&txhash={tx_hash}"
            async with httpx.AsyncClient(timeout=10) as client2:
                r2 = await client2.get(receipt_url)
                if r2.status_code == 200:
                    receipt_data = r2.json()
                    logs = receipt_data.get("result", {}).get("logs", []) or receipt_data.get(
                        "logs", []
                    )

        transfer_found = False
        amount_match = False
        actual_amount = None

        for log in logs:
            topics = log.get("topics", [])

            if len(topics) < 3:
                continue
            if topics[0].lower() != TRANSFER_EVENT_TOPIC0:
                continue
            if log.get("address", "").lower() != usdc_address:
                continue

            # Decode 'to' address from topic2
            try:
                to_address = "0x" + topics[2][-40:].lower()
            except (IndexError, ValueError):
                continue

            if to_address != our_address:
                continue

            # Decode amount from data field
            transfer_found = True
            log_data = log.get("data", "0x")
            try:
                if log_data and log_data.startswith("0x") and len(log_data) >= 66:
                    actual_amount = str(int(log_data[:66], 16))
                elif log_data:
                    actual_amount = str(int(log_data, 16))
            except (ValueError, TypeError):
                logger.warning(
                    f"Could not decode Transfer data for {tx_hash[:16]}...: {str(log_data)[:20]}"
                )

            # Verify amount matches
            if expected_amount and actual_amount:
                if actual_amount == expected_amount:
                    amount_match = True
                else:
                    logger.warning(
                        f"Amount mismatch for {tx_hash[:16]}...: "
                        f"expected {expected_amount} atoms, got {actual_amount} atoms on {chain_key}"
                    )
            elif actual_amount:
                # Check if amount matches any tool price
                for _tid, pricing in TOOL_PRICES.items():
                    if str(pricing.get("price_atoms")) == actual_amount:
                        amount_match = True
                        break

            break  # Found Transfer to our address

        if not transfer_found:
            return {
                "verified": False,
                "reason": f"No USDC Transfer to {our_address[:10]}... found in {tx_hash[:16]}...",
            }

        if not amount_match and expected_amount and actual_amount:
            return {
                "verified": False,
                "reason": f"Amount mismatch: expected {expected_amount} atoms, got {actual_amount} atoms",
            }

        # Mark as spent in Redis with 86400s (24h) TTL
        tool_name = tool_id or "unknown"
        if r:
            spent_key = f"x402:spent_tx:{tx_hash}"
            payment_data = {
                "chain": chain_key,
                "payer": payer or "unknown",
                "amount": actual_amount or amount_atoms or "0",
                "tool": tool_name,
                "timestamp": time.time(),
            }
            r.setex(spent_key, 86400, json.dumps(payment_data))

        # Persist to Supabase (non-blocking)
        try:
            import asyncio

            from app.routers.x402_dashboard import _persist_payment_to_supabase

            asyncio.create_task(
                _persist_payment_to_supabase(
                    tool=tool_name,
                    amount_atoms=str(actual_amount or amount_atoms or "0"),
                    chain=chain_key,
                    payer=payer or "unknown",
                    tx_hash=tx_hash,
                    status="fulfilled",
                )
            )
        except Exception:
            pass

        logger.info(
            f"Verified USDC payment: {tx_hash[:16]}... on {chain_key} "
            f"from {payer[:10] if payer else 'unknown'}... amount={actual_amount or 'unknown'} tool={tool_name}"
        )
        return {
            "verified": True,
            "chain": chain_key,
            "tx_hash": tx_hash,
            "payer": payer,
            "amount": actual_amount or amount_atoms,
            "method": "blockscout-verify",
        }

    except Exception as e:
        logger.error(f"Self-verify error: {e}")
        return {"verified": False, "reason": f"Verification error: {str(e)[:100]}"}


# ── Refund policy helpers ──


def _record_refundable_payment(
    tx_hash: str, chain: str, payer: str, amount: str, tool: str, reason: str
):
    """Record a refundable payment in Redis when a tool returns empty data.

    Stores in Redis key 'x402:refund:{tx_hash}' with 7-day TTL.
    Actual USDC refund is a manual process — we send USDC back from whichever chain has funds.
    """
    r = get_redis()
    if not r:
        logger.warning(f"Cannot record refund for {tx_hash}: Redis unavailable")
        return

    refund_key = f"x402:refund:{tx_hash}"
    refund_data = {
        "tx_hash": tx_hash,
        "chain": chain or "unknown",
        "payer": payer or "unknown",
        "amount_atoms": str(amount) if amount else "0",
        "tool": tool or "unknown",
        "reason": reason,
        "status": "refundable",  # refundable -> requested -> processing -> completed
        "flagged_at": time.time(),
        "requested_at": None,
        "processed_at": None,
    }
    try:
        r.setex(refund_key, 7 * 86400, json.dumps(refund_data))  # 7-day TTL
        logger.info(f"Recorded refundable payment: {tx_hash[:16]}... tool={tool} reason={reason}")
    except Exception as e:
        logger.error(f"Failed to record refund for {tx_hash}: {e}")


# ── Trial tracking (Redis-based) ──
_redis_client: _redis_types.Redis | None | bool = None  # bool = unavailable sentinel


async def x402_enforcement_middleware(request: Request, call_next) -> Response:
    """
    x402 payment enforcement with Chain of Responsibility pattern.
    Delegates early-exit checks (free paths, bot detection) to the
    enforcement chain, then handles payment/trial/DataBus routing.
    """
    path = request.url.path

    # Only intercept x402-tools
    if not path.startswith("/api/v1/x402-tools/"):
        return await call_next(request)

    # ── Chain of Responsibility ──────────────────────────────────
    # Early-exit checks: free paths, bot detection
    try:
        from app.routers.enforcement_chain import (
            SECURITY_HEADERS as CHAIN_SECURITY_HEADERS,
            BotDetector,
            EnforcementChain,
            EnforcementContext,
            FreeEndpointRouter,
        )

        chain = EnforcementChain().add(FreeEndpointRouter()).add(BotDetector())

        ctx = EnforcementContext(request)
        ctx.path = path
        ctx.method = request.method
        ctx.user_agent = (request.headers.get("User-Agent", "") or "").lower()

        for step in chain._steps:
            result = await step.process(request, ctx)
            if result is not None:
                return result

    except ImportError:
        pass  # Chain module not available
    except Exception as e:
        logger.error(f"enforcement_chain_error: {e}")

    # ── Internal bypass: trial GET-to-POST re-dispatch ──────────────
    if request.headers.get("X-RMI-Internal") == "trial-granted":
        response = await call_next(request)
        response.headers["X-RMI-Trial"] = "true"
        remaining_hdr = request.headers.get("X-RMI-Trial-Remaining", "0")
        response.headers["X-RMI-Trial-Remaining"] = remaining_hdr
        for k, v in SECURITY_HEADERS.items():
            if k not in response.headers:
                response.headers[k] = v
        return response

    # ── Developer API Key Check ─────────────────────────────────
    try:
        from app.routers.developer_tier import check_developer_key

        dev_result = await check_developer_key(request)
        if dev_result is not None:
            if dev_result.get("deny"):
                status_code = dev_result.get("status_code", 429)
                return JSONResponse(
                    status_code=status_code,
                    content={
                        "error": dev_result.get("error", "rate_limit_exceeded"),
                        "tier": dev_result.get("tier", "free"),
                        "message": dev_result.get("error", "Rate limit exceeded"),
                        "daily_limit": dev_result.get("daily_limit"),
                        "calls_today": dev_result.get("calls_today"),
                        "upgrade_url": "https://rugmunch.io/mcp-docs#pricing",
                    },
                    headers={
                        **SECURITY_HEADERS,
                        "X-RMI-Tier": dev_result.get("tier", "free"),
                        "X-RMI-Remaining": str(dev_result.get("remaining_today", 0)),
                        "Retry-After": str(dev_result.get("retry_after", 60)),
                    },
                )
            request.state.developer_key = True
            request.state.developer_tier = dev_result.get("tier", "free")
            request.state.developer_email = dev_result.get("email", "")
            response = await call_next(request)
            response.headers["X-RMI-Tier"] = dev_result.get("tier", "free")
            response.headers["X-RMI-Remaining"] = str(dev_result.get("remaining_today", 0))
            return response
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Developer key check failed: {e}")

    # ── Burst protection: rate limit rapid requests ───────────────
    client_id = get_client_id(request)
    # Extract tool name early for burst tracking
    path_tool_id = path.rstrip("/").split("/")[-1] if path.count("/") >= 4 else "unknown"
    r = get_redis()
    if r and re.match(r"^[a-zA-Z0-9_]+$", path_tool_id):
        burst_key = f"x402:burst:{client_id}:{path_tool_id}"
        try:
            burst_count = int(r.get(burst_key) or 0)
            BURST_LIMIT = int(os.getenv("X402_BURST_LIMIT", "5"))
            BURST_WINDOW = int(os.getenv("X402_BURST_WINDOW", "60"))
            if burst_count >= BURST_LIMIT:
                logger.warning(
                    f"Burst limit: {client_id} hit {burst_count} requests in {BURST_WINDOW}s"
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": f"Rate limited. Max {BURST_LIMIT} requests per {BURST_WINDOW}s per tool.",
                        "retry_after": BURST_WINDOW,
                    },
                    headers={**SECURITY_HEADERS, "Retry-After": str(BURST_WINDOW)},
                )
            pipe = r.pipeline()
            pipe.incr(burst_key)
            if burst_count == 0:
                pipe.expire(burst_key, BURST_WINDOW)
            pipe.execute()
        except Exception:
            pass  # Don't block on burst tracking errors

    # Free paths already checked above — flow continues to paid tool enforcement
    # Extract tool name
    tool_id = path.rstrip("/").split("/")[-1]

    # Input validation: reject tool IDs with suspicious characters
    if not re.match(r"^[a-zA-Z0-9_]+$", tool_id):
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid tool identifier"},
            headers=SECURITY_HEADERS,
        )

    client_id = get_client_id(request)

    # ── Payment header parsing: support both v1 (x-pay) and v2 (PAYMENT-SIGNATURE) ──
    # v2 specifies PAYMENT-SIGNATURE header (base64 JSON), v1 uses X-Pay or x-pay
    pay_sig = request.headers.get("PAYMENT-SIGNATURE", "") or request.headers.get(
        "Payment-Signature", ""
    )
    x_pay = request.headers.get("x-pay", "") or request.headers.get("X-Pay", "")

    # Reject oversized payment headers (DoS protection)
    if pay_sig and len(pay_sig) > 16384:
        return JSONResponse(
            status_code=400,
            content={"error": "Payment header too large"},
            headers=SECURITY_HEADERS,
        )
    if x_pay and len(x_pay) > 8192:
        return JSONResponse(
            status_code=400,
            content={"error": "Payment header too large"},
            headers=SECURITY_HEADERS,
        )

    # Parse payment payload from whichever header is present
    # v2 PAYMENT-SIGNATURE takes priority over v1 x-pay
    payload = None
    if pay_sig:
        payload = parse_x_pay_header(pay_sig)  # parse_x_pay handles base64 too
        if payload:
            # v2 spec: payload should have top-level x402Version, accepted, payload, resource
            # Normalize: make sure "accepted" is available at top level for our verifier
            if "accepted" not in payload and "payload" in payload:
                # This is a v2 PaymentPayload format — extract payment info
                pass  # The verify_payment_via_router already handles this format
    elif x_pay:
        payload = parse_x_pay_header(x_pay)

        if payload:
            # Validate payment payload structure
            if not isinstance(payload, dict):
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid payment payload structure"},
                    headers=SECURITY_HEADERS,
                )
            accepted = payload.get("accepted", {})
            if not isinstance(accepted, dict):
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid accepted payment structure"},
                    headers=SECURITY_HEADERS,
                )
            result = await verify_payment_via_router(payload)
            if result.get("verified"):
                # Payment OK — attach info and proceed
                request.state.x402_verified = True
                request.state.x402_payer = result.get("payer", "")
                request.state.x402_chain = result.get("chain", "")
                request.state.x402_tx_hash = result.get("tx_hash", "")
                request.state.x402_amount = result.get("amount", "")
                request.state.x402_method = result.get("method", "")

                # Track facilitator payment success for health monitoring
                facilitator = result.get("method", result.get("facilitator", ""))
                if facilitator:
                    try:
                        from app.routers.facilitator_health import record_payment_success

                        record_payment_success(facilitator, True)
                    except Exception:
                        pass

                response = await call_next(request)
                response.headers["X-RMI-Payment"] = "verified"

                # ── x402 v2: PAYMENT-RESPONSE header (base64 SettlementResponse) ──
                # The v2 spec requires a PAYMENT-RESPONSE header on 200 responses
                # after successful settlement, containing base64 JSON:
                # {success: true, transaction: "0x...", network: "eip155:8453", payer: "0x..."}
                settlement_response = {
                    "success": True,
                    "transaction": result.get("tx_hash", ""),
                    "network": CHAIN_USDC.get(result.get("chain", ""), {}).get(
                        "network", result.get("chain", "")
                    ),
                    "payer": result.get("payer", ""),
                }
                if result.get("amount"):
                    settlement_response["amount"] = result.get("amount")
                try:
                    payment_response_b64 = _base64.b64encode(
                        json.dumps(settlement_response, separators=(",", ":")).encode()
                    ).decode("ascii")
                    response.headers["PAYMENT-RESPONSE"] = payment_response_b64
                except Exception:
                    pass  # Don't fail on settlement header encoding errors

                # Add security headers to successful responses too
                for k, v in SECURITY_HEADERS.items():
                    if k not in response.headers:
                        response.headers[k] = v

                # ── Lightweight refund check (no body reading) ──
                # Body-parsing for empty data detection was removed because it:
                #   a) Breaks streaming responses
                #   b) Reads entire response into memory
                #   c) Duplicates logic that belongs in tool handlers
                # Instead: flag HTTP errors for refund. Empty-data detection
                # is handled by tool handlers setting X-RMI-Result header.
                if response.status_code >= 400:
                    _record_refundable_payment(
                        tx_hash=request.state.x402_tx_hash or "unknown",
                        chain=request.state.x402_chain,
                        payer=request.state.x402_payer,
                        amount=request.state.x402_amount,
                        tool=tool_id,
                        reason=f"HTTP {response.status_code} error response",
                    )
                    response.headers["X-RMI-Refund-Flagged"] = "true"
                    logger.info(
                        f"Flagged payment for refund: tx={request.state.x402_tx_hash} "
                        f"tool={tool_id} reason=HTTP_{response.status_code}"
                    )

                return response

            # Payment verification failed — track failure for health monitoring
            facilitator = result.get("method", result.get("facilitator", ""))
            if facilitator:
                try:
                    from app.routers.facilitator_health import record_payment_success

                    record_payment_success(facilitator, False)
                except Exception:
                    pass

    # No valid payment — check free trials
    try:
        can_trial, remaining = check_trial(tool_id, client_id, consume=False)
    except Exception as e:
        logger.error(f"Trial check failed for {tool_id}: {e}")
        can_trial, remaining = False, 0

    if can_trial:
        # ── Consume the trial now (atomic INCR in Redis) ──
        # The pre-check above used consume=False; now we actually consume it
        try:
            _, remaining_after = check_trial(tool_id, client_id, consume=True)
            remaining = remaining_after
        except Exception as e:
            logger.error(f"Trial consumption failed for {tool_id}/{client_id}: {e}")

        # Allow free trial execution
        # ── Execute the tool directly via DataBus caching shield ──
        # This eliminates the fragile httpx GET-to-POST roundtrip.
        # All 127 tools are available through the DataBus — no need for
        # internal HTTP bouncing through POST-only route handlers.
        try:
            from app.caching_shield.tool_data import td

            # Build params from request (query params for GET, JSON body for POST)
            params = {}
            if request.method == "GET" and request.query_params:
                params = dict(request.query_params)
            elif request.method == "POST":
                try:
                    params = await request.json()
                except Exception:
                    params = {}

            result = await td.call_tool(tool_id, params)

            if result is not None:
                # Trial execution succeeded — return the result
                # Use raw Response (not JSONResponse) to avoid BaseHTTPMiddleware
                # body_iterator consumption bug when returning from middleware directly.
                response_body = json.dumps(result, default=str)
                response = Response(
                    content=response_body,
                    status_code=200,
                    media_type="application/json",
                    headers={
                        "X-RMI-Trial": "true",
                        "X-RMI-Trial-Remaining": str(remaining),
                        **SECURITY_HEADERS,
                    },
                )
                return response
            else:
                # DataBus returned None — tool not available via DataBus.
                # For GET requests: try httpx GET-to-POST conversion.
                # For POST requests: fall through to original route handler.
                if request.method == "GET" and request.query_params:
                    import httpx

                    query_params = dict(request.query_params)
                    base_url = str(request.url).split("?")[0]
                    try:
                        async with httpx.AsyncClient(timeout=30) as client:
                            resp = await client.post(
                                base_url,
                                json=query_params,
                                headers={
                                    "Content-Type": "application/json",
                                    "X-RMI-Internal": "trial-granted",
                                    "X-RMI-Trial": "true",
                                    "X-RMI-Trial-Remaining": str(remaining),
                                    "X-Device-Id": request.headers.get("x-device-id", ""),
                                },
                            )
                            if resp.status_code == 404:
                                # Tool has no POST route handler (DataBus-only tool that failed).
                                # Trial was consumed — return 200 with "no data" result.
                                # The client got their free call, data just wasn't available.
                                return Response(
                                    content=json.dumps(
                                        {
                                            "tool": tool_id,
                                            "result": "no_data_available",
                                            "trial": True,
                                            "trial_remaining": remaining,
                                            "message": "Free trial used. Data not available for this address — try a real token address.",
                                        }
                                    ),
                                    status_code=200,
                                    media_type="application/json",
                                    headers={
                                        "X-RMI-Trial": "true",
                                        "X-RMI-Trial-Remaining": str(remaining),
                                        **SECURITY_HEADERS,
                                    },
                                )
                            response = Response(
                                content=resp.content,
                                status_code=resp.status_code,
                                media_type=resp.headers.get("content-type", "application/json"),
                                headers={
                                    k: v
                                    for k, v in resp.headers.items()
                                    if k.lower() not in ("content-length", "transfer-encoding")
                                },
                            )
                    except Exception:
                        # httpx failed — return "no data" response (trial was consumed)
                        return Response(
                            content=json.dumps(
                                {
                                    "tool": tool_id,
                                    "result": "no_data_available",
                                    "trial": True,
                                    "trial_remaining": remaining,
                                }
                            ),
                            status_code=200,
                            media_type="application/json",
                            headers={
                                "X-RMI-Trial": "true",
                                "X-RMI-Trial-Remaining": str(remaining),
                                **SECURITY_HEADERS,
                            },
                        )
                else:
                    # POST request or GET without params — try call_next
                    response = await call_next(request)
                    # If call_next returns 404, the tool has no POST route handler.
                    # Trial was consumed — return "no data" response instead of 404.
                    if response.status_code == 404:
                        return Response(
                            content=json.dumps(
                                {
                                    "tool": tool_id,
                                    "result": "no_data_available",
                                    "trial": True,
                                    "trial_remaining": remaining,
                                    "message": "Free trial used. Data not available for this address — try a real token address.",
                                }
                            ),
                            status_code=200,
                            media_type="application/json",
                            headers={
                                "X-RMI-Trial": "true",
                                "X-RMI-Trial-Remaining": str(remaining),
                                **SECURITY_HEADERS,
                            },
                        )
                    response.headers["X-RMI-Trial"] = "true"
                    response.headers["X-RMI-Trial-Remaining"] = str(remaining)
                    for k, v in SECURITY_HEADERS.items():
                        if k not in response.headers:
                            response.headers[k] = v
                    return response

        except ImportError:
            logger.warning("DataBus caching shield not available, falling back to call_next")
            response = await call_next(request)
            response.headers["X-RMI-Trial"] = "true"
            response.headers["X-RMI-Trial-Remaining"] = str(remaining)
            for k, v in SECURITY_HEADERS.items():
                if k not in response.headers:
                    response.headers[k] = v
            return response
        except Exception as e:
            logger.error(f"Trial execution via DataBus failed for {tool_id}: {e}")
            # Trial consumed but execution failed — refund the trial
            try:
                r = get_redis()
                if r:
                    key = f"x402:trial:{client_id}:{tool_id}"
                    r.decr(key)  # Roll back the consumption
            except Exception:
                pass
            # Return 402 — trial should be refunded since we couldn't execute
            return build_402_response(tool_id, client_id=client_id)

    # No trials left — return 402
    return build_402_response(tool_id, client_id=client_id)


# ── Discovery Endpoint ──
from fastapi import APIRouter

discovery_router = APIRouter()
# ── Main enforcement router (wrapper) ──
router = APIRouter(prefix="/api/v1/x402", tags=["x402 Enforcement"])

# Note: discovery_router is included on the app directly in main.py at root path
# so .well-known/x402 resolves at /.well-known/x402 (x402 spec requirement)


def _build_discovery_response():
    """Build the full discovery response with all 13 chains and active facilitators."""
    tools = {}
    for tool_id, pricing in TOOL_PRICES.items():
        try:
            price_atoms = str(pricing.get("price_atoms", "10000"))
            price_usd = float(pricing.get("price_usd", 0.01))
            trial_free = int(pricing.get("trial_free", 1))
            category = pricing.get("category", "analysis")
        except (ValueError, TypeError):
            continue

        requirements = []
        for chain_key, cfg in CHAIN_USDC.items():
            method = cfg["method"]
            pay_to = _resolve_pay_to(method)
            asset = _resolve_asset(cfg)
            extra = _build_extra(cfg, tool_id, chain_key, pay_to, method)

            req = {
                "scheme": "exact",
                "network": cfg["network"],
                "asset": asset,
                "amount": price_atoms,
                "payTo": pay_to,
                "maxTimeoutSeconds": 180,
                "extra": extra,
                "facilitators": cfg.get("facilitators", []),
            }
            if "tokens" in cfg:
                req["supportedTokens"] = list(cfg["tokens"].keys())
            requirements.append(req)

        desc = pricing.get("description", "")
        if not desc:
            _TOOL_DESCRIPTIONS = {
                "forensic_valuation": "Institutional-grade token valuation — DCF intrinsic value, comparable analysis with outlier detection, scam probability scoring",
                "osint_identity_hunt": "Cross-platform OSINT — hunt usernames across 400+ networks, domain intelligence, stealth page capture",
                "investigation_report": "Full investigation report — on-chain forensics, financial valuation, OSINT, scam scoring in one deliverable",
                "forensic_pack": "Forensic Investigation Pack — valuation + OSINT + report at 33% discount",
            }
            desc = _TOOL_DESCRIPTIONS.get(tool_id, f"{tool_id} — crypto intelligence tool")
        tools[tool_id] = {
            "description": desc,
            "price_usd": price_usd,
            "category": category,
            "trial_free": trial_free,
            "trial_description": f"{trial_free} free calls before payment required",
            "requirements": requirements,
        }

    # Build spec-compliant supportedNetworks array
    supported_networks = []
    for ck, cv in CHAIN_USDC.items():
        net = {
            "network": cv["network"],
            "name": CHAIN_NAMES.get(ck, ck),
            "chainId": cv.get("chain_id"),
            "currency": cv.get("name", "USD Coin"),
            "facilitators": cv.get("facilitators", []),
        }
        if "tokens" in cv:
            net["tokens"] = dict(cv["tokens"].items())
        supported_networks.append(net)

    # Build spec-compliant paymentFacilitators array
    facilitator_map = {
        "coinbase_cdp": {
            "name": "Coinbase CDP",
            "description": "Fee-free Base + Solana USDC via Coinbase Developer Platform",
            "url": "https://cdp.coinbase.com",
        },
        "payai": {
            "name": "PayAI",
            "description": "Base + Solana USDC, deferred settlement",
            "url": "https://payai.network",
        },
        "primev": {
            "name": "Primev",
            "description": "Fee-free Ethereum via mev-commit preconfirmations",
            "url": "https://primev.xyz",
        },
        "cloudflare_x402": {
            "name": "Cloudflare x402",
            "description": "Base Sepolia + Ethereum fallback",
            "url": "https://x402.org",
        },
        "eip7702": {
            "name": "EIP-7702 Universal",
            "description": "Universal EVM — BSC, Polygon, Avalanche, Fantom, Gnosis, Arbitrum, Optimism, Base",
            "url": "https://eips.ethereum.org/EIPS/eip-7702",
        },
        "tron_selfverify": {
            "name": "TRON Self-Verify",
            "description": "TRON USDT/USDC/USDD — self-verified via TronGrid (fee-free)",
            "url": "https://trongrid.io",
        },
        "bitcoin_selfverify": {
            "name": "Bitcoin Self-Verify",
            "description": "Bitcoin BTC — self-verified via Mempool.space (fee-free, 1-conf)",
            "url": "https://mempool.space",
        },
        "asterpay": {
            "name": "AsterPay",
            "description": "EUR/SEPA European off-ramp",
            "url": "https://asterpay.io",
        },
    }
    payment_facilitators = []
    for fk, fv in facilitator_map.items():
        fac_networks = [n["network"] for n in supported_networks if fk in n.get("facilitators", [])]
        payment_facilitators.append(
            {
                "name": fv["name"],
                "description": fv["description"],
                "url": fv["url"],
                "supportedNetworks": fac_networks,
            }
        )

    return {
        "x402": {
            "version": "2",
            "protocol": "x402",
            "description": "Rug Munch Intelligence (RMI) — Multi-chain x402 payment system. Crypto scam detection, market analysis, and security intelligence via micropayments.",
            "trial_policy": "1 free trial per tool without wallet. Connect wallet for 3 free calls per standard tool, 1 per premium tool.",
            "refund_policy": "Full refund if tool returns no data. Request within 48h via POST /api/v1/x402/refund with tx hash.",
            "facilitator_summary": {
                "coinbase_cdp": "Fee-free Base + Solana USDC via Coinbase Developer Platform",
                "payai": "Base + Solana USDC, deferred settlement",
                "primev": "Fee-free Ethereum via mev-commit preconfirmations",
                "cloudflare_x402": "Base Sepolia + Ethereum fallback",
                "eip7702": "Universal EVM — BSC, Polygon, Avalanche, Fantom, Gnosis, Arbitrum, Optimism, Base",
                "tron_selfverify": "TRON USDT/USDC/USDD — self-verified via TronGrid (fee-free)",
                "bitcoin_selfverify": "Bitcoin BTC — self-verified via Mempool.space (fee-free, 1-conf)",
                "asterpay": "EUR/SEPA European off-ramp",
                "x402_rs": "Self-hosted x402-rs — multi-chain (requires Docker)",
            },
        },
        "gateway_url": "https://mcp.rugmunch.io",
        "payment_endpoint": "https://mcp.rugmunch.io/api/v1/x402-tools",
        "supported_chains": list(CHAIN_USDC.keys()),
        "supportedNetworks": supported_networks,
        "paymentFacilitators": payment_facilitators,
        "chain_count": len(CHAIN_USDC),
        "facilitator_count": len(payment_facilitators),
        "total_tools": len(tools),
        "tools": tools,
    }


def _resolve_pay_to(method: str) -> str:
    _ensure_pay_addresses()
    if method == "payai":
        return SOL_PAY_TO or ""  # type: ignore[return-value]
    elif method == "bitcoin_selfverify":
        return os.getenv("X402_BTC_PAY_TO", "")
    elif method == "tron_selfverify":
        return os.getenv("X402_TRON_PAY_TO", "")
    elif method == "asterpay":
        return os.getenv("ASTERPAY_SEPA_IBAN", "")
    return EVM_PAY_TO or ""  # type: ignore[return-value]


def _resolve_asset(cfg: dict) -> str:
    asset = cfg.get("usdc", "")
    if not asset and "tokens" in cfg:
        first = next(iter(cfg["tokens"].values()), "")
        return first if first != "native" else "BTC"
    return asset


def _build_extra(cfg: dict, tool_id: str, chain_key: str, pay_to: str, method: str) -> dict:
    extra = {
        "name": cfg["name"],
        "version": cfg["version"],
        "tool": tool_id,
        "chain": chain_key,
    }
    if method == "local_eip712" and cfg.get("chain_id"):
        extra["domain"] = {
            "name": cfg["name"],
            "version": cfg["version"],
            "chainId": cfg["chain_id"],
            "verifyingContract": pay_to,
        }
    elif method == "payai":
        extra["feePayer"] = "2wKupLR9q6wXYppw8Gr2NvWxKBUqm4PPJKkQfoxHDBg4"
    elif method == "tron_selfverify":
        extra["tronNetwork"] = "mainnet"
        extra["trc20Tokens"] = cfg.get("tokens", {})
    elif method == "bitcoin_selfverify":
        extra["paymentNetwork"] = "bitcoin"
        extra["settlementChains"] = ["base", "solana"]
    elif method == "asterpay":
        extra["currency"] = "EUR"
        extra["sepa"] = True
    return extra


@discovery_router.get("/.well-known/x402")
async def x402_discovery():
    """x402 discovery endpoint — lists all tools, chains, payment requirements, and trial info.
    Response is cached for DISCOVERY_CACHE_TTL seconds to avoid rebuilding 7-chain requirements per request.
    """
    global _discovery_cache, _discovery_cache_time

    now = time.time()
    if _discovery_cache is not None and (now - _discovery_cache_time) < DISCOVERY_CACHE_TTL:
        return _discovery_cache

    _discovery_cache = _build_discovery_response()
    _discovery_cache_time = now
    return _discovery_cache


# ── Trial Status Endpoint ──
@discovery_router.get("/api/v1/x402/trial-status")
async def get_trial_status(request: Request):
    """Check remaining free trials for the current client"""
    client_id = get_client_id(request)
    trials = {}
    for tool_id, pricing in TOOL_PRICES.items():
        max_trials = pricing.get("trial_free", 3)
        if max_trials > 0:
            r = get_redis()
            if r:
                try:
                    used = int(r.get(f"x402:trial:{client_id}:{tool_id}") or 0)
                except Exception:
                    used = 0
            else:
                used = 0
            trials[tool_id] = {
                "max": max_trials,
                "used": used,
                "remaining": max(0, max_trials - used),
            }

    return {
        "client_id": client_id[:20] + "..." if len(client_id) > 20 else client_id,
        "trials": trials,
        "tools_with_trials": len(trials),
    }


# ── Revenue Read Endpoint ──
@discovery_router.get("/api/v1/x402/revenue")
async def get_revenue():
    """Read cumulative x402 revenue stats from Redis counters.
    _ensure_pay_addresses()

    Returns total revenue, daily breakdown, tool-level stats, and payment counts.
    Revenue counters are incremented by POST /api/v1/x402/receipt on successful payments.
    """
    r = get_redis()
    if not r:
        return JSONResponse(
            status_code=503,
            content={"error": "Redis unavailable, cannot read revenue"},
            headers=SECURITY_HEADERS,
        )

    try:
        total_revenue = float(r.get("x402:revenue:total") or 0)
        total_calls = 0
        tool_revenue = {}
        tool_calls = {}

        # Scan tool-level keys
        for key in r.scan_iter("x402:tool_revenue:*"):
            tool_id = key.decode().replace("x402:tool_revenue:", "")
            tool_revenue[tool_id] = float(r.get(key) or 0)

        for key in r.scan_iter("x402:tool_calls:*"):
            tool_id = key.decode().replace("x402:tool_calls:", "")
            count = int(r.get(key) or 0)
            tool_calls[tool_id] = count
            total_calls += count

        # Daily breakdown (last 30 days)
        daily = {}
        from datetime import datetime, timedelta

        for i in range(30):
            day = (datetime.utcnow() - timedelta(days=i)).strftime("%Y-%m-%d")
            val = float(r.get(f"x402:revenue:daily:{day}") or 0)
            if val > 0:
                daily[day] = val

        return {
            "total_revenue_usd": round(total_revenue, 2),
            "total_tool_calls": total_calls,
            "by_tool": {
                tid: {
                    "revenue_usd": round(tool_revenue.get(tid, 0), 2),
                    "calls": tool_calls.get(tid, 0),
                }
                for tid in set(list(tool_revenue.keys()) + list(tool_calls.keys()))
            },
            "daily": daily,
            "payment_wallets": {
                "evm": EVM_PAY_TO,
                "solana": SOL_PAY_TO,
                "tron": os.getenv("X402_TRON_PAY_TO", ""),
                "bitcoin": os.getenv("X402_BTC_PAY_TO", ""),
            },
        }
    except Exception as e:
        logger.error(f"Revenue read error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to read revenue: {str(e)[:100]}"},
            headers=SECURITY_HEADERS,
        )


# ── #8 Auto-Pricing Suggestions ──────────────────────────────────────


@discovery_router.get("/api/v1/x402/analytics/pricing-suggestions")
async def get_pricing_suggestions():
    """Auto-pricing suggestions based on conversion rates and usage data.

    Algorithm:
    - Pull call counts + revenue + trial counts per tool from Redis
    - Compute conversion rate = paid_calls / (paid_calls + trial_calls)
    - Tools with <5% conversion after 100+ trials → PRICE TOO HIGH → suggest decrease
    - Tools with >40% conversion → PRICE TOO LOW → suggest increase
    - Tools with 0 calls in 7 days → DEAD → suggest archive
    """
    r = get_redis()
    if not r:
        return JSONResponse(
            status_code=503,
            content={"error": "Redis unavailable"},
            headers=SECURITY_HEADERS,
        )
    try:
        suggestions = []
        for tool_id, pricing in TOOL_PRICES.items():
            current_price = pricing.get("price_usd", 0)
            trial_free = pricing.get("trial_free", 3)

            # Get usage stats from Redis
            paid_calls = int(r.get(f"x402:tool_calls:{tool_id}") or 0)
            trial_calls = int(r.get(f"x402:trial_calls:{tool_id}") or 0)
            revenue = float(r.get(f"x402:tool_revenue:{tool_id}") or 0)

            total_calls = paid_calls + trial_calls
            conversion_rate = paid_calls / total_calls if total_calls > 0 else 0
            avg_revenue_per_call = revenue / paid_calls if paid_calls > 0 else 0

            suggestion = None
            suggested_price = current_price

            if total_calls < 10:
                suggestion = "insufficient_data"
            elif total_calls > 100 and conversion_rate < 0.05:
                suggestion = "decrease_price"
                suggested_price = round(current_price * 0.6, 3)
            elif conversion_rate > 0.40:
                suggestion = "increase_price"
                suggested_price = round(current_price * 1.3, 3)
            elif total_calls > 50 and conversion_rate < 0.15:
                suggestion = "consider_decrease"
                suggested_price = round(current_price * 0.8, 3)
            elif paid_calls == 0 and trial_calls > trial_free * 10:
                suggestion = "price_barrier"
                suggested_price = round(current_price * 0.5, 3)
            elif revenue > 0 and avg_revenue_per_call < current_price * 0.5:
                suggestion = "check_pricing_consistency"

            suggestions.append(
                {
                    "tool_id": tool_id,
                    "current_price_usd": current_price,
                    "suggested_price_usd": suggested_price,
                    "suggestion": suggestion,
                    "stats": {
                        "paid_calls": paid_calls,
                        "trial_calls": trial_calls,
                        "total_calls": total_calls,
                        "conversion_rate": round(conversion_rate * 100, 1),
                        "revenue_usd": round(revenue, 4),
                        "avg_revenue_per_call": round(avg_revenue_per_call, 4),
                    },
                }
            )

        # Sort: actionable suggestions first
        suggestions.sort(
            key=lambda s: 0 if s["suggestion"] and s["suggestion"] != "insufficient_data" else 1
        )

        return {
            "suggestions": suggestions,
            "total_tools": len(suggestions),
            "actionable": sum(
                1 for s in suggestions if s["suggestion"] and s["suggestion"] != "insufficient_data"
            ),
        }
    except Exception as e:
        logger.error(f"Pricing suggestions error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)[:100]},
            headers=SECURITY_HEADERS,
        )


# ── Refund Request Endpoint ──
@router.post("/refund")
async def request_refund(request: Request):
    """Request a refund for a paid tool that returned no data.

    Validates:
    (a) tx exists in our payment records (x402:spent_tx or x402:refund)
    (b) tool returned no data or error (already flagged as refundable)
    (c) within 48h of original payment

    If valid, records the refund request. Actual USDC refund is manual.

    POST body: {
        "tx_hash": "0x...",
        "payer": "0x..." (optional, for verification),
        "reason": "Tool returned empty data" (optional)
    }
    """
    try:
        body = await request.json()
        tx_hash = body.get("tx_hash", "").strip()
        requester_payer = body.get("payer", "").strip()
        requester_reason = body.get("reason", "").strip()

        if not tx_hash:
            return JSONResponse(
                status_code=400,
                content={"error": "tx_hash is required"},
                headers=SECURITY_HEADERS,
            )

        r = get_redis()
        if not r:
            return JSONResponse(
                status_code=503,
                content={"error": "Redis unavailable, cannot process refund request"},
                headers=SECURITY_HEADERS,
            )

        # (a) Check if tx exists in our payment records
        spent_data = r.get(f"x402:spent_tx:{tx_hash}")
        existing_refund = r.get(f"x402:refund:{tx_hash}")

        if not spent_data and not existing_refund:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "Transaction not found in payment records",
                    "tx_hash": tx_hash[:16] + "...",
                },
                headers=SECURITY_HEADERS,
            )

        # If there's already a refund record, check its status
        if existing_refund:
            try:
                refund_info = json.loads(existing_refund)
                status = refund_info.get("status", "unknown")
                if status in ("requested", "processing", "completed"):
                    return {
                        "status": "already_requested",
                        "refund_status": status,
                        "tx_hash": tx_hash[:16] + "...",
                        "message": f"Refund already {status}",
                        "amount_atoms": refund_info.get("amount_atoms"),
                        "chain": refund_info.get("chain"),
                    }
            except (json.JSONDecodeError, TypeError):
                pass

        # (b) Check if payment was flagged as refundable (tool returned no data/error)
        refund_reason = requester_reason or "User-requested refund"
        refund_chain = "unknown"
        refund_payer = requester_payer or "unknown"
        refund_amount = "0"
        payment_timestamp = None

        if existing_refund:
            try:
                refund_info = json.loads(existing_refund)
                if refund_info.get("status") == "refundable":
                    refund_reason = refund_info.get("reason") or refund_reason
                    refund_chain = refund_info.get("chain", "unknown")
                    refund_payer = refund_info.get("payer", requester_payer)
                    refund_amount = refund_info.get("amount_atoms", "0")
                    payment_timestamp = refund_info.get("flagged_at")
            except (json.JSONDecodeError, TypeError):
                pass
        elif spent_data:
            # Payment exists but wasn't auto-flagged — user is requesting manually
            # Allow if within 48h window and user provides a reason
            try:
                spent_info = json.loads(spent_data)
                refund_chain = spent_info.get("chain", "unknown")
                refund_payer = spent_info.get("payer", requester_payer)
                refund_amount = spent_info.get("amount", "0")
                payment_timestamp = spent_info.get("timestamp")
            except (json.JSONDecodeError, TypeError):
                # Old format: spent_data was just the chain name string
                refund_chain = spent_data if isinstance(spent_data, str) else "unknown"

        # (c) Verify within 48h of original payment
        if payment_timestamp:
            hours_since_payment = (time.time() - float(payment_timestamp)) / 3600
            if hours_since_payment > 48:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "Refund window expired (48h)",
                        "hours_since_payment": round(hours_since_payment, 1),
                        "tx_hash": tx_hash[:16] + "...",
                    },
                    headers=SECURITY_HEADERS,
                )

        # Record the refund request
        refund_key = f"x402:refund:{tx_hash}"
        refund_data = {
            "tx_hash": tx_hash,
            "chain": refund_chain,
            "payer": refund_payer,
            "amount_atoms": str(refund_amount),
            "tool": "unknown",
            "reason": refund_reason,
            "status": "requested",
            "flagged_at": payment_timestamp,
            "requested_at": time.time(),
            "processed_at": None,
        }
        r.setex(refund_key, 7 * 86400, json.dumps(refund_data))  # 7-day TTL

        logger.info(
            f"Refund requested: tx={tx_hash[:16]}... chain={refund_chain} "
            f"amount={refund_amount} reason={refund_reason}"
        )

        return {
            "status": "requested",
            "tx_hash": tx_hash[:16] + "...",
            "chain": refund_chain,
            "amount_atoms": str(refund_amount),
            "reason": refund_reason,
            "message": "Refund request recorded. Manual processing within 48h. You will receive USDC back on the original payment chain.",
            "estimated_processing": "24-48 hours",
        }

    except Exception as e:
        logger.error(f"Refund request error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Refund request failed: {str(e)[:100]}"},
            headers=SECURITY_HEADERS,
        )
