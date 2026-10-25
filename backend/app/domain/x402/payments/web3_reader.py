"""web3 adapter for the ChainReader protocol.

This is the only module that talks to an RPC endpoint. It observes; it never
decides whether the payment is acceptable (the verifier does that).
"""

from __future__ import annotations

import logging
import os

from app.domain.x402.payments.config import PaymentConfig
from app.domain.x402.payments.errors import VerificationFailed
from app.domain.x402.payments.header import PaymentProof
from app.domain.x402.payments.verifier import ChainObservation

logger = logging.getLogger("x402_web3_reader")

DEFAULT_RPC = "https://mainnet.base.org"


class Web3ChainReader:
    """Reads a transaction/receipt pair from an EVM node."""

    def __init__(self, config: PaymentConfig, rpc_url: str | None = None) -> None:
        self._config = config
        self._rpc_url = rpc_url or os.environ.get("X402_RPC_URL_BASE", DEFAULT_RPC)

    async def observe(self, proof: PaymentProof) -> ChainObservation | None:
        """Return the payment observation, or None when the tx is unknown.

        Raises VerificationFailed when the node cannot be used at all: an
        unreachable RPC must never read as "unpaid but fine".
        """
        if not proof.tx_hash:
            return None
        try:
            from web3 import AsyncWeb3
        except ImportError as exc:
            raise VerificationFailed("web3 is not installed") from exc

        w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(self._rpc_url))
        try:
            tx = await w3.eth.get_transaction(proof.tx_hash)
            receipt = await w3.eth.get_transaction_receipt(proof.tx_hash)
            latest = await w3.eth.block_number
        except Exception as exc:  # node/transport failure
            logger.warning(
                "x402_rpc_read_failed tx=%s error=%s", proof.tx_hash[:12], exc
            )
            raise VerificationFailed("payment could not be read from chain") from exc

        if tx is None or receipt is None:
            return None
        tx_block = receipt.get("blockNumber") or latest
        status = receipt.get("status")
        return ChainObservation(
            success=status in (1, "0x1"),
            value_atoms=int(tx.get("value", 0) or 0),
            pay_to=str(tx.get("to") or ""),
            confirmations=max(0, latest - int(tx_block)),
            settled_tx_hash=proof.tx_hash,
        )
