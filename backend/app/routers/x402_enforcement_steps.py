"""Early-exit steps for the x402 enforcement middleware.

Extracted from ``x402_enforcement.x402_enforcement_middleware`` (behaviour kept
identical) so the middleware reads as a list of named checks.
"""

from __future__ import annotations

import logging
import os
import re

from app.core.redis import get_redis
from app.routers.enforcement_chain import SECURITY_HEADERS
from fastapi import Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

TOOL_ID_RE = re.compile(r"^[a-zA-Z0-9_]+$")


async def run_enforcement_chain(request: Request, path: str) -> Response | None:
    """Run the Chain of Responsibility early-exit checks (free paths, bots).

    ImportError means the optional chain module is absent; transport/runtime
    errors from a step are logged and the request continues to payment. A bug
    in a step (TypeError, AttributeError, AssertionError) propagates on purpose.
    """
    try:
        from app.routers.enforcement_chain import (
            BotDetector,
            EnforcementChain,
            EnforcementContext,
            FreeEndpointRouter,
        )

        chain = EnforcementChain().add(FreeEndpointRouter()).add(BotDetector())

        ctx = EnforcementContext(request)
        ctx.path = path
        ctx.method = request.method
        ctx.user_agent = (request.headers.get("User-Agent", "") or "").lower()

        for step in chain._steps:
            result = await step.process(request, ctx)
            if result is not None:
                return result
    except ImportError:
        return None  # Chain module not available
    except (TimeoutError, ValueError, KeyError, RuntimeError, OSError) as exc:
        logger.warning("enforcement_chain_error", extra={"error": str(exc)})
    return None


async def relay_trial_headers(request: Request, call_next) -> Response:
    """Pass through an already-granted trial request, tagging the response."""
    response = await call_next(request)
    response.headers["X-RMI-Trial"] = "true"
    response.headers["X-RMI-Trial-Remaining"] = request.headers.get(
        "X-RMI-Trial-Remaining", "0"
    )
    for k, v in SECURITY_HEADERS.items():
        if k not in response.headers:
            response.headers[k] = v
    return response


async def handle_developer_key(request: Request, call_next) -> Response | None:
    """Apply the developer API key tier check; None means continue.

    Only import failures and transport/runtime errors from the tier backend are
    swallowed; programming errors in the check propagate.
    """
    try:
        from app.routers.developer_tier import check_developer_key

        dev_result = await check_developer_key(request)
        if dev_result is not None:
            if dev_result.get("deny"):
                status_code = dev_result.get("status_code", 429)
                return JSONResponse(
                    status_code=status_code,
                    content={
                        "error": dev_result.get("error", "rate_limit_exceeded"),
                        "tier": dev_result.get("tier", "free"),
                        "message": dev_result.get("error", "Rate limit exceeded"),
                        "daily_limit": dev_result.get("daily_limit"),
                        "calls_today": dev_result.get("calls_today"),
                        "upgrade_url": "https://rugmunch.io/mcp-docs#pricing",
                    },
                    headers={
                        **SECURITY_HEADERS,
                        "X-RMI-Tier": dev_result.get("tier", "free"),
                        "X-RMI-Remaining": str(dev_result.get("remaining_today", 0)),
                        "Retry-After": str(dev_result.get("retry_after", 60)),
                    },
                )
            request.state.developer_key = True
            request.state.developer_tier = dev_result.get("tier", "free")
            request.state.developer_email = dev_result.get("email", "")
            response = await call_next(request)
            response.headers["X-RMI-Tier"] = dev_result.get("tier", "free")
            response.headers["X-RMI-Remaining"] = str(
                dev_result.get("remaining_today", 0)
            )
            return response
    except ImportError:
        return None
    except (TimeoutError, ValueError, KeyError, RuntimeError, OSError) as exc:
        logger.warning("developer_key_check_failed", extra={"error": str(exc)})
    return None


def check_burst(request: Request, path: str, client_id: str) -> Response | None:
    """Rate-limit rapid requests per client/tool; None means continue."""
    path_tool_id = (
        path.rstrip("/").split("/")[-1] if path.count("/") >= 4 else "unknown"
    )
    r = get_redis()
    if not (r and TOOL_ID_RE.match(path_tool_id)):
        return None
    burst_key = f"x402:burst:{client_id}:{path_tool_id}"
    try:
        burst_count = int(r.get(burst_key) or 0)
        burst_limit = int(os.getenv("X402_BURST_LIMIT", "5"))
        burst_window = int(os.getenv("X402_BURST_WINDOW", "60"))
        if burst_count >= burst_limit:
            logger.warning(
                f"Burst limit: {client_id} hit {burst_count} requests in {burst_window}s"
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": (
                        f"Rate limited. Max {burst_limit} requests per "
                        f"{burst_window}s per tool."
                    ),
                    "retry_after": burst_window,
                },
                headers={**SECURITY_HEADERS, "Retry-After": str(burst_window)},
            )
        pipe = r.pipeline()
        pipe.incr(burst_key)
        if burst_count == 0:
            pipe.expire(burst_key, burst_window)
        pipe.execute()
    except (TimeoutError, ValueError, KeyError, RuntimeError, OSError) as exc:
        # Never block a request on burst-tracking failures.
        logger.warning(
            "burst_tracking_failed",
            extra={"client_id": client_id, "error": str(exc)},
        )
    return None
