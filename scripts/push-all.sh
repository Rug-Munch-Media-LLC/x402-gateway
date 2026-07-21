#!/bin/bash
# Push to all remotes in priority order
# Usage: ./scripts/push-all.sh [branch]
BRANCH="${1:-main}"
set -e
echo "Pushing to forgejo (primary)..."
git push forgejo "$BRANCH" 2>/dev/null || git push origin "$BRANCH"
echo "Pushing to codeberg (mirror)..."
git push codeberg "$BRANCH" 2>/dev/null || echo "  (codeberg unavailable, continuing)"
echo "Pushing to gitlab (mirror)..."
git push gitlab "$BRANCH" 2>/dev/null || echo "  (gitlab unavailable, continuing)"
echo "Pushing to radicle (decentralized)..."
git push radicle "$BRANCH" 2>/dev/null || echo "  (radicle unavailable, continuing)"
echo "✅ All remotes pushed"
