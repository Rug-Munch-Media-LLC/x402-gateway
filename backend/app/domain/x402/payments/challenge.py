"""Signed 402 challenge (invoice) construction.

The signature binds the invoice fields so a client can verify that the
challenge really came from this gateway before paying it. The signing key is
never embedded in source and never logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.domain.x402.payments.accepts import build_accepts
from app.domain.x402.payments.config import PaymentConfig


def _sign(config: PaymentConfig, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = hmac.new(config.hmac_secret, canonical, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def build_challenge(
    config: PaymentConfig,
    tool: str,
    agent_id: str,
    price_cents: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a signed invoice that includes a valid ``accepts`` list."""
    now = now or datetime.now(UTC)
    accepts = build_accepts(config, price_cents)
    invoice: dict[str, Any] = {
        "id": uuid4().hex,
        "tool": tool,
        "agent_id": agent_id,
        "amount_usd": price_cents / 100.0,
        "amount": accepts[0]["amount"],
        "asset": accepts[0]["asset"],
        "network": accepts[0]["network"],
        "pay_to": config.pay_to,
        "chain": config.chain,
        "created_at": now.isoformat(),
        "expires_at": int(now.timestamp()) + config.max_timeout_seconds,
        "accepts": accepts,
    }
    invoice["signature"] = _sign(config, invoice)
    return invoice
