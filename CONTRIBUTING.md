<!--
SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
-->

# Contributing to x402-gateway

> **NOTE: This repo is orphaned.** See [TRIAGE.md](TRIAGE.md). Code will be merged into `rmi-backend` and this repo archived. Contributions are limited to merge-related fixes and documentation.

## TL;DR

```bash
git checkout main && git pull --rebase origin main
git checkout -b fix/my-merge-related-fix
# …make changes…
git add -A
git commit -m "fix: address merge blocker"
git push origin fix/my-merge-related-fix
```

## Repository Model

Single-tenant branch-PR pattern. `feat/*`, `fix/*`, `chore/*` merged to `main`.

### Branches

| Branch             | Purpose                                          |
|--------------------|--------------------------------------------------|
| `main`             | Frozen pending merge into `rmi-backend`.         |
| `fix/<scope>`      | Bug fix related to merge preparation.            |
| `chore/<scope>`    | Tooling, formatting, non-functional.             |
| `docs/<scope>`     | Documentation only.                              |

### Commit Convention

Conventional Commits. Keep changes minimal and surgical — this repo is on the merge-and-archive track.

## Remotes

| Remote      | URL                                                            | Role                          |
|-------------|----------------------------------------------------------------|-------------------------------|
| `origin`    | `ssh://git@git.rugmunch.io:2222/RugMunchMedia/x402-gateway.git`    | Canonical — Forgejo.          |

## Pre-Push Checklist

```bash
# No automated tests; verify the change is minimal and merge-safe.
git diff main
```

## License

By contributing, you agree your work is licensed under the MIT License.

## Further Reading

- `AGENTS.md` — repo contract, owners, critical rules
- `TRIAGE.md` — disposition decision (MERGE → ARCHIVE)
- `../standards/CONVENTIONS.md` — fleet-wide conventions