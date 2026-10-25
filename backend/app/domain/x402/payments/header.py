"""X-Payment header decoding.

Two shapes are accepted, both unchanged from the published contract:
  * legacy: ``base64({"tx_hash", "signature"})``
  * x402 v2: ``base64({"x402Version", "accepted", "payload"})``

Decoding is strict and raises :class:`InvalidPaymentHeader` — never returns
``None`` for a caller to forget to check.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.x402.payments.errors import InvalidPaymentHeader

_TX_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class PaymentProof:
    """Normalized payment proof, whatever wire shape it arrived in."""

    tx_hash: str | None = None
    signature: str = ""
    network: str = ""
    asset: str = ""
    amount: str = ""
    pay_to: str = ""
    payer: str = ""
    valid_before: int | None = None
    nonce: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def replay_key(self) -> str:
        """Stable key for replay detection (tx hash, else auth nonce)."""
        key = self.tx_hash or self.nonce
        if not key:
            raise InvalidPaymentHeader("payment proof has no tx hash or nonce")
        return key.lower()


def _decode_json(raw: str) -> dict[str, Any]:
    try:
        decoded = base64.b64decode(raw, validate=True).decode()
        data = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidPaymentHeader("X-Payment is not valid base64 JSON") from exc
    if not isinstance(data, dict):
        raise InvalidPaymentHeader("X-Payment payload must be a JSON object")
    return data


def _tx_hash(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not _TX_HASH.match(text):
        raise InvalidPaymentHeader("X-Payment tx_hash is not a 32-byte hex hash")
    return text


def _valid_before(value: Any) -> int | None:
    return int(value) if value else None


def _text(value: Any) -> str:
    """Empty-safe string field extraction."""
    return str(value or "")


def _terms(
    data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Split a header into (accepted, payload, authorization)."""
    accepted = data.get("accepted") or {}
    payload = data.get("payload") or {}
    auth = payload.get("authorization") or {}
    if auth and not accepted:
        raise InvalidPaymentHeader("X-Payment payload lacks accepted requirements")
    return accepted, payload, auth


def _build_proof(data: dict[str, Any]) -> PaymentProof:
    accepted, payload, auth = _terms(data)
    proof = PaymentProof(
        tx_hash=_tx_hash(data.get("tx_hash")),
        signature=_text(payload.get("signature") or data.get("signature")),
        network=_text(accepted.get("network")),
        asset=_text(accepted.get("asset")),
        amount=_text(accepted.get("amount")),
        pay_to=_text(accepted.get("payTo")),
        payer=_text(auth.get("from")),
        valid_before=_valid_before(auth.get("validBefore")),
        nonce=_text(auth.get("nonce")),
        raw=data,
    )
    if not proof.replay_key:
        raise InvalidPaymentHeader("payment proof has no tx hash or nonce")
    return proof


def decode_payment_header(x_payment: str | None) -> PaymentProof:
    """Decode and normalize an X-Payment header value."""
    if not x_payment or not x_payment.strip():
        raise InvalidPaymentHeader("missing X-Payment header")
    return _build_proof(_decode_json(x_payment.strip()))
