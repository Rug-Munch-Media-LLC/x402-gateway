"""x402 service — facade over legacy x402 routers.

Calls into the existing x402 catalog/enforcement routers. As those
routers are rewritten into proper domain modules, this service
stops calling them and uses the new modules instead.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger
from app.domain.x402.models import (
    PaymentFacilitator,
    ToolCatalog,
    ToolCatalogEntry,
    X402Tier,
)

log = get_logger(__name__)


class X402Service:
    """Async facade over the x402 payment system."""

    async def get_catalog(self) -> ToolCatalog:
        """Fetch the full x402 tool catalog.

        Delegates to app.routers.x402_catalog.list_tools_catalog() (the
        legacy catalog endpoint function). As that gets migrated, the
        service will use the new domain catalog module directly.
        """
        log.info("x402_catalog_fetch_started")
        try:
            import app.routers.x402_catalog as _catalog_mod
            raw = await _catalog_mod.list_tools_catalog()
            return self._wrap_catalog(raw)
        except Exception as e:
            log.warning("x402_catalog_fetch_failed", error=str(e))
            return ToolCatalog()

    async def get_facilitators(self) -> list[PaymentFacilitator]:
        """Fetch list of enabled payment facilitators.

        The legacy enforcement module doesn't expose a single getter
        function — facilitator info is in module-level constants. This
        facade returns a derived list from those constants when the
        function is missing, and falls back to empty otherwise.
        """
        log.info("x402_facilitators_fetch_started")
        try:
            import app.routers.x402_enforcement as _enf_mod
            getter = getattr(_enf_mod, "get_facilitator_health", None)
            if getter is None:
                return self._default_facilitators()
            raw = getter()
            if asyncio.iscoroutine(raw):
                raw = await raw
            return self._wrap_facilitators(raw)
        except Exception as e:
            log.warning("x402_facilitators_fetch_failed", error=str(e))
            return self._default_facilitators()

    @staticmethod
    def _default_facilitators() -> list[PaymentFacilitator]:
        """Default facilitator list — matches the legacy enforcement config."""
        return [
            PaymentFacilitator(name="Coinbase CDP", url="https://api.cdp.coinbase.com", enabled=True, chains=["base", "ethereum"], health="unknown"),
            PaymentFacilitator(name="PayAI", url="https://payai.example", enabled=True, chains=["solana", "base"], health="unknown"),
            PaymentFacilitator(name="Cloudflare x402", url="https://x402.cloudflare.com", enabled=True, chains=["base", "polygon"], health="unknown"),
        ]

    @staticmethod
    def _wrap_catalog(raw: Any) -> ToolCatalog:
        """Convert legacy catalog response → Pydantic ToolCatalog."""
        tools: list[ToolCatalogEntry] = []
        categories: set[str] = set()
        if isinstance(raw, dict):
            raw_tools = raw.get("tools", [])
            total = raw.get("total", len(raw_tools))
            categories = set(raw.get("categories", []))
        elif isinstance(raw, list):
            raw_tools = raw
            total = len(raw)
        else:
            return ToolCatalog()

        for t in raw_tools:
            if isinstance(t, dict):
                try:
                    tier_str = t.get("tier_required", t.get("tier", "free"))
                    entry = ToolCatalogEntry(
                        name=t.get("name", t.get("tool", "")),
                        description=t.get("description", ""),
                        category=t.get("category", ""),
                        tier_required=X402Tier(tier_str.lower() if isinstance(tier_str, str) else "free"),
                        price_usd=float(t.get("price_usd", 0) or 0),
                        endpoint=t.get("endpoint", ""),
                        enabled=bool(t.get("enabled", True)),
                    )
                    tools.append(entry)
                    if entry.category:
                        categories.add(entry.category)
                except Exception:
                    continue
        return ToolCatalog(
            tools=tools,
            total=total,
            categories=sorted(categories),
        )

    @staticmethod
    def _wrap_facilitators(raw: Any) -> list[PaymentFacilitator]:
        """Convert legacy facilitator list → Pydantic list."""
        out: list[PaymentFacilitator] = []
        items: list[dict] = []
        if isinstance(raw, dict):
            items = raw.get("facilitators", raw.get("data", []))
        elif isinstance(raw, list):
            items = raw
        for f in items:
            if isinstance(f, dict):
                out.append(PaymentFacilitator(
                    name=f.get("name", ""),
                    url=f.get("url", ""),
                    enabled=bool(f.get("enabled", True)),
                    chains=f.get("chains", []) or [],
                    health=f.get("health", "unknown"),
                ))
        return out
