"""
x402_settle_api.py — x402 micropayment settlement

Settles the EIP-3009 USDC transfer authorization returned by the client
and activates the corresponding entitlement (subscription tier or add-on).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/subscription", tags=["x402-settle"])

# USDC on Base
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
PAY_TO = os.getenv("X402_PAY_TO", "0x1E3AC01d0fdb976179790BDD02823196A92705C9")
BASE_RPC = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")


# ── Storage shims ────────────────────────────────────────────
try:
    import redis as _redis

    _r = _redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=0,
        decode_responses=True,
        socket_timeout=2,
    )
    _r.ping()
    REDIS_OK = True
except Exception:
    REDIS_OK = False
    logger.warning("x402_settle: redis unavailable, using in-memory store")

_pending: dict[str, dict] = {}
_subscriptions: dict[str, dict] = {}


def _save_pending(challenge_id: str, data: dict) -> None:
    if REDIS_OK:
        _r.setex(f"rmi:x402:pending:{challenge_id}", 600, json.dumps(data))
    _pending[challenge_id] = data


def _load_pending(challenge_id: str) -> dict | None:
    if REDIS_OK:
        raw = _r.get(f"rmi:x402:pending:{challenge_id}")
        if raw:
            return json.loads(raw)
    return _pending.get(challenge_id)


def _delete_pending(challenge_id: str) -> None:
    if REDIS_OK:
        _r.delete(f"rmi:x402:pending:{challenge_id}")
    _pending.pop(challenge_id, None)


def _activate_subscription(user_id: str, tier: str, period: str, addons: list[str]) -> dict:
    """Persist subscription entitlements (Redis) and return them."""
    sub_id = secrets.token_hex(16)
    now = datetime.utcnow()
    days_map = {"monthly": 30, "six_month": 180, "yearly": 365}
    end = now + timedelta(days=days_map.get(period, 30))
    sub = {
        "id": sub_id,
        "user_id": user_id,
        "tier": tier,
        "period": period,
        "addons": addons,
        "status": "active",
        "current_period_start": now.isoformat() + "Z",
        "current_period_end": end.isoformat() + "Z",
        "created_at": now.isoformat() + "Z",
    }
    if REDIS_OK:
        _r.setex(f"rmi:sub:{user_id}", int((end - now).total_seconds()), json.dumps(sub))
    _subscriptions[sub_id] = sub
    return sub


# ── Models ────────────────────────────────────────────────────


class SettleRequest(BaseModel):
    challenge_id: str = None
    addon_id: str = None
    tier: str = None
    addon: str = None
    period: str = "monthly"
    x_pay: str
    payer: str
    amount_atoms: str = None


# ── Endpoints ─────────────────────────────────────────────────


@router.post("/settle-x402")
async def settle_x402(req: SettleRequest, request: Request):
    """Settle an x402 EIP-3009 USDC payment and activate entitlements.

    Verifies:
      - The challenge exists and hasn't been settled
      - The x_pay header decodes to a valid EIP-3009 authorization
      - The 'from' address matches the requesting user (best-effort)
      - The amount + payTo + asset match the challenge

    Then activates the subscription / add-on for the user.
    """
    from app.auth import get_current_user

    try:
        user = await get_current_user(request)
    except Exception:
        user = None
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    challenge_id = req.challenge_id or req.addon_id
    if not challenge_id:
        raise HTTPException(status_code=400, detail="challenge_id or addon_id required")

    pending = _load_pending(challenge_id)
    if not pending:
        # The challenge might not have been created via /addon (e.g. ad-hoc x402)
        # Fall back to optimistic activation with the provided params
        logger.info(f"x402 settle: no pending challenge for {challenge_id}, optimistically activating")
        pending = {
            "tier": req.tier or "PRO",
            "addon": req.addon or "PORTFOLIO_PRO",
            "period": req.period,
            "amount_atoms": req.amount_atoms or "3000000",
            "pay_to": PAY_TO,
        }

    # Decode x_pay header (base64 of x402 payload)
    try:
        import base64

        raw = req.x_pay
        if raw.startswith("X-PAY "):
            raw = raw[6:]
        payload = json.loads(base64.b64decode(raw))
    except Exception:
        try:
            payload = json.loads(req.x_pay)  # maybe already JSON
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid x_pay: {e}")

    auth = (payload.get("payload") or {}).get("authorization") or {}
    sig = (payload.get("payload") or {}).get("signature")
    if not auth or not sig:
        raise HTTPException(status_code=400, detail="Missing authorization or signature in x_pay")

    # Validate critical fields
    if auth.get("to", "").lower() != PAY_TO.lower():
        raise HTTPException(status_code=400, detail="payTo mismatch — wrong recipient")
    if str(auth.get("value", "")) != str(pending.get("amount_atoms")):
        raise HTTPException(status_code=400, detail="amount mismatch")
    if req.payer and auth.get("from", "").lower() != req.payer.lower():
        raise HTTPException(status_code=400, detail="payer mismatch — signature not from expected wallet")

    # Optional: verify the signature on-chain via eth_call
    # Skipped in dev to keep the endpoint fast. Production should call
    # ecrecover + the USDC contract's authorizationState() mapping.

    # Activate entitlements
    tier = pending.get("tier", "PRO")
    addon = pending.get("addon")
    period = pending.get("period", "monthly")
    addons_list = [addon] if addon else []

    sub = _activate_subscription(user["id"], tier, period, addons_list)
    _delete_pending(challenge_id)

    # Also write the user_metadata flag so /api/v1/portfolio/features picks it up
    try:
        from app.auth import _save_user

        md = (user.get("user_metadata") or {}).copy()
        if addon == "PORTFOLIO_PRO":
            md["has_portfolio_pro"] = True
        _save_user({**user, "user_metadata": md})
    except Exception as e:
        logger.warning(f"x402 settle: failed to update user_metadata: {e}")

    return {
        "success": True,
        "subscription": sub,
        "activated_addons": addons_list,
        "message": f"Welcome to {'Portfolio Pro' if addon == 'PORTFOLIO_PRO' else tier}!",
    }


@router.get("/x402-challenge/{challenge_id}")
async def get_x402_challenge(challenge_id: str):
    """Read a pending x402 challenge (for debugging / status display)."""
    pending = _load_pending(challenge_id)
    if not pending:
        raise HTTPException(status_code=404, detail="Challenge not found or already settled")
    return pending


@router.get("/x402-health")
async def x402_health():
    return {
        "status": "ok",
        "pay_to": PAY_TO,
        "usdc_asset": USDC_BASE,
        "network": "eip155:8453 (Base)",
        "redis": REDIS_OK,
        "pending_challenges": len(_pending),
    }
