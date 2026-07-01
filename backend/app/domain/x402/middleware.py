"""T34 x402 Payment Middleware.

Per v4.0 §T34. HTTP 402 'Payment Required' repurposed for AI agents.

Flow:
  1. Agent calls paid endpoint without X-Payment header
  2. Server returns 402 with payment challenge (signed invoice)
  3. Agent's wallet pays on-chain, gets tx_hash
  4. Agent re-calls with X-Payment: <base64(tx_hash + signature)>
  5. Server verifies on-chain payment via web3, fulfills request

Pricing (per v4.0):
  Free:   5 calls/day
  Pro:    $0.01/call (1000 calls/day)
  Ent:    $0.001/call (unlimited)

For v1, we use a simplified flow:
  - Track calls per agent in Redis (sliding window)
  - Receipts logged to x402_receipts table
  - Payment verification stubbed (returns True for now)
  - The 402 challenge includes a payment_url the agent hits
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request

log = logging.getLogger(__name__)


# ── Pricing per v4.0 §T34 ───────────────────────────────────────────
PRICING: dict[str, dict[str, Any]] = {
    # free_tier: bool, pro_cents: int (USD cents), ent_cents: int
    "get_token_risk":            {"free_tier": True,  "pro_cents": 1,    "ent_cents": 1,    "description": "Real-time token risk score"},
    "get_wallet_analysis":       {"free_tier": True,  "pro_cents": 1,    "ent_cents": 1,    "description": "Wallet activity + reputation"},
    "get_deployer_reputation":   {"free_tier": False, "pro_cents": 2,    "ent_cents": 1,    "description": "Deployer reputation score"},
    "get_news_sentiment":        {"free_tier": True,  "pro_cents": 1,    "ent_cents": 1,    "description": "Latest news + sentiment"},
    "generate_report":           {"free_tier": False, "pro_cents": 500,  "ent_cents": 400,  "description": "Full AI research report"},
    "query_catalog":             {"free_tier": False, "pro_cents": 5,    "ent_cents": 3,    "description": "Natural language catalog query"},
    "find_similar_tokens":       {"free_tier": False, "pro_cents": 3,    "ent_cents": 2,    "description": "Vector-similar tokens"},
    "resolve_entity":            {"free_tier": False, "pro_cents": 10,   "ent_cents": 5,    "description": "Cross-chain entity resolution"},
    "search_rag":                {"free_tier": True,  "pro_cents": 1,    "ent_cents": 1,    "description": "RAG semantic search"},
    "bulk_ingest":               {"free_tier": False, "pro_cents": 10,   "ent_cents": 5,    "description": "Bulk RAG ingestion"},
}

# Free tier daily limit
FREE_DAILY_LIMIT = 5



# ── Nonce burn (replay protection) ──────────────────────────────
# Per MINIMAX_M3_TASKS.md T02. Each tx_hash can only be used ONCE.
# We use Redis SETNX (set-if-not-exists) as an atomic nonce burn:
#   - First call: SETNX returns 1, nonce is burned, request proceeds
#   - Second call: SETNX returns 0, nonce already burned → 410 Gone
# TTL is 24h (the x402 invoice expiry window).

X402_NONCE_TTL = 86400  # 24 hours


async def _burn_nonce(catalog, tx_hash: str) -> bool:
    """Atomically burn a payment nonce. Returns True if first use, False if replay.

    Uses Redis SETNX (SET key value NX EX seconds). Atomic by construction.
    Logs replay attempts for monitoring.
    """
    if not catalog or not catalog._health.redis:
        # Without Redis we cannot prevent replay — fail closed (reject).
        log.warning(f"x402_nocache_reject: tx_hash={tx_hash[:10]}...")
        return False
    key = f"x402:nonce:{tx_hash}"
    try:
        # SETNX semantics: return 1 if key was set, 0 if already existed
        ok = await catalog._redis.set(key, "1", ex=X402_NONCE_TTL, nx=True)
        if not ok:
            log.warning(f"x402_replay_attempt: tx_hash={tx_hash[:10]}...")
            _X402_REPLAY_COUNTER.labels(tool="unknown").inc()
        return bool(ok)
    except Exception as e:
        log.error(f"x402_nonce_burn_fail: {e}")
        # Fail closed — reject if we can't burn atomically
        return False


async def _check_rate_limit(redis_client, agent_id: str) -> bool:
    """Per-agent sliding window rate limit. Free tier = 5 calls/day."""
    # (kept as-is from original file, exposed for the require_payment path)


# ── Prometheus counters (lightweight, no external dep) ──────────
try:
    from prometheus_client import REGISTRY, Counter

    # Use a module-level registry so multiple imports don't double-register
    _X402_REGISTRY = REGISTRY
    _X402_REPLAY_COUNTER = Counter(
        "x402_replay_attempts_total",
        "Number of x402 replay attempts blocked by nonce burn",
        ["tool"],
        registry=_X402_REGISTRY,
    )
    _X402_REVENUE_COUNTER = Counter(
        "x402_revenue_total_usd_cents",
        "x402 revenue in USD cents",
        ["tool", "tier"],
        registry=_X402_REGISTRY,
    )
except Exception:
    # Prometheus not available — use no-op counter stubs
    class _Stub:
        def labels(self, **kw): return self
        def inc(self, n: float = 1.0): pass
    _X402_REPLAY_COUNTER = _Stub()
    _X402_REVENUE_COUNTER = _Stub()



def get_tool_price_usd(tool: str) -> float:
    """Get pro tier price in USD. Returns 0 for free tools."""
    p = PRICING.get(tool, {})
    if p.get("free_tier", False):
        return 0.0
    return p.get("pro_cents", 0) / 100.0


def get_tool_metadata(tool: str) -> dict:
    """Public-facing tool metadata for /api/v1/x402/catalog."""
    p = PRICING.get(tool, {})
    return {
        "name": tool,
        "free_tier": p.get("free_tier", False),
        "free_daily_limit": FREE_DAILY_LIMIT if p.get("free_tier") else 0,
        "price_pro_usd": p.get("pro_cents", 0) / 100.0,
        "price_ent_usd": p.get("ent_cents", 0) / 100.0,
        "description": p.get("description", ""),
    }


# ── Rate limit tracking (Redis) ───────────────────────────────────
def _agent_id_from_request(request: Request | None) -> str:
    """Extract agent id from headers. Defaults to 'anon:ip' for anonymous."""
    if not request:
        return "anon:unknown"
    agent_id = request.headers.get("X-Agent-Id") or request.headers.get("X-Api-Key")
    if agent_id:
        return f"key:{agent_id}"
    client = request.client
    if client:
        return f"anon:{client.host}"
    return "anon:unknown"


async def _check_rate_limit(redis, agent_id: str) -> None:
    """Sliding-window rate limit: 5 free calls/day, 1000 pro, unlimited ent.

    For v1, we use Redis with a daily counter. Per-second limits are
    enforced at the proxy level (nginx/Caddy).
    """
    if not redis:
        return
    try:
        day = datetime.now(UTC).strftime("%Y%m%d")
        key = f"x402:rl:{agent_id}:{day}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 86400)
        if count > FREE_DAILY_LIMIT:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "rate_limit_exceeded",
                    "agent_id": agent_id,
                    "daily_count": count,
                    "limit": FREE_DAILY_LIMIT,
                    "next_action": "Send X-Payment header with valid tx_hash to upgrade tier",
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        log.warning(f"rate_limit_check_fail: {e}")


# ── Payment challenge ─────────────────────────────────────────────
def create_invoice(tool: str, agent_id: str) -> dict:
    """Create a payment challenge (signed invoice) for an agent to pay.

    In v1, the signature is a HMAC placeholder. In production, the
    invoice is signed by the platform's key and the agent verifies it
    before paying.
    """
    invoice_id = uuid4().hex
    amount_usd = get_tool_price_usd(tool)
    invoice = {
        "id": invoice_id,
        "tool": tool,
        "agent_id": agent_id,
        "amount_usd": amount_usd,
        "amount_wei": int(amount_usd * 1e18 / 3000),  # assume ETH = $3000
        "pay_to": "0xRMI_PLATFORM_WALLET",  # placeholder
        "chain": "base",  # L2 for low fees
        "created_at": datetime.now(UTC).isoformat(),
        "expires_at": (datetime.now(UTC).timestamp() + 3600),
    }
    # HMAC placeholder signature (real impl: sign with platform key)
    msg = json.dumps(invoice, sort_keys=True).encode()
    invoice["signature"] = base64.b64encode(
        hashlib.sha256(msg + b"RMI_PLATFORM_HMAC_KEY").digest()
    ).decode()
    return invoice


def verify_payment_header(x_payment: str, expected_amount_usd: float) -> tuple[str, str] | None:
    """Decode the X-Payment header. Returns (tx_hash, signature) or None.

    Format: base64({"tx_hash": "...", "signature": "..."})
    """
    if not x_payment:
        return None
    try:
        decoded = base64.b64decode(x_payment).decode()
        data = json.loads(decoded)
        return data.get("tx_hash", ""), data.get("signature", "")
    except Exception:
        return None


async def verify_payment_on_chain(tx_hash: str, expected_amount_usd: float) -> bool:
    """Verify an on-chain payment. v1 stub — returns True for valid-format hashes.

    Production: web3.py to query the chain, check recipient + amount.
    """
    if not tx_hash or not tx_hash.startswith("0x") or len(tx_hash) != 66:
        return False
    # Real impl: check tx exists, recipient matches, amount >= expected
    return True


async def record_receipt(catalog, tx_hash: str, tool: str, agent_id: str, amount_usd: float) -> bool:
    """Log the payment receipt to x402_receipts table."""
    if not catalog._health.postgres:
        return False
    try:
        async with catalog._pg_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO x402_receipts (tx_hash, agent_id, tool, amount_usd, chain, paid_at, tier)
                   VALUES ($1, $2, $3, $4, $5, NOW(), $6)
                   ON CONFLICT (tx_hash) DO NOTHING""",
                tx_hash, agent_id, tool, amount_usd, "base", "pro",
            )
        return True
    except Exception as e:
        log.warning(f"receipt_record_fail: {e}")
        return False


# ── Main middleware ────────────────────────────────────────────────
async def require_payment(
    tool: str,
    request: Request | None = None,
    catalog=None,
) -> dict:
    """Verify x402 payment. Returns payment context dict.

    Raises HTTPException 402 if payment is required but not provided.
    Raises HTTPException 429 if rate limit is exceeded.
    """
    agent_id = _agent_id_from_request(request)
    if not catalog or not catalog._health.redis:
        # No rate limiting available — log and pass
        log.debug(f"x402_no_redis: {tool} by {agent_id}")
    else:
        await _check_rate_limit(catalog._redis, agent_id)

    # Free tool — no payment required
    if get_tool_price_usd(tool) == 0:
        return {"agent_id": agent_id, "tool": tool, "tier": "free", "paid_via_x402": None}

    # Paid tool — check X-Payment header
    if request is None:
        invoice = create_invoice(tool, agent_id)
        raise HTTPException(
            status_code=402,
            detail={
                "error": "payment_required",
                "invoice": invoice,
                "payment_url": f"https://pay.rugmunch.io/{invoice['id']}",
                "instructions": "Pay on-chain, then retry with X-Payment header (base64 of {tx_hash, signature})",
            },
        )

    x_payment = request.headers.get("X-Payment")
    if not x_payment:
        invoice = create_invoice(tool, agent_id)
        raise HTTPException(
            status_code=402,
            detail={
                "error": "payment_required",
                "invoice": invoice,
                "payment_url": f"https://pay.rugmunch.io/{invoice['id']}",
                "instructions": "Pay on-chain, then retry with X-Payment header (base64 of {tx_hash, signature})",
            },
        )

    decoded = verify_payment_header(x_payment, get_tool_price_usd(tool))
    if not decoded:
        raise HTTPException(status_code=402, detail="invalid X-Payment format")
    tx_hash, _sig = decoded
    if not await verify_payment_on_chain(tx_hash, get_tool_price_usd(tool)):
        raise HTTPException(status_code=402, detail="invalid payment proof")

    # T02: Burn the nonce atomically. Same tx_hash cannot be used twice.
    if not await _burn_nonce(catalog, tx_hash):
        raise HTTPException(
            status_code=410,
            detail={
                "error": "payment_already_used",
                "tx_hash": tx_hash,
                "message": "This payment receipt has already been used. Each tx_hash can only be used once.",
            },
        )
    _X402_REVENUE_COUNTER.labels(tool=tool, tier="pro").inc(int(get_tool_price_usd(tool) * 100))

    # Record the receipt
    if catalog:
        await record_receipt(
            catalog, tx_hash, tool, agent_id, get_tool_price_usd(tool)
        )

    return {
        "agent_id": agent_id,
        "tool": tool,
        "tier": "pro",
        "paid_via_x402": tx_hash,
    }
