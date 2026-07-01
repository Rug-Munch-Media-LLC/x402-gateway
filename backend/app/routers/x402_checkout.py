"""
T1-1: x402 Subscription Checkout — Complete payment flow for RMI subscriptions.
"""
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("x402_checkout")
DB_PATH = "/data/x402_checkout.db"

FACILITATOR_URL = os.getenv("X402_FACILITATOR", "https://x402.org/facilitator")

PLANS = {
    "free": {"name": "Free", "price_usd": 0, "scans_per_day": 10, "api_rate": 60, "features": ["basic_scan"]},
    "bronze": {"name": "Bronze", "price_usd": 9.99, "scans_per_day": 50, "api_rate": 120, "features": ["basic_scan", "whale_alerts", "webhook"]},
    "silver": {"name": "Silver", "price_usd": 29.99, "scans_per_day": 200, "api_rate": 300, "features": ["basic_scan", "whale_alerts", "webhook", "custom_dashboard", "priority_support"]},
    "gold": {"name": "Gold", "price_usd": 99.99, "scans_per_day": 1000, "api_rate": 1000, "features": ["all_features", "alpha_access", "dedicated_support"]},
    "platinum": {"name": "Platinum", "price_usd": 299.99, "scans_per_day": -1, "api_rate": 5000, "features": ["all_features", "white_glove", "custom_integration"]},
}

class CheckoutRequest(BaseModel):
    plan: str
    wallet_address: str
    chain: str = "solana"
    months: int = Field(default=1, ge=1, le=12)

class PaymentVerify(BaseModel):
    tx_hash: str
    checkout_id: str

class Subscription(BaseModel):
    id: str
    wallet_address: str
    plan: str
    expires_at: str
    status: str = "active"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions (
        id TEXT PRIMARY KEY, wallet_address TEXT, plan TEXT, chain TEXT,
        tx_hash TEXT, amount_usd REAL, months INTEGER, status TEXT DEFAULT "pending",
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, expires_at TIMESTAMP)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_sub_wallet ON subscriptions(wallet_address)''')
    conn.commit(); conn.close()
init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; return conn

router = APIRouter(prefix="/checkout", tags=["x402 Checkout"])

@router.get("/plans")
async def list_plans():
    """List available subscription plans."""
    return {"plans": PLANS}

@router.post("/initiate")
async def initiate_checkout(req: CheckoutRequest):
    """Initiate a subscription checkout."""
    if req.plan not in PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan")

    plan = PLANS[req.plan]
    if plan["price_usd"] == 0:
        # Free plan — no payment needed
        checkout_id = f"free_{req.wallet_address[:8]}_{int(datetime.utcnow().timestamp())}"
        conn = get_db()
        try:
            c = conn.cursor()
            expires = (datetime.utcnow() + timedelta(days=365)).isoformat()
            c.execute('''INSERT INTO subscriptions (id, wallet_address, plan, chain, amount_usd, months, status, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (checkout_id, req.wallet_address, req.plan, req.chain, 0, req.months, "active", expires))
            conn.commit()
            return {"checkout_id": checkout_id, "plan": req.plan, "amount_usd": 0, "status": "active", "payment_required": False}
        finally: conn.close()

    # Paid plan — generate payment request
    checkout_id = f"chk_{req.wallet_address[:8]}_{int(datetime.utcnow().timestamp())}"
    amount = plan["price_usd"] * req.months

    conn = get_db()
    try:
        c = conn.cursor()
        c.execute('''INSERT INTO subscriptions (id, wallet_address, plan, chain, amount_usd, months, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (checkout_id, req.wallet_address, req.plan, req.chain, amount, req.months, "pending"))
        conn.commit()
    finally: conn.close()

    return {
        "checkout_id": checkout_id,
        "plan": req.plan,
        "amount_usd": amount,
        "wallet_address": req.wallet_address,
        "status": "pending_payment",
        "payment_required": True,
        "payment_url": f"/checkout/pay/{checkout_id}",
    }

@router.get("/pay/{checkout_id}")
async def get_payment_page(checkout_id: str):
    """Get payment details for a checkout."""
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute('SELECT * FROM subscriptions WHERE id = ?', (checkout_id,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Checkout not found")

        plan = PLANS.get(row['plan'], {})
        return {
            "checkout_id": checkout_id,
            "plan": row['plan'],
            "plan_name": plan.get("name"),
            "amount_usd": row['amount_usd'],
            "wallet_address": row['wallet_address'],
            "chain": row['chain'],
            "months": row['months'],
            "status": row['status'],
            "features": plan.get("features", []),
        }
    finally: conn.close()

@router.post("/verify")
async def verify_payment(verify: PaymentVerify):
    """Verify payment and activate subscription."""
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute('SELECT * FROM subscriptions WHERE id = ?', (verify.checkout_id,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Checkout not found")

        if row['status'] == "active":
            return {"status": "already_active", "subscription_id": verify.checkout_id}

        # Verify tx on-chain (simplified)
        # In production: verify tx_hash against chain RPC
        tx_valid = True  # TODO: actual verification

        if tx_valid:
            expires = (datetime.utcnow() + timedelta(days=30 * row['months'])).isoformat()
            c.execute('UPDATE subscriptions SET status = ?, tx_hash = ?, expires_at = ? WHERE id = ?',
                      ("active", verify.tx_hash, expires, verify.checkout_id))
            conn.commit()

            return {
                "status": "active",
                "subscription_id": verify.checkout_id,
                "plan": row['plan'],
                "expires_at": expires,
            }
        else:
            raise HTTPException(status_code=400, detail="Payment verification failed")
    finally: conn.close()

@router.get("/subscription/{wallet_address}")
async def get_subscription(wallet_address: str):
    """Get active subscription for a wallet."""
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute('SELECT * FROM subscriptions WHERE wallet_address = ? AND status = "active" ORDER BY expires_at DESC LIMIT 1', (wallet_address,))
        row = c.fetchone()
        if not row:
            return {"wallet_address": wallet_address, "active": False, "plan": "free"}

        plan = PLANS.get(row['plan'], PLANS["free"])
        return {
            "wallet_address": wallet_address,
            "active": True,
            "plan": row['plan'],
            "plan_name": plan["name"],
            "features": plan["features"],
            "expires_at": row['expires_at'],
            "scans_remaining": plan["scans_per_day"],
        }
    finally: conn.close()

@router.get("/health")
async def health():
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) as total, SUM(CASE WHEN status="active" THEN 1 ELSE 0 END) as active FROM subscriptions')
        row = c.fetchone()
        return {"status": "healthy", "total_subscriptions": row['total'], "active": row['active']}
    finally: conn.close()
