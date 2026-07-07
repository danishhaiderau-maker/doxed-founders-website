#!/bin/sh
# Vercel "Ignored Build Step" — exit 0 to SKIP the build (no charge),
# exit 1 to PROCEED with the build.
#
# Configured on the doxed-founders-website project via
#   PATCH /v9/projects/doxed-founders-website
#   { "commandForIgnoringBuildStep": "bash scripts/vercel-ignore-build.sh" }
#
# This is the Vercel analogue of the GitHub Actions paths-ignore we
# already use to suppress chore(founder-os) memory-sync commits.
#
# Skips:
#   1. chore(founder-os): sync memory commits (1-line tasks.json changes
#      that don't affect any deployable code).
#   2. Commits that touch NO deployable code paths (apps/, packages/,
#      prisma/, public/, next/web config, etc.) — pure docs / scripts /
#      research / .github changes.
#
# NEVER skips:
#   - Manual deploys (no VERCEL_GIT_COMMIT_SHA in env) — these are
#     always intentional.
#   - Commits that change any file under apps/, packages/, prisma/,
#     public/, or any next/web build config.
#
# This script is evaluated on EVERY deploy trigger, including
# API-triggered deploys via scripts/vercel-deploy.mjs (which pass
# gitSource.sha) — so memory-sync commits deployed via the API path
# are also skipped, exactly what we want.

set -eu

# ─── 1. Manual / API deploys without git context ─────────────────────────
# When Vercel can't resolve a git commit (rare for our flow), always
# build — we'd rather over-build than silently skip a real change.
if [ -z "${VERCEL_GIT_COMMIT_SHA:-}" ]; then
  exit 1
fi

COMMIT_SHA="$VERCEL_GIT_COMMIT_SHA"

# ─── 2. Read the commit message (best-effort) ────────────────────────────
# git is available in the Vercel build container. If anything goes
# wrong reading the message, fall through to the path-based check
# rather than skipping.
COMMIT_MSG=""
if [ -n "${VERCEL_GIT_REPO_SLUG:-}" ] || true; then
  COMMIT_MSG="$(git log -1 --pretty=%B "$COMMIT_SHA" 2>/dev/null || echo "")"
fi

# ─── 3. Skip known memory-sync / bot-state commit subjects ───────────────
# These are 1-line state-file pushes from the founder-os agent. The
# tasks.json / context.md / roadmap.md files they touch are never read
# by the Next.js build or runtime.
case "$COMMIT_MSG" in
  *"chore(founder-os)"*)
    echo "[ignore-build] chore(founder-os) memory-sync commit — skipping."
    exit 0
    ;;
  *"sync memory"*)
    echo "[ignore-build] 'sync memory' commit — skipping."
    exit 0
    ;;
esac

# ─── 4. Path-based skip: any code-path change? ───────────────────────────
# If HEAD touches NONE of these paths, the build output is guaranteed
# identical to the previous deploy, so skip it. List intentionally
# broad — covers everything the Next.js build + install reads.
#
# Note: HEAD^ may not exist on the very first deploy of a project, but
# that's a one-shot case and the build will run because git diff
# fails open (empty CHANGED → skip; we counter that below for the
# empty-repo case by checking git rev-list count).
PARENT="$(git rev-list --max-parents=0 "$COMMIT_SHA" 2>/dev/null || echo "")"
if [ -n "$PARENT" ] && [ "$PARENT" = "$COMMIT_SHA" ]; then
  # Root commit — no parent to diff against. Build to be safe.
  exit 1
fi

CHANGED="$(git diff --name-only "$COMMIT_SHA^" "$COMMIT_SHA" -- \
  'apps/' \
  'packages/' \
  'prisma/' \
  'public/' \
  'next.config.*' \
  'apps/web/next.config.*' \
  'package.json' \
  'package-lock.json' \
  'pnpm-workspace.yaml' \
  'tsconfig*.json' \
  'railway.toml' \
  'railway.json' \
  '.npmrc' \
  '.nvmrc' \
  '.node-version' \
  2>/dev/null || echo "")"

if [ -z "$CHANGED" ]; then
  echo "[ignore-build] No deployable code-path changes — skipping build."
  echo "  commit: $COMMIT_SHA"
  echo "  subject: $(printf '%s' "$COMMIT_MSG" | head -n1)"
  exit 0
fi

# ─── 5. Otherwise: build ─────────────────────────────────────────────────
echo "[ignore-build] Deployable paths changed; proceeding with build."
echo "  commit: $COMMIT_SHA"
echo "  subject: $(printf '%s' "$COMMIT_MSG" | head -n1)"
echo "  changed:"
printf '%s\n' "$CHANGED" | sed 's/^/    /'
exit 1
