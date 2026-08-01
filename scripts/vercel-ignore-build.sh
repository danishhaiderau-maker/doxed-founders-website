#!/bin/sh
# Vercel Ignored Build Step: exit 0 skips an unchanged web build; exit 1
# builds. This is deliberately fail-open: missing git context, an unknown
# diff, or any web/workspace change always builds.
set -eu

if [ -z "${VERCEL_GIT_COMMIT_SHA:-}" ]; then
  echo "[vercel-ignore] No git SHA; building safely."
  exit 1
fi

sha="$VERCEL_GIT_COMMIT_SHA"
if ! git cat-file -e "$sha^{commit}" 2>/dev/null; then
  echo "[vercel-ignore] Commit is unavailable locally; building safely."
  exit 1
fi

if ! parent="$(git rev-parse "$sha^" 2>/dev/null)"; then
  echo "[vercel-ignore] Root or shallow commit; building safely."
  exit 1
fi

if ! changed="$(git diff --name-only "$parent" "$sha" 2>/dev/null)"; then
  echo "[vercel-ignore] Unable to resolve changed files; building safely."
  exit 1
fi

if [ -z "$changed" ]; then
  echo "[vercel-ignore] Empty diff; skipping duplicate build."
  exit 0
fi

# Vercel installs the root workspace, builds @dcf/utils, and then @dcf/web.
# Changes outside these inputs cannot affect the deployed web artifact. Keep
# this POSIX-shell-only so a missing optional utility can never cause a skip.
build_required=0
while IFS= read -r file; do
  case "$file" in
    apps/web/*|packages/*|package.json|package-lock.json|npm-shrinkwrap.json|tsconfig*.json|vercel.json|.vercelignore|scripts/vercel-ignore-build.sh)
      build_required=1
      ;;
  esac
done <<EOF
$changed
EOF

if [ "$build_required" -eq 1 ]; then
  echo "[vercel-ignore] Web/workspace input changed; building."
  exit 1
fi

echo "[vercel-ignore] No web/workspace input changed; skipping build."
printf '%s\n' "$changed"
exit 0
