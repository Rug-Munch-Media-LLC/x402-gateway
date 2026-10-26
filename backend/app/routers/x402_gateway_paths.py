"""Gateway-config discovery paths (deployment seam).

Chain configs live in an ``x402-gateway`` checkout that is mounted or placed
differently per host. Those absolute roots are DEPLOYMENT data, so they are
resolved here (env override first, then derived sibling paths, then the
container/conventional defaults) instead of being hardcoded in the routers.
"""

from __future__ import annotations

import os

# Container mount + conventional companion checkout. Host-specific roots belong
# in X402_GATEWAY_BASE, never in source: this repository is public.
_DEFAULT_BASES = ("/app/x402-gateway", "/opt/x402-gateway")


def gateway_base_candidates(base_dir: str | None = None) -> list[str]:
    """Ordered directories that may hold gateway chain configs (dedup'd)."""
    override = os.environ.get("X402_GATEWAY_BASE", "").strip()
    candidates = [override] if override else []
    if base_dir:
        candidates.append(os.path.join(base_dir, "x402-gateway"))
        candidates.append(os.path.join(os.path.dirname(base_dir), "x402-gateway"))
    candidates.extend(_DEFAULT_BASES)
    ordered: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    return ordered
