"""Narrow failure sets for the best-effort x402 enrichment boundaries.

Each set lists the concrete exception types a block can actually raise, so the
enrichment paths never need a blind ``except Exception`` (ruff BLE001/S110).
Redis failures come from ``x402_redis_errors`` so both modules agree.
"""

from __future__ import annotations

from app.routers.x402_redis_errors import REDIS_ERRORS

try:  # runtime dependency; fallback keeps imports safe when the driver is absent
    from clickhouse_driver.errors import Error as ClickHouseError
except ImportError:  # pragma: no cover - only when clickhouse-driver is missing
    ClickHouseError = OSError

# ClickHouse query plus row-shape handling. AttributeError is real: _ch() yields
# None when ClickHouse is unavailable, so the client call fails before any query.
CH_FAILURES: tuple[type[BaseException], ...] = (
    ClickHouseError,
    OSError,
    ImportError,
    AttributeError,
    IndexError,
    KeyError,
    TypeError,
    ValueError,
)

# Redis commands plus JSON decoding and dict hygiene around cached payloads.
CACHE_FAILURES: tuple[type[BaseException], ...] = (
    *REDIS_ERRORS,
    ImportError,
    AttributeError,
    IndexError,
    KeyError,
    TypeError,
    ValueError,
)

# RAG embed/search: cache failures plus a service that is missing or unconfigured.
RAG_FAILURES: tuple[type[BaseException], ...] = (*CACHE_FAILURES, RuntimeError)

# Scam-pattern detection runs its own event loop; RuntimeError covers a busy loop.
SCAM_FAILURES: tuple[type[BaseException], ...] = (*RAG_FAILURES, OSError)

# urllib plus JSON shape for live Etherscan and LLM HTTP lookups.
HTTP_FAILURES: tuple[type[BaseException], ...] = (
    OSError,
    KeyError,
    IndexError,
    TypeError,
    ValueError,
)
