"""x402 ``accepts`` entries — the machine-readable payment options.

An unpaid 402 is only useful if the client can act on it, so the accepts list
is always derived from config + price, never hand-assembled at call sites.
"""

from __future__ import annotations

from typing import Any

from app.domain.x402.payments.config import ATOMS_PER_CENT, USDC_BASE, PaymentConfig


def build_accepts(config: PaymentConfig, price_cents: int) -> list[dict[str, Any]]:
    """Return the x402 v2 accepts list for a tool priced in USD cents.

    Raises ValueError for a non-positive price: a paid tool with no price is a
    configuration bug, not a free call.
    """
    if price_cents <= 0:
        raise ValueError("price_cents must be positive for a paid tool")
    asset = config.asset or USDC_BASE
    return [
        {
            "scheme": "exact",
            "network": config.network,
            "asset": asset,
            "amount": str(price_cents * ATOMS_PER_CENT),
            "payTo": config.pay_to,
            "maxTimeoutSeconds": config.max_timeout_seconds,
            "extra": {
                "name": "USD Coin",
                "version": "2",
                "decimals": 6,
            },
        }
    ]
