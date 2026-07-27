# Open-Core Model

RugMunch operates an industry-standard open-core business model:

- **PUBLIC (Open Source):** Core product code, scanner pipelines, frontend components, RAG engine, scraping framework. MIT licensed. Self-hostable. Fully functional.
- **PRIVATE (Proprietary):** Admin backend (rugmunch.io), production infrastructure, security stack, operational methods, competitive intelligence, deployment automation.

## Why This Model

Following industry leaders (GitLab CE/EE, Supabase OSS/Platform, Sentry OSS/Hosted):

- Code transparency builds trust
- Community can audit, contribute, and self-host
- The hard work of integration + hosting + support is our product
- The admin panel and operational tooling are our competitive edge

## What Belongs Where

| Type | Visibility | Examples |
|------|-----------|---------|
| Public repos | Open Source (MIT) | rmi-frontend, rmi-backend, pryscraper, walletpress, rmi-rag, chain-indexer, rugcharts, rugmaps |
| Private repos | Proprietary | fleet-infra (infra configs), rmi-core (admin backend) |

## Protections

- gitleaks pre-commit on ALL repos
- No internal hostnames, IPs, or secrets in public code
- No proprietary scoring or risk algorithms in OSS
- METHODOLOGY-PROTECTION.md enforced across all repos

For the full policy, see [standards/ENTERPRISE-README.md](https://git.rugmunch.io/RugMunchMedia/standards).
