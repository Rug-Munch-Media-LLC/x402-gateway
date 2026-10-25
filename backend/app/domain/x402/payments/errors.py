"""Typed payment errors for the x402 payment core.

Every failure on the payment path is one of these errors: the gate never
returns a bare ``None``/``False`` that a caller can mistake for success, and
each error carries an HTTP mapping so the wire contract stays in one place.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

PAYMENT_REQUIRED = 402
PAYMENT_REPLAY = 410
SETTLEMENT_FAILED = 502


class PaymentError(Exception):
    """Base class for every payment-path failure."""

    code = "payment_error"
    status_code = PAYMENT_REQUIRED

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context = context

    def as_detail(self) -> dict[str, Any]:
        """Public, secret-free error body."""
        return {"error": self.code, "message": self.message, **self.context}


class PaymentRequired(PaymentError):
    """No payment supplied — caller must pay the returned challenge."""

    code = "payment_required"

    def __init__(self, challenge: dict[str, Any], **context: Any) -> None:
        super().__init__("Payment required", **context)
        self.challenge = challenge

    def as_detail(self) -> dict[str, Any]:
        invoice_id = self.challenge.get("id", "")
        return {
            **super().as_detail(),
            "invoice": self.challenge,
            "payment_url": f"https://pay.rugmunch.io/{invoice_id}",
            "instructions": (
                "Pay on-chain, then retry with an X-Payment header "
                "(base64 JSON describing tx_hash or an x402 v2 payload)"
            ),
        }


class InvalidPaymentHeader(PaymentError):
    """X-Payment header is missing, not base64, or not the expected shape."""

    code = "invalid_payment_header"


class VerificationFailed(PaymentError):
    """On-chain / facilitator verification rejected the proof."""

    code = "invalid_payment_proof"


class PaymentExpired(PaymentError):
    """The payment authorization is past its valid-before window."""

    code = "payment_expired"


class AmountMismatch(PaymentError):
    """Paid amount does not equal the required amount."""

    code = "amount_mismatch"


class AssetMismatch(PaymentError):
    """Paid asset is not the accepted asset."""

    code = "asset_mismatch"


class ChainMismatch(PaymentError):
    """Payment was made on a network we do not accept for this tool."""

    code = "chain_mismatch"


class RecipientMismatch(PaymentError):
    """Payment was sent to an address other than the configured pay-to."""

    code = "recipient_mismatch"


class PaymentReplay(PaymentError):
    """This proof was already spent on a different resource."""

    code = "payment_already_used"
    status_code = PAYMENT_REPLAY


class SettlementError(PaymentError):
    """Settlement failed after a successful verification."""

    code = "settlement_failed"
    status_code = SETTLEMENT_FAILED


def to_http_exception(error: PaymentError) -> HTTPException:
    """Map a typed payment error to the HTTP response the gateway returns.

    Detail shape is stable: a dict with ``error`` plus a human message, with
    the challenge nested under ``invoice`` for ``payment_required``.
    """
    return HTTPException(status_code=error.status_code, detail=error.as_detail())
