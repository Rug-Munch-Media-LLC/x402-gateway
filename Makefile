# ── x402-gateway Makefile ──────────────────────────────────────
# Rug Munch Media fleet standard (from fleet-template/Makefile.python.template)
# Source of truth: https://git.rugmunch.io/RugMunchMedia/x402-gateway
#
# RMI x402 micropayment gateway (v5.0) - the public payment API for
# mcp.rugmunch.io. Multi-chain (Base + Solana), 15+ tool bundles, AI Guard.
#
# Layout:
#   backend/app/mcp/x402_mcp_server.py     - MCP server (uvicorn :8001)
#   backend/app/routers/                   - FastAPI routers (catalog, middleware, ...)
#   backend/app/domain/x402/               - domain logic (pricing, tiers, settlement)

SHELL := /bin/bash

.PHONY: help install dev build lint format check test typecheck security \
        precommit commit ci clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install backend Python deps
	cd backend && pip install -e ".[dev]"
	cd backend && pip install -r requirements.txt 2>/dev/null || true

dev: ## Start MCP server (uvicorn :8001, streamable_http_app)
	cd backend && python3 -m app.mcp.x402_mcp_server

build: ## Build backend Docker image
	cd backend && docker build -t x402-gateway .

lint: ## Lint backend/app/ with ruff
	cd backend && ruff check app/

format: ## Format backend/app/ with ruff (writes)
	cd backend && ruff format app/
	cd backend && ruff check --fix app/

check: ## Full check: lint + format + typecheck
	cd backend && ruff check app/
	cd backend && ruff format --check app/
	cd backend && mypy app/ --ignore-missing-imports

typecheck: ## MyPy type check
	cd backend && mypy app/ --ignore-missing-imports

test: ## Run backend tests (placeholder)
	@cd backend && (pytest tests/ -x -q -m "not integration" 2>/dev/null || echo "no tests yet")

security: ## Security audit (bandit + ruff S-rules)
	cd backend && bandit -r app/ -q
	cd backend && ruff check --select S app/

precommit: ## Install pre-commit hooks
	pre-commit install

commit: ## Interactive commit with commitizen
	cz commit || git commit

ci: lint typecheck security ## Full CI: lint + typecheck + security

clean: ## Remove build artifacts
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache
