"""Dynamic MCP Tool Catalog — reads from gateway configs + external MCP servers
Expanded to include 200+ tools across 40+ services
"""

import logging
import os
import re

from fastapi import APIRouter

logger = logging.getLogger(__name__)

# Provider name sanitization map
_PROVIDER_MAP = {
    "DexScreener": "DEX",
    "CoinGecko": "Market Data",
    "DefiLlama": "DeFi Analytics",
    "Birdeye": "Token Discovery",
    "PumpFun": "Launch",
    "Pump.fun": "Launch",
    "GeckoTerminal": "DEX Pool",
    "DexPaprika": "Multi-Source DEX",
}

_SERVICE_MAP = {
    "dexscreener": "dex-analytics",
    "coingecko": "market-data",
    "defillama": "defi-analytics",
    "birdeye": "token-discovery",
    "pumpfun": "launch-platform",
    "geckoterminal": "dex-pool-data",
    "dexpaprika": "multi-source-dex",
    "dexscreener_new": "dex-analytics",
    "dexscreener_hot": "dex-analytics",
    "dexscreener_meme": "dex-analytics",
    "defillama_stablecoins": "defi-analytics",
    "defillama_bridges": "defi-analytics",
    "pumpfun_gainers": "launch-platform",
}


def _sanitize_tool(tool: dict) -> dict:
    """Remove upstream provider names from user-visible tool fields"""
    for field in ("name", "description"):
        if field in tool and isinstance(tool[field], str):
            val = tool[field]
            for old, new in _PROVIDER_MAP.items():
                val = val.replace(old, new)
            tool[field] = val
    if "service" in tool and isinstance(tool["service"], str):
        tool["service"] = _SERVICE_MAP.get(tool["service"], tool["service"])
    return tool


router = APIRouter(prefix="/api/v1/x402", tags=["x402-catalog"])

# Cache for tool catalog
_catalog_cache: dict = {}


def parse_gateway_tools(gateway_dir: str) -> list[dict]:
    """Parse RMI_TOOLS definitions from a gateway index.ts file.

    Handles nested braces by tracking brace depth."""
    index_path = os.path.join(gateway_dir, "index.ts")
    if not os.path.exists(index_path):
        return []

    with open(index_path) as f:
        content = f.read()

    tools = []

    # Find the RMI_TOOLS block
    rmi_start = content.find("const RMI_TOOLS")
    if rmi_start == -1:
        return []

    # Find opening brace of RMI_TOOLS
    brace_start = content.find("{", rmi_start)
    if brace_start == -1:
        return []

    # Find matching closing brace by tracking depth
    depth = 0
    pos = brace_start
    while pos < len(content):
        if content[pos] == "{":
            depth += 1
        elif content[pos] == "}":
            depth -= 1
            if depth == 0:
                break
        pos += 1

    rmi_block = content[brace_start : pos + 1]

    # Now parse individual tool definitions within the block
    # Tools look like:  tool_name: { name: "...", description: "...", ... }
    # We need to handle nested braces in extras

    # Split into tool entries by finding top-level 'word:' patterns
    tool_pattern = re.compile(r"(\w+):\s*\{", re.MULTILINE)

    for tm in tool_pattern.finditer(rmi_block):
        tool_id = tm.group(1)

        # Skip non-tool entries
        if tool_id in ("interface", "type", "export", "import", "const", "let", "var"):
            continue
        if tool_id.startswith("//") or tool_id.startswith("RMI_TOOLS"):
            continue

        # Find the matching closing brace for this tool
        tool_start = tm.end() - 1  # position of opening {
        depth = 0
        tool_end = tool_start
        for i in range(tool_start, len(rmi_block)):
            if rmi_block[i] == "{":
                depth += 1
            elif rmi_block[i] == "}":
                depth -= 1
                if depth == 0:
                    tool_end = i + 1
                    break

        block = rmi_block[tool_start:tool_end]

        # Extract fields
        name = re.search(r'name:\s*"([^"]*)"', block)
        desc = re.search(r'description:\s*"([^"]*)"', block)
        price = re.search(r'price:\s*"\$?([^"]*)"', block)
        atomic = re.search(r'priceAtomic:\s*"([^"]*)"', block)
        category = re.search(r'category:\s*"([^"]*)"', block)
        trial = re.search(r"trialFree:\s*(\d+)", block)
        method = re.search(r'method:\s*"([^"]*)"', block)

        if name and category:
            tools.append(
                {
                    "id": tool_id,
                    "name": name.group(1),
                    "description": desc.group(1) if desc else "",
                    "price": f"${price.group(1)}" if price else "$0",
                    "priceUsd": float(price.group(1)) if price else 0,
                    "priceAtomic": atomic.group(1) if atomic else "0",
                    "category": category.group(1).lower(),
                    "trialFree": int(trial.group(1)) if trial else 0,
                    "method": method.group(1) if method else "POST",
                    "service": "rmi-native",
                    "source": "native",
                }
            )

    return tools


def load_external_mcp_tools() -> list[dict]:
    """Load expanded external MCP tool definitions.

    Priority:
    1. Local expanded_mcp_catalog.py file (fast, offline)
    2. mcp-router.rugmunch.io/tools (live, always current)
    3. Returns empty list if both unavailable
    """
    base_dir = os.path.dirname(os.path.dirname(__file__))
    catalog_path = os.path.join(base_dir, "services", "expanded_mcp_catalog.py")

    # Try local file first
    if os.path.exists(catalog_path):
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location("expanded_mcp_catalog", catalog_path)
            if spec is None or spec.loader is None:
                raise ImportError("Cannot load spec for expanded_mcp_catalog")
            module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            tools = getattr(module, "EXTERNAL_MCP_TOOLS", [])
            if tools:
                logger.info(f"Loaded {len(tools)} tools from expanded_mcp_catalog.py")
                return tools
        except Exception as e:
            logger.warning(f"Failed to load expanded_mcp_catalog.py: {e}")

    # Fallback: fetch from mcp-router dynamically
    try:
        import json as _json
        import urllib.request

        url = "https://mcp.rugmunch.io/tools"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())

        tools = []
        # mcp-router returns service groups, each with tools
        if isinstance(data, dict):
            services = data.get("services", data.get("tools", data))
            if isinstance(services, list):
                for service in services:
                    service_name = service.get("name", service.get("service", "unknown"))
                    service_tools = service.get("tools", [])
                    if isinstance(service_tools, list):
                        for t in service_tools:
                            tool_id = t.get("name", t.get("id", ""))
                            if tool_id:
                                tools.append(
                                    {
                                        "id": tool_id,
                                        "name": t.get("description", tool_id),
                                        "description": t.get("description", f"MCP tool: {tool_id}"),
                                        "price": "$0.01",
                                        "priceUsd": 0.01,
                                        "category": "mcp-external",
                                        "trialFree": 3,
                                        "method": "MCP",
                                        "service": service_name,
                                        "source": "mcp-router",
                                        "chains": ["SOLANA", "BASE"],
                                    }
                                )
                    else:
                        # Flat tool list
                        for key, val in service.items():
                            if isinstance(val, dict) and "name" in val:
                                tools.append(
                                    {
                                        "id": key,
                                        "name": val.get("name", key),
                                        "description": val.get("description", ""),
                                        "price": "$0.01",
                                        "priceUsd": 0.01,
                                        "category": "mcp-external",
                                        "trialFree": 3,
                                        "method": "MCP",
                                        "service": service_name,
                                        "source": "mcp-router",
                                        "chains": ["SOLANA", "BASE"],
                                    }
                                )

        if tools:
            logger.info(f"Loaded {len(tools)} tools from mcp-router dynamically")
            return tools
    except Exception as e:
        logger.warning(f"Failed to fetch from mcp-router: {e}")

    return []


def discover_route_tools() -> list[dict]:
    """Discover tools from FastAPI route definitions"""
    tools = []
    backend_dir = os.path.dirname(__file__)
    skip = {
        "bundles",
        "discovery",
        "frameworks",
        "comprehensive_audit",
        "anthropic-tools",
        "gemini-tools",
        "langchain-tools",
        "openai-tools",
        "bundles/all_in_one",
        "bundles/intelligence_pack",
        "bundles/security_pack",
        "bundles/forensic_pack",
        "{tool_id}",
        "payment-methods",
    }

    # Load authoritative pricing/categories from TOOL_PRICES
    try:
        from app.routers.x402_enforcement import TOOL_PRICES
    except Exception:
        TOOL_PRICES = {}

    for fname in ["x402_tools.py", "x402_forensic_tools.py"]:
        fpath = os.path.join(backend_dir, fname)
        if not os.path.exists(fpath):
            continue
        with open(fpath) as f:
            content = f.read()
        # Find all route paths
        routes = re.findall(r'@router\.(?:get|post)\("\/([^"]+)"\)', content)
        for route in routes:
            if route in skip:
                continue
            # Use TOOL_PRICES as source of truth for pricing and category
            if route in TOOL_PRICES:
                tp = TOOL_PRICES[route]
                tools.append(
                    {
                        "id": route,
                        "name": tp.get("description", route.replace("_", " ").title()),
                        "description": tp.get("description", f"Tool: {route}"),
                        "price": f"${tp.get('price_usd', 0.01):.2f}",
                        "priceUsd": tp.get("price_usd", 0.01),
                        "category": tp.get("category", "analysis"),
                        "trialFree": tp.get("trial_free", 1),
                        "method": "POST",
                        "service": "rmi-native",
                        "source": "route",
                        "chains": [],
                    }
                )
                continue
            # Try to find docstring for description
            desc = f"Tool: {route}"
            doc_match = re.search(rf'@router\.(?:get|post)\("/{route}"\)[\s\S]*?"""([^"]*)"""', content)
            if doc_match:
                desc = doc_match.group(1).strip().split("\n")[0].strip()
            tools.append(
                {
                    "id": route,
                    "name": route.replace("_", " ").title(),
                    "description": desc,
                    "price": "$0.01",
                    "priceUsd": 0.01,
                    "category": "api",
                    "trialFree": 1,
                    "method": "POST",
                    "service": "rmi-native",
                    "source": "route",
                    "chains": [],
                }
            )
    return tools


def get_catalog():
    """Build full tool catalog from TOOL_PRICES + X402_TOOL_PRICING (databus) = single source of truth.

    Combined enforcement + databus-only tools = 170 total.
    Gateway configs and route discovery are used for enrichment (chains, icons, etc).
    """

    # Always rebuild (no stale caching)
    base_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

    # ── Primary source: TOOL_PRICES + DATABUS_TOOLS (170 total, single source of truth) ──
    try:
        from app.routers.x402_enforcement import CHAIN_USDC, TOOL_PRICES

        all_tools = {}
        services = set()
        categories = set()

        for tool_id, pricing in TOOL_PRICES.items():
            desc = pricing.get("description", f"{tool_id} — crypto intelligence tool")
            category = pricing.get("category", "analysis")
            chains = []
            # If it's a per-chain variant, show the specific chain
            base_tool = pricing.get("base_tool")
            chain = pricing.get("chain")
            if chain:
                chains = [chain.upper()]

            all_tools[tool_id] = {
                "id": tool_id,
                "name": desc,
                "description": desc,
                "price": f"${pricing.get('price_usd', 0.01):.2f}",
                "priceUsd": pricing.get("price_usd", 0.01),
                "priceAtomic": pricing.get("price_atoms", "10000"),
                "category": category,
                "trialFree": pricing.get("trial_free", 1),
                "method": "POST",
                "service": "rmi-native",
                "source": "enforcement",
                "chains": chains,
                "base_tool": base_tool,
                "chain": chain,
            }
            services.add("rmi-native")
            categories.add(category)

        # Add databus-only tools (not in TOOL_PRICES enforcement)
        try:
            from app.routers.x402_databus_tools import X402_TOOL_PRICING as DATABUS_TOOLS

            for tool_id, pricing in DATABUS_TOOLS.items():
                if tool_id not in all_tools:
                    desc = pricing.get("description", f"{tool_id} — DataBus crypto intelligence")
                    category = pricing.get("category", "data")
                    all_tools[tool_id] = {
                        "id": tool_id,
                        "name": desc,
                        "description": desc,
                        "price": f"${pricing.get('price_usd', 0.05):.2f}",
                        "priceUsd": pricing.get("price_usd", 0.05),
                        "priceAtomic": pricing.get("price_atoms", "50000"),
                        "category": category,
                        "trialFree": pricing.get("trial_free", 1),
                        "method": "POST",
                        "service": "rmi-databus",
                        "source": "databus",
                        "chains": [],
                        "base_tool": None,
                        "chain": None,
                    }
                    services.add("rmi-databus")
                    categories.add(category)
        except ImportError:
            pass
    except ImportError:
        all_tools = {}
        services = set()
        categories = set()

    # ── Enrich from gateway configs (add chain info, icons) ──
    candidates = [
        "/app/x402-gateway",
        os.path.join(base_dir, "x402-gateway"),
        os.path.join(os.path.dirname(base_dir), "x402-gateway"),
        "/root/backend/x402-gateway",
    ]
    gateway_base = None
    for c in candidates:
        if os.path.isdir(c):
            gateway_base = c
            break

    chains = {}
    if gateway_base and os.path.exists(gateway_base):
        for chain_dir in sorted(os.listdir(gateway_base)):
            chain_path = os.path.join(gateway_base, chain_dir)
            if not os.path.isdir(chain_path):
                continue
            tools = parse_gateway_tools(chain_path)
            if tools:
                chains[chain_dir] = len(tools)
                for t in tools:
                    tid = t["id"]
                    if tid in all_tools:
                        # Enrich existing entry with chain info
                        if chain_dir.upper() not in all_tools[tid].get("chains", []):
                            all_tools[tid]["chains"].append(chain_dir.upper())
                    else:
                        # New tool from gateway not in TOOL_PRICES — add it
                        t["chains"] = [chain_dir.upper()]
                        all_tools[tid] = t
                        services.add("rmi-native")
                        categories.add(t.get("category", "unknown"))

    # ── Enrich from FastAPI route definitions ──
    route_tools = discover_route_tools()
    for t in route_tools:
        tid = t["id"]
        if tid in all_tools:
            # Enrich — route definitions may have more accurate descriptions
            if not all_tools[tid].get("description") or all_tools[tid].get("description", "").startswith("Tool:"):
                all_tools[tid]["description"] = t.get("description", all_tools[tid].get("description", ""))
        else:
            all_tools[tid] = t
            services.add("rmi-native")
            categories.add(t.get("category", "analysis"))

    # ── Enrich from external MCP servers ──
    external_tools = load_external_mcp_tools()
    for t in external_tools:
        tid = t.get("id", "")
        if not tid:
            continue
        # Normalize pricing - external tools may have price_usd (snake) or priceUsd (camel)
        price_usd = t.get("price_usd", t.get("priceUsd", 0.01))
        price_atoms = t.get("price_atoms", t.get("priceAtomic", str(int(price_usd * 1_000_000))))
        trial_free = t.get("trial_free", t.get("trialFree", 1))

        if tid in all_tools:
            # Enrich existing entry with external pricing if missing
            if not all_tools[tid].get("priceUsd"):
                all_tools[tid].update(
                    {
                        "price": f"${price_usd:.2f}",
                        "priceUsd": price_usd,
                        "priceAtomic": price_atoms,
                        "trialFree": trial_free,
                        "method": t.get("method", "POST"),
                    }
                )
        else:
            # New tool from external MCP
            t["chains"] = [c.upper() for c in t.get("chains", [])]
            t["price"] = f"${price_usd:.2f}"
            t["priceUsd"] = price_usd
            t["priceAtomic"] = price_atoms
            t["trialFree"] = trial_free
            t["method"] = t.get("method", "MCP")
            t["source"] = t.get("source", "expanded-mcp")
            all_tools[tid] = t
            services.add(t.get("service", "unknown"))
            categories.add(t.get("category", "unknown"))

    # Base tools that aren't variants should show all chains they support
    chain_suffixes = [
        "_solana",
        "_base",
        "_ethereum",
        "_bsc",
        "_arbitrum",
        "_polygon",
        "_avalanche",
        "_fantom",
        "_gnosis",
        "_optimism",
    ]
    for tid, tool in all_tools.items():
        # If a base tool has variants, show chains on the base
        base = tid
        for suffix in chain_suffixes:
            if tid.endswith(suffix):
                base = tid[: -len(suffix)]
                break
        # Check if this base has chain variants
        chain_variants = [t for t in all_tools if t.startswith(base + "_")]
        if chain_variants and not tool.get("chain"):
            # This is a base tool — it supports all chains its variants cover
            variant_chains = []
            for v in chain_variants:
                v_chain = all_tools[v].get("chain", "")
                if v_chain:
                    variant_chains.append(v_chain.upper())
            if variant_chains:
                tool["chains"] = sorted(set(variant_chains + tool.get("chains", [])))

    # Sanitize ALL tools before returning
    tool_list = sorted([_sanitize_tool(dict(t)) for t in all_tools.values()], key=lambda x: x.get("priceUsd", 0))

    result = {
        "chains": chains or {"base": 0, "solana": 0},
        "total_tools": len(tool_list),
        "total_chains": len(chains) or 2,
        "total_services": len(services),
        "tools": tool_list,
        "categories": sorted(categories),
        "services": sorted(services),
    }

    _catalog_cache.clear()
    _catalog_cache.update(result)
    return result


@router.get("/tools-catalog")
async def list_tools_catalog():
    """Get all available MCP tools across all chains and external services"""
    return get_catalog()


@router.get("/tools-catalog/{chain}")
async def list_chain_tools(chain: str):
    """Get tools for a specific chain (includes native + external)"""
    catalog = get_catalog()
    chain_upper = chain.upper()
    chain_tools = [t for t in catalog["tools"] if chain_upper in t.get("chains", [])]
    return {"chain": chain, "count": len(chain_tools), "tools": chain_tools}


@router.get("/tools-catalog/category/{category}")
async def list_category_tools(category: str):
    """Get tools in a specific category across all chains"""
    catalog = get_catalog()
    filtered = [t for t in catalog["tools"] if t.get("category", "") == category.lower()]
    return {"category": category, "count": len(filtered), "tools": filtered}


@router.get("/tools-catalog/service/{service}")
async def list_service_tools(service: str):
    """Get tools from a specific external MCP service"""
    catalog = get_catalog()
    filtered = [t for t in catalog["tools"] if t.get("service", "") == service.lower()]
    return {"service": service, "count": len(filtered), "tools": filtered}
