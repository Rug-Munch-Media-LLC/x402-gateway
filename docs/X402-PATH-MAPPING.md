# x402 endpoint path mapping (documented 2026-10-25)

Three shapes exist today. This page is the single mapping; no service was
refactored (a coordinated deploy of three services is required first).

| Purpose | Spec (x402 v2) | This gateway serves | Coffer / MCP server |
| --- | --- | --- | --- |
| Verify | `POST /verify` | `POST /x402/verify` | `POST /v1/x402/verify` |
| Settle | `POST /settle` | `POST /settle-x402` (`x402_settle_api.py`) | `POST /v1/x402/settle` |
| Supported | `GET /supported` | `GET /x402/supported` | `GET /v1/x402/supported` |

Config today points at `https://x402.org/facilitator/...` — the upstream
facilitator, not a local path. [REVIEW] Consolidating the three served shapes
onto `/verify` + `/settle` is deferred: it needs one coordinated deploy and
client version negotiation. Clients must target the served path above.
