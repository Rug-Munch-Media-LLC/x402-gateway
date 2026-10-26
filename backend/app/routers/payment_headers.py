"""Version-aware x402 payment header selection.

v2 spec: ``PAYMENT-SIGNATURE`` (base64 JSON PaymentPayload).
v1: ``X-PAYMENT`` / ``X-Pay`` (raw or ``x402 <json>``).
"""
from __future__ import annotations

from collections.abc import Mapping


def select_payment_header(headers: Mapping[str, str]) -> tuple[str, int]:
    """Return ``(raw_value, x402_version)``; ``("", 0)`` when no proof."""
    lowered = {str(k).lower(): str(v) for k, v in dict(headers).items()}
    for name, version in (("payment-signature", 2), ("x-payment", 1), ("x-pay", 1)):
        value = (lowered.get(name) or "").strip()
        if value:
            return value, version
    return "", 0
