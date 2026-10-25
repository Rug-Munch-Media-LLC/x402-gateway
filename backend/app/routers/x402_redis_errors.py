"""Narrow Redis failure types so x402 handlers never need a blind except."""

from __future__ import annotations

try:  # redis is a runtime dependency; fallback keeps imports safe if absent
    from redis.exceptions import RedisError as _RedisError
except ImportError:  # pragma: no cover - only when redis is not installed
    _RedisError = OSError

# Connection, timeout and protocol failures derive from RedisError; OSError
# covers socket-level failures raised while building the client.
REDIS_ERRORS: tuple[type[BaseException], ...] = (_RedisError, OSError)
