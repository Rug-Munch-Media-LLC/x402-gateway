"""Payment-header steps for the x402 enforcement middleware.

Verification/settlement semantics are unchanged from the inline version.
"""

from __future__ import annotations

import base64 as _base64
import json
import logging

from app.routers.enforcement_chain import SECURITY_HEADERS
from app.routers.x402_enforcement import (
    CHAIN_USDC,
    _record_refundable_payment,
    parse_x_pay_header,
    verify_payment_via_router,
)
from fastapi import Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def oversized_payment_header(pay_sig: str, x_pay: str) -> Response | None:
    """Reject payment headers large enough to be a DoS vector."""
    if pay_sig and len(pay_sig) > 16384:
        return JSONResponse(
            status_code=400,
            content={"error": "Payment header too large"},
            headers=SECURITY_HEADERS,
        )
    if x_pay and len(x_pay) > 8192:
        return JSONResponse(
            status_code=400,
            content={"error": "Payment header too large"},
            headers=SECURITY_HEADERS,
        )
    return None


def validate_payment_payload(x_pay: str) -> tuple[dict | None, Response | None]:
    """Parse a v1 x-pay header and validate its shape."""
    payload = parse_x_pay_header(x_pay)
    if not payload:
        return None, None
    if not isinstance(payload, dict):
        return None, JSONResponse(
            status_code=400,
            content={"error": "Invalid payment payload structure"},
            headers=SECURITY_HEADERS,
        )
    if not isinstance(payload.get("accepted", {}), dict):
        return None, JSONResponse(
            status_code=400,
            content={"error": "Invalid accepted payment structure"},
            headers=SECURITY_HEADERS,
        )
    return payload, None


def record_facilitator(result: dict, success: bool) -> None:
    """Track facilitator verify outcomes for health monitoring.

    Health telemetry must never break a payment path: only import failures and
    runtime/transport errors from the health module are swallowed.
    """
    facilitator = result.get("method", result.get("facilitator", ""))
    if not facilitator:
        return
    try:
        from app.routers.facilitator_health import record_payment_success

        record_payment_success(facilitator, success)
    except (ImportError, AttributeError, RuntimeError, OSError, ValueError) as exc:
        logger.warning(
            "facilitator_health_record_failed",
            extra={"facilitator": facilitator, "error": str(exc)},
        )


def settlement_response_header(result: dict) -> str | None:
    """Build the base64 PAYMENT-RESPONSE header required by x402 v2."""
    settlement_response = {
        "success": True,
        "transaction": result.get("tx_hash", ""),
        "network": CHAIN_USDC.get(result.get("chain", ""), {}).get(
            "network", result.get("chain", "")
        ),
        "payer": result.get("payer", ""),
    }
    if result.get("amount"):
        settlement_response["amount"] = result.get("amount")
    try:
        return _base64.b64encode(
            json.dumps(settlement_response, separators=(",", ":")).encode()
        ).decode("ascii")
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        # Never fail a settled request on header encoding; a programming error
        # in this function (e.g. NameError) still propagates.
        logger.warning("settlement_header_encode_failed", extra={"error": str(exc)})
        return None


def flag_refundable(request: Request, response: Response, tool_id: str) -> None:
    """Flag a settled payment whose response was an HTTP error."""
    _record_refundable_payment(
        tx_hash=request.state.x402_tx_hash or "unknown",
        chain=request.state.x402_chain,
        payer=request.state.x402_payer,
        amount=request.state.x402_amount,
        tool=tool_id,
        reason=f"HTTP {response.status_code} error response",
    )
    response.headers["X-RMI-Refund-Flagged"] = "true"
    logger.info(
        f"Flagged payment for refund: tx={request.state.x402_tx_hash} "
        f"tool={tool_id} reason=HTTP_{response.status_code}"
    )


async def verified_payment_response(
    request: Request, call_next, result: dict, tool_id: str
) -> Response:
    """Attach verified-payment state, call the app, then decorate the response."""
    request.state.x402_verified = True
    request.state.x402_payer = result.get("payer", "")
    request.state.x402_chain = result.get("chain", "")
    request.state.x402_tx_hash = result.get("tx_hash", "")
    request.state.x402_amount = result.get("amount", "")
    request.state.x402_method = result.get("method", "")

    record_facilitator(result, True)

    response = await call_next(request)
    response.headers["X-RMI-Payment"] = "verified"

    encoded = settlement_response_header(result)
    if encoded is not None:
        response.headers["PAYMENT-RESPONSE"] = encoded

    for k, v in SECURITY_HEADERS.items():
        if k not in response.headers:
            response.headers[k] = v

    if response.status_code >= 400:
        flag_refundable(request, response, tool_id)
    return response


async def paid_request_response(
    request: Request, call_next, tool_id: str
) -> Response | None:
    """Return a response when a payment header was supplied, else None (no payment)."""
    pay_sig = request.headers.get("PAYMENT-SIGNATURE", "") or request.headers.get(
        "Payment-Signature", ""
    )
    x_pay = request.headers.get("x-pay", "") or request.headers.get("X-Pay", "")

    oversized = oversized_payment_header(pay_sig, x_pay)
    if oversized is not None:
        return oversized

    if pay_sig:
        # v2 PaymentPayload: verify_payment_via_router normalises this format
        parse_x_pay_header(pay_sig)
        return None
    if not x_pay:
        return None

    payload, invalid = validate_payment_payload(x_pay)
    if invalid is not None:
        return invalid
    if not payload:
        return None

    result = await verify_payment_via_router(payload)
    if result.get("verified"):
        return await verified_payment_response(request, call_next, result, tool_id)
    record_facilitator(result, False)
    return None
