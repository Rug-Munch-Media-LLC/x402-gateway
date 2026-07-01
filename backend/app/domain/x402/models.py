"""Pydantic v2 models for the x402 domain."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class X402Tier(StrEnum):
    """x402 payment tier — maps to scanner tiers."""

    FREE = "free"
    PRO = "pro"
    ELITE = "elite"
    INTERNAL = "internal"


class ToolPricing(BaseModel):
    """Per-tool pricing in x402."""

    model_config = ConfigDict(str_strip_whitespace=True)

    tool: str
    price_usd: float = Field(default=0.0, ge=0)
    price_crypto: dict[str, str] = Field(
        default_factory=dict,
        description="Crypto amounts: {'sol': '0.01', 'eth': '0.001', 'btc': '0.00001', 'trx': '5'}",
    )
    tier_required: X402Tier = X402Tier.FREE


class ToolCatalogEntry(BaseModel):
    """A single tool in the x402 catalog."""

    name: str
    description: str = ""
    category: str = ""
    tier_required: X402Tier = X402Tier.FREE
    price_usd: float = 0.0
    endpoint: str = ""
    enabled: bool = True


class ToolCatalog(BaseModel):
    """Full x402 tool catalog."""

    tools: list[ToolCatalogEntry] = Field(default_factory=list)
    total: int = 0
    categories: list[str] = Field(default_factory=list)
    fetched_at: str = ""


class PaymentFacilitator(BaseModel):
    """x402 payment facilitator info."""

    name: str
    url: str
    enabled: bool = True
    chains: list[str] = Field(default_factory=list)
    health: str = "unknown"


class PaymentRequest(BaseModel):
    """x402 payment request."""

    model_config = ConfigDict(str_strip_whitespace=True)

    tool: str
    chain: str = Field(default="solana")
    user_address: str | None = None

    @field_validator("chain")
    @classmethod
    def _chain_lowercase(cls, v: str) -> str:
        return v.lower().strip()


class PaymentReceipt(BaseModel):
    """x402 payment receipt."""

    tool: str
    chain: str
    tx_hash: str | None = None
    amount_paid: dict[str, str] = Field(default_factory=dict)
    status: str = "pending"  # pending | confirmed | failed
    timestamp: str = ""
