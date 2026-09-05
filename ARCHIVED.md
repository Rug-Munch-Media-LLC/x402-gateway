# 🗄️ x402-gateway — Archived

> # 🗑️ SCHEDULED FOR DELETION
>
> **This repo is a byte-identical clone of [`rmi-mcp-x402`](https://codeberg.org/cryptorugmunch/rmi-mcp-x402).**
> It will be **deleted from all remotes**. History is preserved in `rmi-mcp-x402`.
>
> **Remotes this repo exists on (verified 2026-09-05 via `git ls-remote`):**
> - **gitlab** — `git@gitlab.com:cryptorugmuncher/x402-gateway.git` (exists — main, phase1/archive, v1.0.0)
> - **forgejo** — `https://git.rugmunch.io/RugMunchMedia/x402-gateway.git` (does NOT exist — "Repository not found")
> - **codeberg** — `git@codeberg.org:cryptorugmunch/x402-gateway.git` (does NOT exist — "Cannot find repository")
>
> Deletion applies to the gitlab remote where the repo actually lives. Do not make changes here. All contributions go to `rmi-mcp-x402`.

**Archived:** 2026-07-27  
**Reason:** Duplicate codebase — QA verified 24/27 router files are byte-identical, 3 files differ only by minor enhancements in the canonical repo.  
**Successor:** [`rmi-mcp-x402`](https://codeberg.org/cryptorugmunch/rmi-mcp-x402) — the canonical home for x402 gateway code.

## Duplication Summary (verified 2026-07-27)

| Scope | Status |
|-------|--------|
| `app/routers/` (24 files) | ✅ Byte-identical |
| `app/routers/x402_checkout.py` | ⚠️ rmi-mcp-x402 has on-chain payment verification, idempotency, proper error handling |
| `app/routers/x402_middleware.py` | ⚠️ rmi-mcp-x402 has formatting + API key auth |
| `app/mcp/x402_mcp_server.py` | ⚠️ rmi-mcp-x402 has formatting improvements |
| `app/domain/x402/middleware.py` | ⚠️ rmi-mcp-x402 has extended docstrings |
| `app/api/`, `app/core/`, `app/domain/verified/`, `app/domain/x402/tools/` | 🆕 Only in rmi-mcp-x402 (additional functionality) |

## What rmi-mcp-x402 Has That x402-gateway Doesn't

- On-chain payment verification (Blockscout + RPC fallback)
- Idempotency support (header-based + tx_hash natural keys)
- API key authentication middleware
- Verified identity API (`api/v1/verified/`)
- DuckDB analytics (`core/duckdb_analytics.py`)
- 7 tool modules (`domain/x402/tools/`: label, market, news, report, scanner, token, wallet)
- MCP manifest + server (`mcp/manifest.py`, `mcp/server.py`)

## Fleet-Infra Status

- nginx already routes to `rmi-mcp-x402:8001` (not x402-gateway)
- Docker Compose already references `rmi-mcp-x402` (not x402-gateway)
- BUILD-CHECKLIST.md updated — moved from Private to Archived
- AGENTS.md updated — removed from active repo list
- PRODUCT-GOVERNANCE.md updated — marked as Archived
- X402_ARCHITECTURE.md updated — paths point to rmi-mcp-x402

## What Still Lives in Separate Repos

The TypeScript Cloudflare Worker gateways (`x402-gateway-base`, `x402-gateway-solana`) are separate repos with Cloudflare Worker TS code. These are **not** part of this Python backend repo.

## Final Verdict

**x402-gateway (Python backend) has zero unique code.** All functionality exists in `rmi-mcp-x402` with improvements. Route all contributions to `rmi-mcp-x402`.
