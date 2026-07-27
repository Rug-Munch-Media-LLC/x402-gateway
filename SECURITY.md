[//]: # (SPDX-License-Identifier: MIT)
[//]: # (Copyright (c) 2026 Rug Munch Media LLC)
# SECURITY.md — x402-gateway

> Threat model, secrets, auth, rate limiting, vuln disclosure.

## Threat Model

### Assets
- User wallets (private keys never touch our servers — verified by signature)
- API keys (secrets manager)
- DB credentials (secrets manager)
- Reputation (RMM LLC brand)

### Threats
1. **Secret leak to git** — gitleaks pre-commit hook + history scan on incidents
2. **SQL injection** — SQLAlchemy ORM + parameter binding only
3. **XSS** — React auto-escapes; CSP headers on nginx
4. **CSRF** — SameSite=Strict cookies + Bearer tokens
5. **DDoS** — Cloudflare proxy in front
6. **Supply chain** — renovate keeps deps updated; pip-audit weekly

## Secrets

**Storage**: secrets manager

**Loading**:
- Local dev: `.env` file (gitignored)
- Production: docker `--env-file`, systemd `EnvironmentFile`

**Rotation**:
- API tokens: quarterly
- DB passwords: quarterly
- Signing keys: on personnel change

**Audit**: any secret leak triggers rotation within 1 hour.

## Authentication
- Bearer tokens for API
- OAuth2 (Telegram, etc) for user-facing bots
- x402 micropayments for paid endpoints (signing via Solana/EVM wallet)

## Authorization
- RBAC for admin endpoints
- Rate limiting per IP/token (sliding window)
- Admin endpoints require `cfg.admin_key` check

## Input Validation
- Pydantic models for ALL API inputs (no `dict`/`any` types)
- Length limits, regex validation, enum constraints
- Reject any input containing mainnet addresses/keys (pre-commit hook)

## Rate Limiting
- Configurable per-IP and per-token sliding window
- Returns 429 with `Retry-After` header when exceeded

## Dependencies
- `pip-audit` weekly (CI)
- `safety check` weekly (CI)
- Renovate bot for auto-PR updates

## Vulnerability Disclosure
Email: security@rugmunch.io (PGP key available on request)
Response within 24 hours.
Patch within 7 days for critical, 30 days for medium.

## Audit Log
Every state-changing action logged to:
- `app/state/audit/` (JSONL, append-only)
- ClickHouse (analytics, 90-day retention)
- WORM storage (R2 with object lock, 7-year retention)

## Incident Response
1. Detect (alert from monitoring)
2. Contain (revoke compromised secrets, block IPs)
3. Eradicate (patch vuln, deploy fix)
4. Recover (verify systems clean, restore from backup if needed)
5. Learn (post-mortem, update SECURITY.md)
