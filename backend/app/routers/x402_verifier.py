"""
x402 Payment Verification — Official SDK Integration
=====================================================
Single source of truth for x402 payment verification.

Replaces:
- Hand-rolled EIP-712 verification in x402_middleware.py::PaymentVerifier
- Facilitator router fallback chain in x402_enforcement.py::verify_payment_via_router
- Old verify_payment() in x402_enforcement.py

Uses official x402 SDK for ALL cryptographic operations:
- EIP-712 typed data signature verification
- EIP-3009 transferWithAuthorization validation
- Facilitator communication (PayAI, CDP, etc.)
- Settlement handling
"""

import logging
import os
from typing import Any

logger = logging.getLogger("x402_verifier")

# ---------------------------------------------------------------------------
# SDK Imports — fail fast if not installed
# ---------------------------------------------------------------------------

try:
    from x402 import X402_VERSION
    from x402.http import FacilitatorConfig, HTTPFacilitatorClient
    from x402.mechanisms.evm.exact import ExactEvmServerScheme
    from x402.schemas import Network
    from x402.server import x402ResourceServer

    SDK_VERSION = X402_VERSION
    SDK_AVAILABLE = True
    logger.info("x402_sdk_loaded", extra={"version": SDK_VERSION})
except ImportError as e:
    SDK_AVAILABLE = False
    SDK_VERSION = None
    logger.error(
        "x402_sdk_missing",
        extra={
            "error": str(e),
            "fix": "pip install 'x402[fastapi]'",
            "impact": "Payment verification will FAIL — no fallback available",
        },
    )
    # We don't silently degrade — payment verification fails fast
    raise RuntimeError("x402 SDK is required. Install with: pip install 'x402[fastapi]'") from e

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Facilitator URL — PayAI is production-ready, free, no API key needed
FACILITATOR_URL = os.environ.get("X402_FACILITATOR_URL", "https://facilitator.payai.network")

# Supported networks with USDC addresses
SUPPORTED_NETWORKS: dict[str, dict[str, str]] = {
    "eip155:8453": {  # Base mainnet
        "name": "Base",
        "usdc": "0x833589fCD6eDb6E08f4c7c32D4f71b54bdA02913",
        "pay_to_env": "X402_EVM_PAY_TO",
    },
    "eip155:84532": {  # Base Sepolia (test)
        "name": "Base Sepolia",
        "usdc": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "pay_to_env": "X402_EVM_PAY_TO",
    },
    "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp": {  # Solana mainnet
        "name": "Solana",
        "usdc": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "pay_to_env": "X402_SOL_PAY_TO",
    },
}

DEFAULT_NETWORK = os.environ.get("X402_DEFAULT_NETWORK", "eip155:8453")

# ---------------------------------------------------------------------------
# SDK Client — singleton, created once
# ---------------------------------------------------------------------------


class VerifierClient:
    """Single x402 verification client using the official SDK.

    Wraps x402ResourceServer + HTTPFacilitatorClient for use by our
    enforcement middleware. One instance, created at import time.
    """

    def __init__(self) -> None:
        if not SDK_AVAILABLE:
            raise RuntimeError("x402 SDK not available")

        self.facilitator = HTTPFacilitatorClient(FacilitatorConfig(url=FACILITATOR_URL))
        self.server = x402ResourceServer(self.facilitator)

        # Register EVM scheme for all eip155:* networks
        self.server.register("eip155:*", ExactEvmServerScheme())

        logger.info(
            "verifier_client_initialized",
            extra={
                "facilitator": FACILITATOR_URL,
                "networks": list(SUPPORTED_NETWORKS.keys()),
                "sdk_version": str(SDK_VERSION),
            },
        )

    async def verify_payment(
        self,
        payload: dict[str, Any],
        network: str | None = None,
    ) -> dict[str, Any]:
        """Verify an x402 payment payload using the official SDK.

        Args:
            payload: The payment payload from PAYMENT-SIGNATURE header.
                     Should contain 'accepted' with network, amount, asset,
                     'payload' with authorization + signature.
            network: Override network (e.g. 'eip155:8453'). Auto-detected from
                     payload if not provided.

        Returns:
            dict with keys: verified (bool), reason (str), tx_hash (str|None),
                            payer (str|None), amount (str|None)
        """
        # Auto-detect network from payload if not provided
        if network is None:
            accepted = payload.get("accepted", {})
            network = accepted.get("network", DEFAULT_NETWORK)

        try:
            # Build PaymentRequirements from payload
            requirements = self._build_requirements(payload)

            # Verify via SDK — this handles:
            # 1. EIP-712 signature verification
            # 2. Nonce checking
            # 3. Amount validation
            # 4. Network validation
            # 5. Facilitator communication
            result = await self.server.verify_payment(
                payload,
                requirements=requirements,
            )

            if result and getattr(result, "verified", False):
                return {
                    "verified": True,
                    "reason": getattr(result, "reason", "Verified via x402 SDK"),
                    "tx_hash": getattr(result, "tx_hash", None),
                    "payer": getattr(result, "payer", None),
                    "amount": getattr(result, "amount", None),
                    "network": network,
                }
            else:
                reason = "Verification failed"
                if result:
                    reason = getattr(result, "reason", str(result))
                return {
                    "verified": False,
                    "reason": reason,
                    "tx_hash": None,
                    "payer": None,
                    "amount": None,
                    "network": network,
                }

        except Exception as e:
            logger.error(
                "sdk_verification_error",
                extra={
                    "network": network,
                    "error": str(e)[:200],
                },
            )
            return {
                "verified": False,
                "reason": f"SDK verification error: {e!s}",
                "tx_hash": None,
                "payer": None,
                "amount": None,
                "network": network,
            }

    def _build_requirements(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Build PaymentRequirements dict from incoming payload."""
        accepted = payload.get("accepted", {})
        resource = payload.get("resource", {})

        return {
            "x402Version": payload.get("x402Version", 2),
            "resource": resource,
            "accepts": [
                {
                    "scheme": accepted.get("scheme", "exact"),
                    "network": accepted.get("network", DEFAULT_NETWORK),
                    "asset": accepted.get("asset", ""),
                    "amount": accepted.get("amount", ""),
                    "payTo": accepted.get("payTo", ""),
                    "maxTimeoutSeconds": accepted.get("maxTimeoutSeconds", 180),
                    "extra": accepted.get("extra", {}),
                }
            ],
        }

    async def settle_payment(
        self,
        payload: dict[str, Any],
        network: str | None = None,
    ) -> dict[str, Any]:
        """Settle a verified payment via the facilitator.

        Called after successful tool execution to finalize the payment.
        """
        if network is None:
            accepted = payload.get("accepted", {})
            network = accepted.get("network", DEFAULT_NETWORK)

        try:
            requirements = self._build_requirements(payload)
            result = await self.server.settle_payment(payload, requirements=requirements)
            return {
                "settled": True,
                "tx_hash": getattr(result, "tx_hash", None),
                "network": network,
            }
        except Exception as e:
            logger.error(
                "settlement_error",
                extra={"network": network, "error": str(e)[:200]},
            )
            return {
                "settled": False,
                "tx_hash": None,
                "network": network,
                "error": str(e),
            }


# Global singleton — created once, reused everywhere
verifier: VerifierClient | None = None


def get_verifier() -> VerifierClient:
    """Get or create the global verifier singleton."""
    global verifier
    if verifier is None:
        verifier = VerifierClient()
    return verifier
