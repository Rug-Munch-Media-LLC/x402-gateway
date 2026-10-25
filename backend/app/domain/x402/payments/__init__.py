"""x402 payment core — verification, claims, settlement.

Import from here; the modules stay one-primary-function on purpose.
"""

from app.domain.x402.payments.config import PaymentConfig, load_config
from app.domain.x402.payments.errors import (
    AmountMismatch,
    AssetMismatch,
    ChainMismatch,
    InvalidPaymentHeader,
    PaymentError,
    PaymentExpired,
    PaymentReplay,
    PaymentRequired,
    RecipientMismatch,
    SettlementError,
    VerificationFailed,
    to_http_exception,
)
from app.domain.x402.payments.gate import PaymentGate
from app.domain.x402.payments.header import PaymentProof, decode_payment_header
from app.domain.x402.payments.nonce import (
    ClaimOutcome,
    MemoryNonceStore,
    NonceStore,
    RedisNonceStore,
)
from app.domain.x402.payments.requirements import Requirements, build_requirements
from app.domain.x402.payments.settle import SettlementReceipt, settle_payment
from app.domain.x402.payments.verifier import (
    ChainObservation,
    ChainReader,
    VerifiedPayment,
    verify_payment,
)

__all__ = [
    "AmountMismatch",
    "AssetMismatch",
    "ChainMismatch",
    "ChainObservation",
    "ChainReader",
    "ClaimOutcome",
    "InvalidPaymentHeader",
    "MemoryNonceStore",
    "NonceStore",
    "PaymentConfig",
    "PaymentError",
    "PaymentExpired",
    "PaymentGate",
    "PaymentProof",
    "PaymentReplay",
    "PaymentRequired",
    "RecipientMismatch",
    "RedisNonceStore",
    "Requirements",
    "SettlementError",
    "SettlementReceipt",
    "VerificationFailed",
    "VerifiedPayment",
    "build_requirements",
    "decode_payment_header",
    "load_config",
    "settle_payment",
    "to_http_exception",
    "verify_payment",
]
