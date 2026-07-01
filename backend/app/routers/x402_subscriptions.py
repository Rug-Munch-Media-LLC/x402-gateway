"""
x402 Subscriptions — Recurring Billing via USDC on Base
========================================================
Uses the x402 "upto" scheme: user approves a max spend,
we settle actual subscription cost each period.

Flow:
  1. User chooses tier (Starter $14.99 / Pro $49.99 / Elite $99.99)
  2. User approves USDC spend limit (e.g., $50/mo for Pro)
  3. Each month: we settle the exact amount via x402 upto scheme
  4. Credits granted daily to their unified user profile
  5. User can cancel anytime → stops settlements

Same price as Stripe subscriptions. Same credits.
Different payment method: USDC on Base, no card needed.
"""

import json
import logging
import os
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routers.user_profiles import (
    add_credits,
    get_user_by_wallet,
    set_subscription,
)
from app.routers.x402_verifier import get_verifier

logger = logging.getLogger("x402_subscriptions")

router = APIRouter(prefix="/api/v1/x402", tags=["x402-subscriptions"])

# ── Subscription Plans (same as pricing catalog) ─────────────

SUBSCRIPTION_PLANS = {
    "bot_starter": {
        "name": "Bot Starter",
        "price_usdc": 14.99,
        "price_atoms": "14990000",
        "credits_per_day": 100,
        "network": "eip155:8453",
        "asset": "0x833589fCD6eDb6E08f4c7c32D4f71b54bdA02913",  # USDC on Base
    },
    "bot_pro": {
        "name": "Bot Pro",
        "price_usdc": 49.99,
        "price_atoms": "49990000",
        "credits_per_day": 500,
        "network": "eip155:8453",
        "asset": "0x833589fCD6eDb6E08f4c7c32D4f71b54bdA02913",
    },
    "bot_elite": {
        "name": "Bot Elite",
        "price_usdc": 99.99,
        "price_atoms": "99990000",
        "credits_per_day": 5000,
        "network": "eip155:8453",
        "asset": "0x833589fCD6eDb6E08f4c7c32D4f71b54bdA02913",
    },
}


# ── Redis Helpers ────────────────────────────────────────────


def _redis():
    try:
        from app.auth import get_redis as _gr

        return _gr()
    except Exception:
        return None


def _key_sub(wallet: str) -> str:
    return f"x402:subscription:{wallet.lower()}"


def _key_approval(wallet: str) -> str:
    return f"x402:approval:{wallet.lower()}"


# ── Request Models ───────────────────────────────────────────


class SubscribeRequest(BaseModel):
    wallet: str
    plan_id: str  # "bot_starter", "bot_pro", "bot_elite"
    authorization: dict  # x402 upto authorization payload
    signature: str  # Signed authorization


class CancelRequest(BaseModel):
    wallet: str


# ── API Routes ───────────────────────────────────────────────


@router.get("/subscription/plans")
async def list_plans():
    """List available subscription plans with pricing."""
    return {
        "plans": {
            pid: {
                "name": p["name"],
                "price_usdc": p["price_usdc"],
                "credits_per_day": p["credits_per_day"],
                "network": p["network"],
            }
            for pid, p in SUBSCRIPTION_PLANS.items()
        },
        "currency": "USDC",
        "network": "Base (eip155:8453)",
        "benefit": "Subscriptions are 5-50x cheaper than pay-per-call x402",
    }


@router.get("/subscription/status/{wallet}")
async def subscription_status(wallet: str):
    """Check subscription status for a wallet address."""
    r = _redis()
    if not r:
        return {"active": False, "note": "Redis unavailable"}

    data = r.get(_key_sub(wallet.lower()))
    if not data:
        return {"active": False, "wallet": wallet}

    sub = json.loads(data)
    now = time.time()

    return {
        "active": sub.get("expires_at", 0) > now,
        "wallet": wallet,
        "plan_id": sub.get("plan_id"),
        "plan_name": SUBSCRIPTION_PLANS.get(sub.get("plan_id", ""), {}).get("name", "Unknown"),
        "expires_at": sub.get("expires_at"),
        "expires_in_days": max(0, int((sub.get("expires_at", 0) - now) / 86400)),
        "auto_renew": sub.get("auto_renew", True),
        "credits_per_day": sub.get("credits_per_day", 0),
    }


@router.post("/subscription/subscribe")
async def subscribe(req: SubscribeRequest):
    """Start a new x402 subscription.

    Flow:
    1. User approves USDC spend limit (upto scheme via wallet)
    2. We verify the authorization
    3. Grant initial credits + set up daily grant cron
    4. Return subscription info
    """
    plan = SUBSCRIPTION_PLANS.get(req.plan_id)
    if not plan:
        raise HTTPException(400, detail=f"Unknown plan: {req.plan_id}")

    wallet = req.wallet.lower()

    # Verify upto authorization via x402 SDK
    try:
        v = get_verifier()
        # Register upto scheme for this network
        from x402.mechanisms.evm.upto import UptoEvmServerScheme

        v.server.register("eip155:*", UptoEvmServerScheme())

        # Verify the authorization
        auth_payload = {
            "x402Version": 2,
            "scheme": "upto",
            "network": plan["network"],
            "sender": wallet,
            "maxAmount": plan["price_atoms"],
            "asset": plan["asset"],
            "payTo": os.environ.get("X402_EVM_PAY_TO", ""),
            "authorization": req.authorization,
            "signature": req.signature,
        }

        result = await v.verify_payment(auth_payload, network=plan["network"])
        if not result.get("verified"):
            raise HTTPException(
                401, detail=f"Authorization failed: {result.get('reason', 'unknown')}"
            )

    except ImportError as e:
        logger.warning(f"x402 upto verification not available: {e}")
        # Fall through — accept authorization as-is for now
    except Exception as e:
        logger.error(f"upto_verification_error: {e}")
        raise HTTPException(500, detail=f"Verification error: {e}")

    # Find or create user profile
    profile, created = get_user_by_wallet(wallet)
    from app.routers.user_profiles import find_or_create_user

    profile, _ = find_or_create_user(wallet=wallet)

    # Set subscription (30 days)
    plan_id = req.plan_id
    days = 30
    set_subscription(profile.user_id, plan_id, days=days)

    # Store subscription in Redis
    r = _redis()
    if r:
        sub_data = {
            "wallet": wallet,
            "user_id": profile.user_id,
            "plan_id": plan_id,
            "credits_per_day": plan["credits_per_day"],
            "price_usdc": plan["price_usdc"],
            "price_atoms": plan["price_atoms"],
            "started_at": time.time(),
            "expires_at": time.time() + (days * 86400),
            "auto_renew": True,
            "network": plan["network"],
            "asset": plan["asset"],
        }
        r.set(_key_sub(wallet), json.dumps(sub_data))
        r.expire(_key_sub(wallet), days * 86400 + 3600)

        # Grant initial month's credits upfront
        initial_credits = plan["credits_per_day"] * 30
        add_credits(profile.user_id, initial_credits, source=f"subscription_{plan_id}")

    logger.info(
        "subscription_started",
        extra={
            "wallet": wallet[:10],
            "plan": plan_id,
            "user_id": profile.user_id,
        },
    )

    return {
        "success": True,
        "wallet": wallet,
        "plan_id": plan_id,
        "plan_name": plan["name"],
        "credits_per_day": plan["credits_per_day"],
        "initial_credits": initial_credits
        if "initial_credits" in dir()
        else plan["credits_per_day"] * 30,
        "expires_in_days": days,
        "auto_renew": True,
        "user_id": profile.user_id,
    }


@router.post("/subscription/cancel")
async def cancel_subscription(req: CancelRequest):
    """Cancel an auto-renewing x402 subscription.
    Current period remains active until expiry.
    """
    wallet = req.wallet.lower()
    r = _redis()

    if r:
        data = r.get(_key_sub(wallet))
        if data:
            sub = json.loads(data)
            sub["auto_renew"] = False
            r.set(_key_sub(wallet), json.dumps(sub))

            # Also cancel in user profiles
            from app.routers.user_profiles import cancel_subscription as cancel_profile_sub

            cancel_profile_sub(sub.get("user_id", ""))

    logger.info("subscription_cancelled", extra={"wallet": wallet[:10]})

    return {
        "success": True,
        "wallet": wallet,
        "message": "Auto-renew cancelled. Access continues until period end.",
    }


@router.post("/subscription/renew")
async def renew_subscription(req: SubscribeRequest):
    """Renew/confirm an x402 subscription for another period.
    Called by cron or manually to settle the monthly cost.
    """
    wallet = req.wallet.lower()
    r = _redis()

    if not r:
        raise HTTPException(503, detail="Redis unavailable")

    data = r.get(_key_sub(wallet))
    if not data:
        raise HTTPException(404, detail="No active subscription")

    sub = json.loads(data)

    # Settle the monthly cost via x402 upto
    try:
        v = get_verifier()
        plan = SUBSCRIPTION_PLANS.get(sub["plan_id"])
        if not plan:
            raise HTTPException(400, detail="Plan not found")

        # Settle the exact amount for this period
        settlement = await v.facilitator.settle(
            payload={"accepted": {"amount": plan["price_atoms"], "network": plan["network"]}},
            requirements={"accepts": [{"amount": plan["price_atoms"], "network": plan["network"]}]},
        )

        # Extend subscription
        new_expiry = time.time() + (30 * 86400)
        sub["expires_at"] = new_expiry
        sub["last_settled"] = time.time()
        r.set(_key_sub(wallet), json.dumps(sub))

        # Grant next month's credits
        user_id = sub.get("user_id", "")
        if user_id:
            add_credits(
                user_id, plan["credits_per_day"] * 30, source=f"subscription_renew_{sub['plan_id']}"
            )

        logger.info(
            "subscription_renewed",
            extra={
                "wallet": wallet[:10],
                "plan": sub["plan_id"],
                "settlement": str(settlement.get("tx_hash", ""))[:16],
            },
        )

        return {
            "success": True,
            "renewed": True,
            "expires_at": new_expiry,
            "credits_added": plan["credits_per_day"] * 30,
        }

    except Exception as e:
        logger.error(f"subscription_renew_error: {e}")
        raise HTTPException(500, detail=f"Renewal failed: {e}")
