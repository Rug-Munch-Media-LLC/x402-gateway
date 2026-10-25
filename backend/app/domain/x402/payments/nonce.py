"""Single-use payment claims (replay protection + retry idempotency).

A claim is scoped to the resource it paid for. Same proof for the same scope
is an idempotent retry; same proof for a different scope is a replay.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

logger = logging.getLogger("x402_nonce")


class ClaimOutcome(StrEnum):
    FIRST_USE = "first_use"
    SAME_SCOPE = "same_scope"
    REPLAY = "replay"


class NonceStore(Protocol):
    """Atomic claim store. Implementations must be race-safe."""

    async def claim(self, key: str, scope: str, ttl_seconds: int) -> ClaimOutcome: ...

    async def release(self, key: str) -> None: ...


@dataclass
class MemoryNonceStore:
    """In-memory store for tests and single-process deployments."""

    ttl_seconds: int = 86_400
    _claims: dict[str, tuple[str, float]] = field(default_factory=dict)

    async def claim(self, key: str, scope: str, ttl_seconds: int) -> ClaimOutcome:
        now = time.monotonic()
        entry = self._claims.get(key)
        if entry is not None and entry[1] > now:
            return ClaimOutcome.SAME_SCOPE if entry[0] == scope else ClaimOutcome.REPLAY
        self._claims[key] = (scope, now + ttl_seconds)
        return ClaimOutcome.FIRST_USE

    async def release(self, key: str) -> None:
        self._claims.pop(key, None)


class RedisNonceStore:
    """Redis-backed store: SET NX for the first claim, then compare scope."""

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    @staticmethod
    def _key(key: str) -> str:
        return f"x402:nonce:{key}"

    async def claim(self, key: str, scope: str, ttl_seconds: int) -> ClaimOutcome:
        redis_key = self._key(key)
        if await self._redis.set(redis_key, scope, ex=ttl_seconds, nx=True):
            return ClaimOutcome.FIRST_USE
        stored = await self._redis.get(redis_key)
        if isinstance(stored, bytes):
            stored = stored.decode()
        if stored == scope:
            return ClaimOutcome.SAME_SCOPE
        logger.warning("x402_replay_attempt key=%s", key[:12])
        return ClaimOutcome.REPLAY

    async def release(self, key: str) -> None:
        await self._redis.delete(self._key(key))
