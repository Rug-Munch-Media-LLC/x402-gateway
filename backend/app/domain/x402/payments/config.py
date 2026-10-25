"""Payment configuration, loaded once from the environment.

Secrets are read from env and never logged. If no invoice signing key is
configured a per-process key is generated instead of shipping a hardcoded one.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field

USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_BASE_SEPOLIA = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
USDC_DECIMALS = 6
BASE_NETWORK = "eip155:8453"

# USD cents -> USDC atoms (6 decimals): 1 cent = 10_000 atoms.
ATOMS_PER_CENT = 10 ** (USDC_DECIMALS - 2)


@dataclass(frozen=True)
class PaymentConfig:
    """Immutable settings for one payment surface."""

    network: str = BASE_NETWORK
    chain: str = "base"
    asset: str = USDC_BASE
    pay_to: str = ""
    max_timeout_seconds: int = 180
    required_confirmations: int = 12
    hmac_secret: bytes = field(default=b"", repr=False)

    def __post_init__(self) -> None:
        if not self.network or not self.chain:
            raise ValueError("network and chain must be set")
        if self.max_timeout_seconds <= 0:
            raise ValueError("max_timeout_seconds must be positive")
        if self.hmac_secret == b"":
            object.__setattr__(self, "hmac_secret", secrets.token_bytes(32))


def load_config(env: Mapping[str, str] | None = None) -> PaymentConfig:
    """Build the config from env vars, tolerating absent optional values."""
    env = os.environ if env is None else env
    network = env.get("X402_NETWORK", BASE_NETWORK)
    pay_to = env.get("X402_EVM_PAY_TO") or env.get("X402_PAY_TO_ADDRESS") or ""
    return PaymentConfig(
        network=network,
        chain=env.get("X402_CHAIN", "base"),
        asset=env.get("X402_ASSET", USDC_BASE),
        pay_to=pay_to,
        max_timeout_seconds=int(env.get("X402_MAX_TIMEOUT_SECONDS", "180")),
        required_confirmations=int(env.get("X402_REQUIRED_CONFIRMATIONS", "12")),
        hmac_secret=(env.get("X402_INVOICE_HMAC_SECRET") or "").encode() or b"",
    )
