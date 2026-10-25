"""x402 domain — T34 payment layer for AI agents.

Re-exports are lazy (PEP 562): importing ``app.domain.x402.payments`` must not
drag in routers, health registration or an app-wide settings module. Each name
is imported on first access, so a missing optional dependency breaks only the
caller that asked for it.
"""

from __future__ import annotations

import logging
from importlib import import_module
from typing import Any

log = logging.getLogger(__name__)

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "PaymentFacilitator": ("app.domain.x402.models", "PaymentFacilitator"),
    "PaymentReceipt": ("app.domain.x402.models", "PaymentReceipt"),
    "ToolCatalog": ("app.domain.x402.models", "ToolCatalog"),
    "ToolCatalogEntry": ("app.domain.x402.models", "ToolCatalogEntry"),
    "ToolPricing": ("app.domain.x402.models", "ToolPricing"),
    "X402Tier": ("app.domain.x402.models", "X402Tier"),
    "X402Service": ("app.domain.x402.service", "X402Service"),
    "router": ("app.domain.x402.router", "router"),
    "PaymentGate": ("app.domain.x402.payments.gate", "PaymentGate"),
}

__all__ = [
    "PaymentFacilitator",
    "PaymentGate",
    "PaymentReceipt",
    "ToolCatalog",
    "ToolCatalogEntry",
    "ToolPricing",
    "X402Service",
    "X402Tier",
    "router",
]


def __getattr__(name: str) -> Any:
    """Resolve a public re-export on first access."""
    try:
        module_name, attribute = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
