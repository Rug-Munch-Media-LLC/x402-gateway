"""The x402 402 Payment Required body and its chain constants.

Kept apart from the verifier so the accepted-network/asset table has one
home and the verification steps stay inside the size ceilings.
"""

import os
from collections.abc import Mapping
from typing import Any

PAYMENT_NETWORKS = (
    "base",
    "solana",
    "ethereum",
    "bsc",
    "arbitrum",
    "optimism",
    "polygon",
    "avalanche",
)

USDC_ADDRESSES = {
    "base": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "solana": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "ethereum": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
}

DEFAULT_PAY_TO = "0x1E3AC01d0fdb976179790BDD02823196A92705C9"


def payment_required_detail(
    tool_id: str,
    price_usd: float,
    pricing: Mapping[str, Any],
    x402_version: Any,
    identifier: str,
) -> dict[str, Any]:
    """The 402 body: price, accepted networks, pay-to address, trial state."""
    return {
        "error": "payment_required",
        "tool_id": tool_id,
        "price_usd": price_usd,
        "payment_required": {
            "x402_version": x402_version,
            "max_amount": f"{price_usd:.4f}",
            "payment_type": "USDC",
            "networks": list(PAYMENT_NETWORKS),
            "usdc_addresses": dict(USDC_ADDRESSES),
            "pay_to": os.getenv("X402_EVM_PAY_TO", DEFAULT_PAY_TO),
            "wallet_connect": True,
            "connect_wallet_for_trials": pricing.get("trial_free", 0) > 0,
        },
        "trial_free": pricing.get("trial_free", 0),
        "identifier": identifier,
    }
