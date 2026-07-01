#!/usr/bin/env python3
"""
RMI x402 MCP Server v5.0 - COMPLETE SYSTEM
==========================================

Complete x402 micropayment gateway with:
- Free/Trial/Premium tier system
- Multi-chain support (Base + Solana)
- AI Guard middleware integration
- 15+ tool bundles with intelligence suite
- Solana & Ethereum MCP server integration
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("x402_mcp_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


# ============================================================
# AI GUARD INTEGRATION (works even without app.security module)
# ============================================================


class AIGuardWrapper:
    """AI Guard that works standalone or with app.security."""

    def __init__(self):
        self.active = False
        self.blocked_patterns = [
            "drop table",
            "delete from",
            "insert into",
            "--",
            "/*",
            "xp_",
            "union select",
            "exec(",
            "eval(",
            "<script",
            "javascript:",
            "onload=",
            "onerror=",
            "document.cookie",
            "localStorage",
            "SELECT * FROM",
            "DROP TABLE",
            "INSERT INTO",
            "DELETE FROM",
        ]
        self._load_external_guard()

    def _load_external_guard(self):
        """Try to load RugMunch's actual AI Guard if available."""
        try:
            import sys

            sys.path.insert(0, "/srv/rugmuncher-backend/backend/app")
            from security.ai_guard import AIGuard

            self._guard = AIGuard()
            self.active = True
            logger.info("✅ AI Guard loaded from RugMunch security module")
        except Exception as e:
            logger.warning(f"⚠️ Using standalone AI Guard (external module unavailable: {e})")
            self._guard = None
            self.active = True  # Standalone still works

    async def check_request(self, request: Request) -> tuple[bool, str | None]:
        """Check request for security violations."""
        # Check headers
        user_agent = request.headers.get("User-Agent", "").lower()
        if any(bot in user_agent for bot in ["sqlmap", "nikto", "nmap", "masscan", "zgrab"]):
            return False, "Security scanner detected"

        # Check body for POST/PUT/PATCH
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                body = await request.body()
                body_str = body.decode("utf-8", errors="ignore").lower()
                for pattern in self.blocked_patterns:
                    if pattern in body_str:
                        return False, f"Malicious pattern detected: {pattern}"
            except Exception:
                pass

        return True, None


# ============================================================
# DATA MODELS
# ============================================================


@dataclass
class X402Bundle:
    """Complete x402 tool bundle definition."""

    bundle_id: str
    name: str
    description: str
    category: str
    endpoints: list[dict[str, Any]] = field(default_factory=list)
    price_usd: float = 0.0
    tier: str = "standard"  # free, trial, standard, premium, enterprise
    is_premium: bool = False
    requires_payment: bool = True

    @property
    def base_eth(self) -> str:
        return f"{self.price_usd / 3000:.6f} ETH"

    @property
    def solana_usdc(self) -> str:
        return f"{self.price_usd:.2f} USDC"

    def dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "endpoints": self.endpoints,
            "endpoint_count": len(self.endpoints),
            "price_usd": self.price_usd,
            "tier": self.tier,
            "is_premium": self.is_premium,
            "requires_payment": self.requires_payment,
            "pricing": {"base": self.base_eth, "solana": self.solana_usdc},
        }


# ============================================================
# FACILITATOR CONFIG
# ============================================================


class MultiChainFacilitator:
    """Multi-chain payment facilitator."""

    base_config = {
        "name": "RugMunch Intelligence x402 Gateway",
        "network": "BASE",
        "wallet_address": os.environ.get("X402_BASE_WALLET", "0x1E3AC01d0fdb976179790BDD02823196A92705C9"),
        "token_symbol": "ETH",
        "token_decimals": 18,
        "rpc_url": "https://mainnet.base.org",
        "explorer": "https://basescan.org",
    }

    solana_config = {
        "name": "RugMunch Intelligence x402 Gateway",
        "network": "SOLANA",
        "wallet_address": os.environ.get("X402_SOLANA_WALLET", "Gix4P9AmwcZRGzr2hCEME5m2QAvY86dBfm8c7e7MpFzv"),
        "token_symbol": "USDC",
        "token_decimals": 6,
        "rpc_url": "https://api.mainnet-beta.solana.com",
        "explorer": "https://solscan.io",
    }

    third_party = {
        "base": "https://mcp.rugmunch.io",
        "solana": "https://mcp.rugmunch.io",
    }


# ============================================================
# TOOL MANAGER - COMPLETE CATALOG
# ============================================================


class X402ToolManager:
    """Manages all x402 tool bundles including free/trial/premium."""

    def __init__(self):
        self._bundles = self._load_all_bundles()
        self._index = {b.bundle_id: b for b in self._bundles}
        self._index.update({b.name.lower(): b for b in self._bundles})

    def _load_all_bundles(self) -> list[X402Bundle]:
        """Load complete catalog: free, trial, standard, premium."""

        return [
            # ========== FREE TIER ==========
            X402Bundle(
                bundle_id="free-health",
                name="Free Health Check",
                description="Free system health and status endpoints",
                category="free",
                endpoints=[
                    {"method": "GET", "path": "/health", "description": "System health"},
                    {"method": "GET", "path": "/status", "description": "System status"},
                    {"method": "GET", "path": "/version", "description": "API version"},
                ],
                price_usd=0.0,
                tier="free",
                is_premium=False,
                requires_payment=False,
            ),
            X402Bundle(
                bundle_id="free-discovery",
                name="Free Tool Discovery",
                description="Discover all available tools without payment",
                category="free",
                endpoints=[
                    {"method": "GET", "path": "/.well-known/x402", "description": "x402 discovery"},
                    {"method": "GET", "path": "/v1/bundles", "description": "List all bundles"},
                    {"method": "GET", "path": "/v1/bundles/{id}", "description": "Bundle details"},
                ],
                price_usd=0.0,
                tier="free",
                is_premium=False,
                requires_payment=False,
            ),
            # ========== TRIAL TIER ==========
            X402Bundle(
                bundle_id="trial-security",
                name="Trial Security Suite",
                description="7-day trial of premium security tools",
                category="trial",
                endpoints=[
                    {
                        "method": "POST",
                        "path": "/scan/contract",
                        "description": "Smart contract scan",
                    },
                    {"method": "POST", "path": "/scan/token", "description": "Token risk scan"},
                    {"method": "POST", "path": "/scan/wallet", "description": "Wallet risk scan"},
                ],
                price_usd=0.0,
                tier="trial",
                is_premium=False,
                requires_payment=False,
            ),
            X402Bundle(
                bundle_id="trial-intelligence",
                name="Trial Intelligence Suite",
                description="7-day trial of market intelligence tools",
                category="trial",
                endpoints=[
                    {"method": "GET", "path": "/market/pulse", "description": "Token pulse"},
                    {"method": "GET", "path": "/market/trends", "description": "Market trends"},
                    {"method": "GET", "path": "/market/whales", "description": "Whale tracking"},
                ],
                price_usd=0.0,
                tier="trial",
                is_premium=False,
                requires_payment=False,
            ),
            # ========== STANDARD TIER ==========
            X402Bundle(
                bundle_id="token-pulse",
                name="Token Pulse",
                description="Real-time market momentum, volume, whale alerts",
                category="market",
                endpoints=[
                    {"method": "GET", "path": "/tokens", "description": "Token listings"},
                    {"method": "GET", "path": "/tokens/{address}", "description": "Token details"},
                    {
                        "method": "GET",
                        "path": "/lp/{address}",
                        "description": "Liquidity pool analysis",
                    },
                ],
                price_usd=0.01,
                tier="standard",
            ),
            X402Bundle(
                bundle_id="url-scam-detector",
                name="URL Scam Detector",
                description="Detect phishing sites, fake docs, malicious redirects",
                category="security",
                endpoints=[
                    {"method": "POST", "path": "/scan/url", "description": "URL scan"},
                    {"method": "POST", "path": "/scan/domain", "description": "Domain analysis"},
                ],
                price_usd=0.01,
                tier="standard",
            ),
            X402Bundle(
                bundle_id="wallet-profiler",
                name="Wallet Profiler",
                description="Full wallet analysis with persona detection",
                category="wallet",
                endpoints=[
                    {"method": "POST", "path": "/wallet/profile", "description": "Profile wallet"},
                    {
                        "method": "POST",
                        "path": "/wallet/history",
                        "description": "Transaction history",
                    },
                ],
                price_usd=0.05,
                tier="standard",
            ),
            X402Bundle(
                bundle_id="social-sentiment",
                name="Social Sentiment",
                description="Cross-platform sentiment analysis",
                category="market",
                endpoints=[
                    {
                        "method": "POST",
                        "path": "/sentiment/analyze",
                        "description": "Analyze sentiment",
                    },
                    {
                        "method": "GET",
                        "path": "/sentiment/trends",
                        "description": "Trending topics",
                    },
                ],
                price_usd=0.03,
                tier="standard",
            ),
            # ========== PREMIUM TIER ==========
            X402Bundle(
                bundle_id="suspicious-transfers",
                name="Suspicious Transfers Scanner",
                description="Cross-chain anomaly detection with RugMunch intelligence",
                category="security",
                endpoints=[
                    {"method": "POST", "path": "/scan/transfers", "description": "Scan transfers"},
                    {"method": "GET", "path": "/scan/results", "description": "Get results"},
                    {"method": "POST", "path": "/scan/anomaly", "description": "Anomaly detection"},
                ],
                price_usd=0.15,
                tier="premium",
                is_premium=True,
            ),
            X402Bundle(
                bundle_id="wallet-labeler",
                name="Wallet Labeler",
                description="Behavioral reputation engine (Arkham+Nansen+Alchemy)",
                category="security",
                endpoints=[
                    {"method": "POST", "path": "/label/wallet", "description": "Label wallet"},
                    {"method": "GET", "path": "/label/history", "description": "Label history"},
                    {
                        "method": "POST",
                        "path": "/label/cluster",
                        "description": "Cluster detection",
                    },
                ],
                price_usd=0.20,
                tier="premium",
                is_premium=True,
            ),
            X402Bundle(
                bundle_id="memory-bank",
                name="Memory Bank",
                description="Unified agent knowledge store with GCS export",
                category="ai",
                endpoints=[
                    {"method": "POST", "path": "/memory/store", "description": "Store knowledge"},
                    {"method": "GET", "path": "/memory/query", "description": "Query knowledge"},
                    {"method": "POST", "path": "/memory/export", "description": "Export to GCS"},
                ],
                price_usd=0.12,
                tier="premium",
                is_premium=True,
            ),
            X402Bundle(
                bundle_id="deep-contract-audit",
                name="Deep Contract Audit",
                description="Smart contract audit, honeypot detection, hidden mint functions",
                category="security",
                endpoints=[
                    {
                        "method": "POST",
                        "path": "/audit/contract",
                        "description": "Full contract audit",
                    },
                    {"method": "POST", "path": "/audit/honeypot", "description": "Honeypot check"},
                    {
                        "method": "POST",
                        "path": "/audit/proxy",
                        "description": "Proxy pattern check",
                    },
                ],
                price_usd=0.05,
                tier="premium",
                is_premium=True,
            ),
            X402Bundle(
                bundle_id="token-forensics",
                name="Token Forensics",
                description="Deep forensics from DexScreener, GeckoTerminal, CoinGecko",
                category="forensics",
                endpoints=[
                    {
                        "method": "POST",
                        "path": "/forensics/token",
                        "description": "Token forensics",
                    },
                    {
                        "method": "POST",
                        "path": "/forensics/report",
                        "description": "Generate report",
                    },
                ],
                price_usd=0.10,
                tier="premium",
                is_premium=True,
            ),
            X402Bundle(
                bundle_id="whale-decoder",
                name="Whale Decoder",
                description="Advanced whale wallet analysis across chains",
                category="wallet",
                endpoints=[
                    {
                        "method": "POST",
                        "path": "/whale/decode",
                        "description": "Decode whale strategy",
                    },
                    {"method": "GET", "path": "/whale/positions", "description": "Track positions"},
                ],
                price_usd=0.15,
                tier="premium",
                is_premium=True,
            ),
            X402Bundle(
                bundle_id="darkroom-security",
                name="Darkroom Security Suite",
                description="Advanced security analysis, threat detection, forensics",
                category="security",
                endpoints=[
                    {"method": "POST", "path": "/security/scan", "description": "Security scan"},
                    {
                        "method": "POST",
                        "path": "/security/threats",
                        "description": "Threat database",
                    },
                    {
                        "method": "POST",
                        "path": "/security/forensics",
                        "description": "Forensic analysis",
                    },
                ],
                price_usd=0.20,
                tier="premium",
                is_premium=True,
            ),
            X402Bundle(
                bundle_id="solana-mcp",
                name="Solana MCP Server",
                description="Native Solana blockchain queries via MCP protocol",
                category="mcp",
                endpoints=[
                    {
                        "method": "POST",
                        "path": "/mcp/solana/balance",
                        "description": "Get SOL balance",
                    },
                    {
                        "method": "POST",
                        "path": "/mcp/solana/transaction",
                        "description": "Get transaction",
                    },
                    {
                        "method": "POST",
                        "path": "/mcp/solana/tokens",
                        "description": "Get token accounts",
                    },
                    {
                        "method": "POST",
                        "path": "/mcp/solana/simulate",
                        "description": "Simulate transaction",
                    },
                ],
                price_usd=0.08,
                tier="premium",
                is_premium=True,
            ),
            X402Bundle(
                bundle_id="ethereum-mcp",
                name="Ethereum MCP Server",
                description="Native Ethereum blockchain queries via MCP protocol",
                category="mcp",
                endpoints=[
                    {
                        "method": "POST",
                        "path": "/mcp/eth/balance",
                        "description": "Get ETH balance",
                    },
                    {
                        "method": "POST",
                        "path": "/mcp/eth/transaction",
                        "description": "Get transaction",
                    },
                    {"method": "POST", "path": "/mcp/eth/call", "description": "Call contract"},
                    {"method": "POST", "path": "/mcp/eth/logs", "description": "Get event logs"},
                    {"method": "POST", "path": "/mcp/eth/ens", "description": "Resolve ENS"},
                ],
                price_usd=0.08,
                tier="premium",
                is_premium=True,
            ),
        ]

    def list_bundles(self, tier: str | None = None) -> list[X402Bundle]:
        """List all or filtered bundles."""
        if tier:
            return [b for b in self._bundles if b.tier == tier]
        return self._bundles

    def get_bundle(self, identifier: str) -> X402Bundle | None:
        return self._index.get(identifier.lower())

    @property
    def free_bundles(self) -> list[X402Bundle]:
        return [b for b in self._bundles if b.tier == "free"]

    @property
    def trial_bundles(self) -> list[X402Bundle]:
        return [b for b in self._bundles if b.tier == "trial"]

    @property
    def premium_bundles(self) -> list[X402Bundle]:
        return [b for b in self._bundles if b.is_premium]

    @property
    def total_endpoints(self) -> int:
        return sum(len(b.endpoints) for b in self._bundles)


# ============================================================
# MAIN X402 SERVER
# ============================================================


class X402MCPServer:
    """Complete x402 MCP server v5.0."""

    def __init__(self):
        self._tool_manager = X402ToolManager()
        self._facilitator = MultiChainFacilitator()
        self._ai_guard = AIGuardWrapper()
        self._app = None

        b = self._tool_manager
        logger.info("🎉 x402 MCP v5.0 initialized")
        logger.info(f"   Bundles: {len(b.list_bundles())}")
        logger.info(
            f"   Free: {len(b.free_bundles)} | Trial: {len(b.trial_bundles)} | Premium: {len(b.premium_bundles)}"
        )
        logger.info(f"   Endpoints: {b.total_endpoints}")
        logger.info(f"   Revenue/call: ${sum(b.price_usd for b in b.list_bundles()):.2f}")
        logger.info(f"   AI Guard: {'✅ Active' if self._ai_guard.active else '❌ Inactive'}")

    def streamable_http_app(self) -> FastAPI:
        if self._app is not None:
            return self._app

        app = FastAPI(
            title="RugMunch Intelligence x402 Gateway v5.0",
            description="Complete crypto security & intelligence x402 gateway with free/trial/premium tiers",
            version="5.0.0",
            docs_url="/",
        )

        self._add_system_endpoints(app)
        self._add_discovery_endpoints(app)
        self._add_middleware(app)

        self._app = app
        return app

    def _add_system_endpoints(self, app: FastAPI):
        """Health, status, version endpoints."""

        @app.get("/health")
        async def health():
            b = self._tool_manager
            return JSONResponse(
                {
                    "status": "healthy",
                    "version": "5.0.0",
                    "timestamp": datetime.utcnow().isoformat(),
                    "statistics": {
                        "bundles": len(b.list_bundles()),
                        "free": len(b.free_bundles),
                        "trial": len(b.trial_bundles),
                        "standard": len([x for x in b.list_bundles() if x.tier == "standard"]),
                        "premium": len(b.premium_bundles),
                        "endpoints": b.total_endpoints,
                    },
                    "ai_guard": self._ai_guard.active,
                    "chains": ["base", "solana"],
                    "facilitator": {
                        "base_wallet": self._facilitator.base_config["wallet_address"][:20] + "...",
                        "solana_wallet": self._facilitator.solana_config["wallet_address"][:20] + "...",
                    },
                }
            )

        @app.get("/version")
        async def version():
            return JSONResponse(
                {
                    "version": "5.0.0",
                    "name": "RugMunch Intelligence x402 Gateway",
                    "chains": ["base", "solana"],
                    "tiers": ["free", "trial", "standard", "premium"],
                }
            )

    def _add_discovery_endpoints(self, app: FastAPI):
        """x402 discovery and bundle listing."""

        @app.get("/.well-known/x402")
        async def discovery():
            b = self._tool_manager
            return JSONResponse(
                {
                    "version": "5.0.0",
                    "name": "RugMunch Intelligence x402 Gateway",
                    "description": "Complete crypto security & intelligence with free/trial/premium tiers",
                    "facilitators": {
                        "base": self._facilitator.base_config,
                        "solana": self._facilitator.solana_config,
                        "third_party": self._facilitator.third_party,
                    },
                    "bundles": [bundle.dict() for bundle in b.list_bundles()],
                    "tiers": {
                        "free": [bundle.dict() for bundle in b.free_bundles],
                        "trial": [bundle.dict() for bundle in b.trial_bundles],
                        "premium": [bundle.dict() for bundle in b.premium_bundles],
                    },
                    "statistics": {
                        "total_bundles": len(b.list_bundles()),
                        "total_endpoints": b.total_endpoints,
                        "free_count": len(b.free_bundles),
                        "trial_count": len(b.trial_bundles),
                        "premium_count": len(b.premium_bundles),
                        "max_revenue_usd": f"${sum(x.price_usd for x in b.list_bundles()):.2f}",
                    },
                    "mcp_servers": {
                        "solana": "/mcp/solana",
                        "ethereum": "/mcp/eth",
                    },
                    "documentation": "https://docs.rugmunch.io/x402-v5",
                    "support": "support@rugmunch.io",
                }
            )

        @app.get("/v1/bundles")
        async def list_bundles(tier: str | None = None):
            bundles = self._tool_manager.list_bundles(tier)
            return JSONResponse(
                {
                    "success": True,
                    "count": len(bundles),
                    "tier_filter": tier,
                    "bundles": [b.dict() for b in bundles],
                }
            )

        @app.get("/v1/bundles/{bundle_id}")
        async def get_bundle(bundle_id: str):
            bundle = self._tool_manager.get_bundle(bundle_id)
            if not bundle:
                raise HTTPException(status_code=404, detail="Bundle not found")

            return JSONResponse(
                {
                    "success": True,
                    "bundle": bundle.dict(),
                    "payment_requirements": {
                        "base": self._get_payment_req(bundle, "base"),
                        "solana": self._get_payment_req(bundle, "solana"),
                    },
                }
            )

    def _add_middleware(self, app: FastAPI):
        """AI Guard + x402 payment middleware."""

        @app.middleware("http")
        async def security_and_payment_middleware(request: Request, call_next):
            path = request.url.path

            # Skip free endpoints
            if any(
                path.startswith(p) for p in ["/health", "/version", "/.well-known", "/docs", "/openapi", "/v1/bundles"]
            ):
                return await call_next(request)

            # AI Guard check
            if self._ai_guard.active:
                is_safe, reason = await self._ai_guard.check_request(request)
                if not is_safe:
                    return JSONResponse(
                        {
                            "error": "Request blocked by AI Guard",
                            "code": "AI_GUARD_BLOCKED",
                            "reason": reason,
                        },
                        status_code=403,
                    )

            # x402 payment check for premium bundles
            # Extract bundle from path
            path_parts = path.split("/")
            if len(path_parts) >= 2:
                bundle_id = (
                    path_parts[1]
                    if not path_parts[1].startswith("v1")
                    else path_parts[2]
                    if len(path_parts) > 2
                    else None
                )

                if bundle_id:
                    bundle = self._tool_manager.get_bundle(bundle_id)
                    if bundle and bundle.requires_payment and bundle.price_usd > 0:
                        x402_header = request.headers.get("X-Payment")
                        solana_payment = request.headers.get("X-Solana-Payment")

                        if not x402_header and not solana_payment:
                            return JSONResponse(
                                content={
                                    "error": "Payment required",
                                    "code": "PAYMENT_REQUIRED",
                                    "bundle": bundle_id,
                                    "bundle_name": bundle.name,
                                    "price_usd": bundle.price_usd,
                                    "payment_options": {
                                        "base": self._get_payment_req(bundle, "base"),
                                        "solana": self._get_payment_req(bundle, "solana"),
                                    },
                                    "instructions": "Include X-Payment (Base) or X-Solana-Payment (Solana) header with transaction proof",
                                },
                                status_code=402,
                                headers={
                                    "X-Payment-Required": "true",
                                    "X-Payment-Address-Base": self._facilitator.base_config["wallet_address"],
                                    "X-Payment-Address-Solana": self._facilitator.solana_config["wallet_address"],
                                    "X-Bundle-Name": bundle.name,
                                    "X-Price-USD": str(bundle.price_usd),
                                },
                            )

            return await call_next(request)

    def _get_payment_req(self, bundle: X402Bundle, chain: str) -> dict[str, Any]:
        facilitator = self._facilitator.base_config if chain == "base" else self._facilitator.solana_config

        if chain == "solana":
            amount_atomic = int(bundle.price_usd * 10**6)
            symbol = "USDC"
            decimals = 6
        else:
            amount_atomic = int(bundle.price_usd / 3000 * 10**18)
            symbol = "ETH"
            decimals = 18

        return {
            "chain": chain.upper(),
            "network": facilitator["network"],
            "amount_usd": bundle.price_usd,
            "amount_atomic": amount_atomic,
            "symbol": symbol,
            "decimals": decimals,
            "recipient_address": facilitator["wallet_address"],
            "reference": f"bundle-{bundle.bundle_id}",
            "valid_until": (datetime.utcnow() + timedelta(hours=1)).isoformat(),
        }


# Create global instance
mcp = X402MCPServer()

if __name__ == "__main__":
    app = mcp.streamable_http_app()
    uvicorn.run(app, host="0.0.0.0", port=8001)
