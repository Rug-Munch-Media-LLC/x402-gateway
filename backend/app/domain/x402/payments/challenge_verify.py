"""Invoice signature verification — one job, one function.

Separated from construction so each module keeps a single primary function.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from app.domain.x402.payments.config import PaymentConfig


def verify_challenge_signature(config: PaymentConfig, invoice: dict[str, Any]) -> bool:
    """Constant-time check that an invoice signature matches its fields."""
    provided = invoice.get("signature") or ""
    body = {k: v for k, v in invoice.items() if k != "signature"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    expected = base64.b64encode(
        hmac.new(config.hmac_secret, canonical, hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(provided, expected)
