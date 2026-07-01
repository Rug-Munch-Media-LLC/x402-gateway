"""x402 domain — auto-registers its health check + HTTP routes.

T34 from v4.0. Sovereign-first x402 payment layer for AI agents.

Re-exports the legacy models (PaymentFacilitator, X402Tier) for backward
compatibility with v1 routers that import from app.domain.x402.
"""
from __future__ import annotations

from app.core import health as health_mod
from app.core.health import DomainHealth


async def _health_check() -> DomainHealth:
    """x402 health: catalog + middleware available."""
    try:
        from app.domain.x402.middleware import PRICING
        return DomainHealth(
            name="x402",
            healthy=True,
            details={"tools": len(PRICING), "middleware": "available"},
        )
    except Exception as e:
        return DomainHealth(name="x402", healthy=False, error=str(e))


health_mod.register_health_check("x402", _health_check)


# Re-export models for backward compat with v1 routers and tests
from app.domain.x402.models import (
    PaymentFacilitator,
    PaymentReceipt,
    ToolCatalog,
    ToolCatalogEntry,
    ToolPricing,
    X402Tier,
)

# Re-export the new T34 router
from app.domain.x402.router import router  # noqa: E402
from app.domain.x402.service import X402Service

__all__ = [
    "PaymentFacilitator",
    "PaymentReceipt",
    "ToolCatalog",
    "ToolCatalogEntry",
    "ToolPricing",
    "X402Service",
    "X402Tier",
    "router",
]
