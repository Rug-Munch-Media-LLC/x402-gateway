"""Free-trial steps for the x402 enforcement middleware.

Trial consumption, DataBus execution and the GET-to-POST fallback are kept
byte-for-byte equivalent to the inline version.
"""

from __future__ import annotations

import json
import logging

import httpx
from app.core.redis import get_redis
from app.routers.enforcement_chain import SECURITY_HEADERS
from app.routers.x402_enforcement import build_402_response, check_trial
from fastapi import Request, Response

logger = logging.getLogger(__name__)

NO_DATA_MESSAGE = (
    "Free trial used. Data not available for this address — try a real token address."
)


def trial_verdict(tool_id: str, client_id: str) -> tuple[bool, int]:
    """Non-consuming trial check."""
    try:
        return check_trial(tool_id, client_id, consume=False)
    except (ImportError, TimeoutError, ValueError, KeyError, RuntimeError, OSError) as exc:
        logger.warning("trial_check_failed", extra={"tool": tool_id, "error": str(exc)})
        return False, 0


def consume_trial(tool_id: str, client_id: str, remaining: int) -> int:
    """Atomically consume one trial; returns the post-consumption remainder."""
    try:
        _, remaining_after = check_trial(tool_id, client_id, consume=True)
        return remaining_after
    except (ImportError, TimeoutError, ValueError, KeyError, RuntimeError, OSError) as exc:
        logger.warning(
            "trial_consumption_failed",
            extra={"tool": tool_id, "client_id": client_id, "error": str(exc)},
        )
        return remaining


def no_data_response(
    tool_id: str, remaining: int, message: str | None = None
) -> Response:
    """200 response for a consumed trial whose data source had nothing."""
    body: dict = {
        "tool": tool_id,
        "result": "no_data_available",
        "trial": True,
        "trial_remaining": remaining,
    }
    if message is not None:
        body["message"] = message
    return Response(
        content=json.dumps(body),
        status_code=200,
        media_type="application/json",
        headers={
            "X-RMI-Trial": "true",
            "X-RMI-Trial-Remaining": str(remaining),
            **SECURITY_HEADERS,
        },
    )


async def databus_trial_result(
    request: Request, tool_id: str, remaining: int
) -> Response | None:
    """Execute the tool through the DataBus caching shield; None = not available."""
    from app.caching_shield.tool_data import td

    params: dict = {}
    if request.method == "GET" and request.query_params:
        params = dict(request.query_params)
    elif request.method == "POST":
        try:
            params = await request.json()
        except (ValueError, TypeError) as exc:
            logger.warning("trial_body_parse_failed", extra={"tool": tool_id, "error": str(exc)})
            params = {}

    result = await td.call_tool(tool_id, params)
    if result is None:
        return None
    return Response(
        content=json.dumps(result, default=str),
        status_code=200,
        media_type="application/json",
        headers={
            "X-RMI-Trial": "true",
            "X-RMI-Trial-Remaining": str(remaining),
            **SECURITY_HEADERS,
        },
    )


async def http_trial_result(
    request: Request, tool_id: str, remaining: int
) -> Response | None:
    """GET-to-POST fallback for tools with no DataBus entry; None = not applicable.

    Transport/decoding failures are turned into the "no data" trial response;
    a genuine bug here (TypeError, AttributeError) is allowed to propagate.
    """
    if not (request.method == "GET" and request.query_params):
        return None

    query_params = dict(request.query_params)
    base_url = str(request.url).split("?")[0]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                base_url,
                json=query_params,
                headers={
                    "Content-Type": "application/json",
                    "X-RMI-Internal": "trial-granted",
                    "X-RMI-Trial": "true",
                    "X-RMI-Trial-Remaining": str(remaining),
                    "X-Device-Id": request.headers.get("x-device-id", ""),
                },
            )
            if resp.status_code == 404:
                return no_data_response(tool_id, remaining, NO_DATA_MESSAGE)
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type", "application/json"),
                headers={
                    k: v
                    for k, v in resp.headers.items()
                    if k.lower() not in ("content-length", "transfer-encoding")
                },
            )
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, RuntimeError, OSError) as exc:
        # Upstream call failed — trial was consumed, report no data
        logger.warning("trial_http_fallback_failed", extra={"tool": tool_id, "error": str(exc)})
        return no_data_response(tool_id, remaining)


async def relay_trial(request: Request, call_next, remaining: int) -> Response:
    """Plain pass-through with trial headers (no 404 substitution)."""
    response = await call_next(request)
    response.headers["X-RMI-Trial"] = "true"
    response.headers["X-RMI-Trial-Remaining"] = str(remaining)
    for k, v in SECURITY_HEADERS.items():
        if k not in response.headers:
            response.headers[k] = v
    return response


async def run_trial(
    request: Request, call_next, tool_id: str, client_id: str, remaining: int
) -> Response:
    """Execute a granted free trial via DataBus, with fallbacks.

    The DataBus SDK may raise transport/decoding errors; those are swallowed
    (trial refunded, 402 returned). Programming errors propagate.
    """
    try:
        databus = await databus_trial_result(request, tool_id, remaining)
        if databus is not None:
            return databus
        http_result = await http_trial_result(request, tool_id, remaining)
        if http_result is not None:
            return http_result
        response = await call_next(request)
        if response.status_code == 404:
            return no_data_response(tool_id, remaining, NO_DATA_MESSAGE)
        response.headers["X-RMI-Trial"] = "true"
        response.headers["X-RMI-Trial-Remaining"] = str(remaining)
        for k, v in SECURITY_HEADERS.items():
            if k not in response.headers:
                response.headers[k] = v
        return response
    except ImportError:
        logger.warning(
            "DataBus caching shield not available, falling back to call_next"
        )
        return await relay_trial(request, call_next, remaining)
    except (
        httpx.HTTPError,
        TimeoutError,
        ValueError,
        KeyError,
        RuntimeError,
        OSError,
    ) as exc:
        logger.warning("trial_databus_failed", extra={"tool": tool_id, "error": str(exc)})
        # Trial consumed but execution failed — refund it
        try:
            r = get_redis()
            if r:
                key = f"x402:trial:{client_id}:{tool_id}"
                r.decr(key)  # Roll back the consumption
        except (TimeoutError, ValueError, KeyError, RuntimeError, OSError) as exc:
            logger.warning(
                "trial_rollback_failed",
                extra={"tool": tool_id, "error": str(exc)},
            )
        # Return 402 — trial refunded since we couldn't execute
        return build_402_response(tool_id, client_id=client_id)
