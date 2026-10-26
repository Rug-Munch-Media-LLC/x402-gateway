#!/usr/bin/env bash
# Blocks commits that contain internal infrastructure details or methodology.
#
# ── WHY THE COMMITTED PATTERN LIST IS DELIBERATELY VAGUE ──────────────────────
# This file is installed into PUBLIC repos, so the pattern list is published with
# it. An enumerated list of our hostnames, secrets-manager paths and internal
# directory layout would therefore LEAK exactly what this gate exists to protect.
# That is not hypothetical: until 2026-09-20 the list named our hosts, our gopass
# path scheme and real deploy directories, in 14 repos, and it is in their
# permanent git history.
#
# So the committed set below is SHAPE-BASED ONLY. The specific literals load at
# runtime from an UNCOMMITTED, gitignored file:
#
#     .methodology-patterns        (override path: $METHODOLOGY_PATTERNS)
#
# One pattern per line, plain regex, '#' comments ignored. Absent that file the
# generic shapes still catch the common cases - the gate never fails open and
# never hard-errors on a missing override.
#
# Canonical source: standards/scripts/pre-commit-methodology-check.sh
set -euo pipefail

# Files allowed to contain these patterns: security scanner definitions (the
# detector, not the detected) and canonical infra documentation.
EXEMPT_FILES=(
    # Hook config carries a routine gopass read (signing/secret fetch)
    # as FUNCTIONAL config; edits to it must be able to land.
    "lefthook.yml"
    ".lefthook.yml"
    # The server contract. It DOCUMENTS the infrastructure it governs - real
    # /srv paths, the secrets-manager name, hostnames - rather than leaking it,
    # and this repo is PRIVATE. Same rationale the fleet-infra guard uses for
    # its own contract docs. Without this, ANY edit to AGENTS.md is blocked on
    # its pre-existing content, which is how fixes silently fail to land.
    "AGENTS.md"
    "scripts/pre-commit-methodology-check.sh"
    # The git runbook. It DOCUMENTS the secret-store contract (how to read a
    # value without printing it) exactly as AGENTS.md documents the server.
    # Without this, ANY edit to GIT.md is blocked on its own pre-existing
    # documentation line - the file becomes unmaintainable, and the 2026-10-24
    # protection fix could not record its mechanism.
    "GIT.md"
    "scripts/commit-msg-methodology-check.sh"
    "scripts/dev/pre-commit-methodology-check.sh"
    "fleet/STACK-INTENT.md"
    ".gitleaks.toml"
    "SITE_OWNERSHIP.md"
    "METHODOLOGY-PROTECTION.md"
    ".methodology-patterns"
    "fleet/lead-awareness.md"
    # Fleet memory: the app contract and its standalone client MUST carry
    # the Tailscale endpoint (functional config, private repo).
    "fleet/MEMORY.md"
    "fleet/scripts/sm"
    # Shell/quoting standard + cinnabox parity-sync script: canonical infra
    # documentation/tooling that MUST carry real host aliases + paths.
    "fleet/SHELL-STANDARD.md"
    "fleet/scripts/fleet-sync-cinnabox.sh"
    "scripts/check-antipatterns.py"
    # Deploy tooling. These WRITE the fleet runtime: fleet-deploy.py splices
    # the hermes config at its real path and fleet-config.py renders the live
    # deploy env, so they must carry real host paths as functional config. Same
    # rationale as fleet-sync-cinnabox.sh above. Without this, ANY refactor of
    # these two is blocked on their own pre-existing content.
    "fleet/scripts/fleet-deploy.py"
    "fleet/scripts/fleet-config.py"
    # Structural gates. check-open-core.sh names the adopted manifest and the
    # fleet-infra checkout it validates, and build-agent-registry.py names the
    # agent tree it reads — functional config, not disclosure. Same rationale as
    # fleet-deploy.py and fleet-config.py above: without this, ANY edit to these
    # files is blocked on their own pre-existing content.
    "scripts/check-open-core.sh"
    "fleet/scripts/build-agent-registry.py"
    "scripts/check-structure.py"
    "scripts/check-monkeypatch.py"
    "scripts/ci-gate-selftest.sh"
    # Agent-lane gates. Both DEFAULT to the real lane roots as functional config
    # (LANE_ROOT=/srv/work/retrees, REPOS_ROOT=/srv/work/repos) and resolve
    # $REPOS_ROOT/standards, so they must name those paths to function at all.
    # Same rationale as the structural gates above: without this, ANY edit to
    # these two is blocked on their own pre-existing content.
    "scripts/agent-lane.sh"
    "scripts/check-agent-lanes.sh"
    # Spend/route accounting. These read and write real files under
    # /srv/work/repos/contextcrypto/ (the dispatch log and the routing policy),
    # which is functional config, not documentation — the path IS the interface
    # they operate on, and the repo is PRIVATE. Same rationale as the entries
    # above: without this, ANY edit to these files is blocked on their
    # pre-existing content, which is how fixes silently fail to land.
    # Model-sync reads the live provider cache at /root/.hermes/, which is the
    # file it exists to sync. Functional config, private repo, same rationale
    # as the other entries here.
    "fleet/scripts/fleet-model-sync.py"
    "fleet/scripts/fleet-spend.py"
    "fleet/scripts/route-agent.py"
    "scripts/check-code-structure.py"
    "scripts/check-baseline-freshness.py"
    "scripts/push-mirrors" "scripts/forgejo-branch-protection.sh" "OPEN-CORE-POLICY.json" "scripts/rotate-secrets.sh" "docs/SECRET-ROTATION.md" "docs/research/frontier-ci-gating-2026.md"
    # Hypermodularity gate (2026-10-07): it must name REPO_ROOT=/srv/work/repos
    # and the origin-URL fragments it filters repos by — that IS the gate's
    # function, not a leak. Blocking it means a one-line fix to the gate cannot
    # land: an unset-HOME crash here was refusing EVERY push until it was
    # allowlisted - same rationale as the other functional-config entries above.
    "fleet/gates/hypermodularity-global.sh"
    # Cinnabox command layer (private repo): these MUST carry real /srv paths —
    # the scripts default to real deploy locations on purpose, and they document
    # the same infrastructure AGENTS.md does. Same rationale as AGENTS.md above.
    "fleet/cinnabox/README.md"
    "fleet/cinnabox/ci-gate"
    "fleet/cinnabox/bt"
)

FORBIDDEN_PATTERNS=(
    # CGNAT shared address space (RFC 6598) - a public standard range, not our
    # allocation, so naming it reveals nothing.
    '100\.6[4-9]\.'
    '100\.7[0-9]\.'
    '100\.8[0-9]\.'
    '100\.9[0-9]\.'
    '100\.1[01][0-9]\.'

    # Secrets-manager INVOCATION - names the tool class, never our path scheme.
    # 2026-10-15: require a REAL token start after the verb. A bracketed/angled
    # placeholder (`gopass show [INTERNAL_SECRET_PATH]`) is documentation, not a
    # disclosure, and blocking it in a private repo is what forced a bypass.
    # A real path (`gopass show rmi/...`) still matches.
    '\b(gopass|vault|op|pass)[[:space:]]+(show|insert|get|edit)[[:space:]]+[^[<${:space:]'

    # Internal filesystem SHAPES - the structure, not the real names.
    '/srv/[a-z0-9_-]+/[a-z0-9_-]+'
    '/root/(backend|frontend)\b'
    '/var/www/[a-z0-9_-]+'

    # Bare internal-hostname shapes. Deliberately does NOT list our hosts.
    '[a-z0-9-]+\.(internal|intranet|corp|lan)\b'

    # Methodology keywords (high-signal, generic).
    'proprietary.*algorithm'
    'internal[^.]*methodology'
    'competitor.*analysis.*method'
    'our.*scraping.*target'
    'production.*deploy.*root@'
)

# ALLOWED_PATTERNS — patterns that are explicitly permitted even if they
# resemble forbidden shapes. Each is a plain regex. These are checked BEFORE
# FORBIDDEN_PATTERNS; a match here means the file is clean for that pattern.
# The canonical example: our own git host used as a dependency URL in pyproject.toml,
# requirements.txt, Cargo.toml, go.mod, etc. — this is NOT a methodology leak.
ALLOWED_PATTERNS=(
    # Our Forgejo host used as a git dependency source (pip/poetry/cargo/go).
    # Matches: git+https://git.rugmunch.io/..., git@git.rugmunch.io:..., etc.
    'git\+https://git\.rugmunch\.io/'
    'git@git\.rugmunch\.io:'
    'https://git\.rugmunch\.io/.*\.git'
    # Simulated agent REFUSAL text in the agent-evals harness. It names the
    # phrase only to assert the mock refuses to comply; it discloses nothing.
    # Without this the harness is un-committable.
    'I cannot reveal proprietary scanning algorithms'
)

# ── uncommitted, environment-specific literals ───────────────────────────────
# Our actual hostnames, path scheme and internal names live HERE, never in git.
LOCAL_PATTERNS="${METHODOLOGY_PATTERNS:-.methodology-patterns}"
if [ -f "$LOCAL_PATTERNS" ]; then
    while IFS= read -r raw || [ -n "$raw" ]; do
        line="${raw%%#*}"
        line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
        [ -n "$line" ] && FORBIDDEN_PATTERNS+=("$line")
    done <"$LOCAL_PATTERNS"
fi

is_exempt() {
    local f="$1" exempt
    for exempt in "${EXEMPT_FILES[@]}"; do
        case "$f" in
        "$exempt") return 0 ;;
        esac
    done
    return 1
}

is_allowed() {
    local f="$1" pattern
    [ -f "$f" ] || return 1
    for pattern in "${ALLOWED_PATTERNS[@]}"; do
        if grep -lqE -- "$pattern" "$f" 2>/dev/null; then
            return 0
        fi
    done
    return 1
}

FOUND=0
STAGED_FILES="$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null || true)"

if [ -z "$STAGED_FILES" ]; then
    echo "✅ Methodology check: clean (no staged files)"
    exit 0
fi

# ── PRIVATE-REPO AWARENESS (2026-10-15) ──────────────────────────────────────
# This hook is installed into PUBLIC repos. In a PRIVATE repo (per
# standards/OPEN-CORE-POLICY.json -> private_proprietary) the internal-SHAPE
# patterns describe the repo's OWN infrastructure, which is exactly what a
# private infra repo is for (SUBAGENT-SPEC.md 4.8). Run there, they produced a
# false "methodology leak" on /srv/<path> references and forced a bypass —
# and a gate people bypass is worse than no gate. Secrets-invocation and
# methodology-keyword patterns ALWAYS run, private repo or not.
REPO_ROOT_FOR_POLICY="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
REPO_NAME_FOR_POLICY="$(basename "$REPO_ROOT_FOR_POLICY")"
PRIVATE_REPO=0
for _pol in "${STANDARDS_DIR:-/srv/work/repos/standards}/OPEN-CORE-POLICY.json" \
	"$REPO_ROOT_FOR_POLICY/../standards/OPEN-CORE-POLICY.json" \
	"$REPO_ROOT_FOR_POLICY/OPEN-CORE-POLICY.json"; do
	[ -r "$_pol" ] || continue
	PRIVATE_REPO="$(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
priv = set(d.get("private_proprietary", []))
aliases = d.get("aliases", {}) or {}
name = sys.argv[2]
print(1 if (name in priv or aliases.get(name) in priv) else 0)
' "$_pol" "$REPO_NAME_FOR_POLICY" 2>/dev/null || echo 0)"
	break
done

# Shapes that describe a repo's own internals — legitimate in a private repo.
INTERNAL_SHAPE_PATTERNS=(
	'100\.6[4-9]\.' '100\.7[0-9]\.' '100\.8[0-9]\.' '100\.9[0-9]\.' '100\.1[01][0-9]\.'
	'/srv/[a-z0-9_-]+/[a-z0-9_-]+' '/root/(backend|frontend)\b' '/var/www/[a-z0-9_-]+'
	'[a-z0-9-]+\.(internal|intranet|corp|lan)\b'
)
# The uncommitted override file is ALL internal literals (our hostnames, our
# secret-store paths) — equally legitimate in a private repo.
LOCAL_LITERAL_PATTERNS=()
if [ -f "$LOCAL_PATTERNS" ]; then
	# MUST trim exactly like the FORBIDDEN_PATTERNS loader above (strip the inline
	# comment, then leading/trailing whitespace). The override file is indented, so
	# an untrimmed copy never equalled the trimmed pattern and the private-repo skip
	# silently missed every literal in it — which is why this still blocked after the
	# first fix (2026-10-15).
	while IFS= read -r _raw || [ -n "$_raw" ]; do
		_l="${_raw%%#*}"
		_l="$(printf '%s' "$_l" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
		[ -n "$_l" ] && LOCAL_LITERAL_PATTERNS+=("$_l")
	done <"$LOCAL_PATTERNS"
fi
if [ "$PRIVATE_REPO" = "1" ]; then
	echo "ℹ️  private repo ($REPO_NAME_FOR_POLICY): internal-path/CGNAT shapes allowed here; secrets + methodology shapes still enforced"
fi

for pattern in "${FORBIDDEN_PATTERNS[@]}"; do
	_skip=0
	if [ "$PRIVATE_REPO" = "1" ]; then
		for _ip in "${INTERNAL_SHAPE_PATTERNS[@]}" "${LOCAL_LITERAL_PATTERNS[@]}"; do
			[ "$pattern" = "$_ip" ] && _skip=1 && break
		done
	fi
	[ "$_skip" = "1" ] && continue
    while IFS= read -r file; do
        [ -n "$file" ] || continue
        is_exempt "$file" && continue
        is_allowed "$file" && continue
        [ -f "$file" ] || continue
        if grep -lqE -- "$pattern" "$file" 2>/dev/null; then
            echo "❌ FORBIDDEN PATTERN MATCH in $file"
            echo "   (pattern withheld - see .methodology-patterns; it is an"
            echo "    internal-infrastructure guard, not reproduced here)"
            FOUND=1
        fi
    done <<<"$STAGED_FILES"
done

if [ "$FOUND" -eq 1 ]; then
    echo ""
    echo "🚫 COMMIT BLOCKED: Methodology leak detected."
    echo "   These patterns reveal internal infrastructure or methods."
    echo "   See: METHODOLOGY-PROTECTION.md"
    echo ""
    echo "   Fix the finding. Do not bypass the gate."
    echo "   A false positive is fixed in the PATTERN (allowlist it where it is"
    echo "   defined) — there is no bypass flag in this repo's contract."
    exit 1
fi

echo "✅ Methodology check: clean"
exit 0
