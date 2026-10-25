#!/usr/bin/env bash
# push-all.sh [branch] — delegate to the ONE push seam. Do not add push logic.
#
# 2026-10-24: this file used to push forgejo/codeberg/gitlab/radicle itself and
# continued after every failure ("codeberg unavailable, continuing"), so a
# mirror could advance while Forgejo was behind and a rejection was invisible.
# It is a delegator now: Forgejo first, abort on failure, classified mirrors,
# Radicle/Nostr publication only when the published tip moved, named exit codes.
set -uo pipefail
SEAM=/srv/work/repos/fleet-infra/scripts/git/mirror-sync.sh
[ -x "$SEAM" ] || { echo "FATAL: canonical seam missing: $SEAM" >&2; exit 9; }
root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "not in a git repo" >&2; exit 2; }
exec "$SEAM" "$root" "${1:-}"
