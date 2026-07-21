# REVIEW-GATE.md — Enterprise Quality Gate
# Source: /srv/work/repos/standards/REVIEW-GATE.md

## Before Every Commit

1. **Lint**: ruff (Python) / biome (TypeScript)
2. **TypeCheck**: mypy --strict (Python) / tsc --noEmit (TypeScript)
3. **Tests**: pytest -x (Python) / vitest run (TypeScript)
4. **Security**: gitleaks detect --verbose
5. **Secrets**: verify no API keys or .env in diff
6. **Design Gate**: no `Any` in TS sigs, no bare `except:` in Python

## Review Checklist

- [ ] No hallucinated imports or APIs
- [ ] No `print()` / `console.log()` in prod code
- [ ] No `Any` type (TypeScript) / untyped def (Python) in public API
- [ ] No eval(), no Function()
- [ ] No hardcoded RPC URLs, API keys, or secrets
- [ ] Error handling present (no silent failures)
- [ ] Input validation present
- [ ] Build succeeds
- [ ] Tests pass (or test plan documented)
- [ ] Conventional commit format

## GVU Triad (Generator ≠ Verifier)

| Role | Who | Constraint |
|------|-----|-----------|
| **G**enerator | Developer / AI | Writes code, does NOT self-review |
| **V**erifier | Secondary agent | Author of reviews, NOT same as G |
| **U**ser | Human operator | Final approval, NOT same as V |

## Auto-Gates

| Gate | Where | Tool |
|------|-------|------|
| Pre-commit | Local | .pre-commit-config.yaml |
| CI | Forgejo/GitHub | ci-quality-gate.yml |
| Review | Agent | ai-agent-quality-gate skill |
