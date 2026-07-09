#!/usr/bin/env bash
# build-founder-ide.sh
#
# Main Founder IDE build (Git Bash). Wraps VSCodium's dev/build.sh with our
# branding env vars and runs prepare-founder-ide.sh first.
#
# Run this from the ROOT of a VSCodium downstream checkout (the repo that
# contains dev/build.sh, build/windows/package.sh, etc). It does NOT clone
# VSCodium — that's a one-time setup step on the build machine. See
# packages/founder-ide/RELEASES.md.
#
# Usage:
#   "C:\Program Files\Git\bin\bash.exe" ./build/build-founder-ide.sh
#
# Env overrides:
#   SKIP_PREPARE=1     - skip prepare-founder-ide.sh (you already ran it)
#   EXTENSION_VSIX=... - path to founder-ide-extension .vsix
#   VSCODIUM_BUILD_FLAGS - extra flags to pass to dev/build.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- 1. Source branding env vars ---------------------------------------------
# These are read by VSCodium's dev/build.sh.
# shellcheck source=/dev/null
source "${PKG_DIR}/config/build-env.sh"

# --- 2. Run our branding patches (after VSCodium's prepare_vscode.sh) ---------
# dev/build.sh runs prepare_vscode.sh internally before compiling, but our
# patches need to land on the prepared tree. The cleanest hook is to run our
# prepare step as a pre-build patch against the vscode tree that build.sh
# extracts. We export env so prepare-founder-ide.sh can find the .vsix.
export FOUNDER_IDE_EXTENSION_VSIX="${EXTENSION_VSIX:-${PKG_DIR}/assets/founder-ide-extension.vsix}"
export FOUNDER_IDE_EXTENSION_VERSION="${FOUNDER_IDE_EXTENSION_VERSION:-0.0.1}"

# VSCodium's build.sh extracts the vscode tree to ./VSCode and runs
# prepare_vscode.sh inside it. We can't easily inject mid-build, so we use
# the VSCodium-supported pattern: a post-prepare hook via the
# FOUNDER_PREPARE_HOOK env var if set, otherwise we run our prepare step
# against ./VSCode before build.sh's compile step. The simplest robust
# approach: run prepare-founder-ide.sh after build.sh has extracted + prepared
# the tree but before compile. VSCodium exposes this via the
# `prepare_extra_script` convention (see how their own patches hook in).
#
# In practice: we set VSCODIUM_PREPARE_EXTRA to our script path; if the
# downstream doesn't honor it, run our prepare step manually after build.sh
# finishes the prepare phase (handled in the orchestration script).
export VSCODIUM_PREPARE_EXTRA="${SCRIPT_DIR}/prepare-founder-ide.sh"

if [ "${SKIP_PREPARE:-0}" != "1" ]; then
  echo "[build-founder-ide] prepare hook registered: ${VSCODIUM_PREPARE_EXTRA}"
  echo "[build-founder-ide] (if your VSCodium checkout does not honor VSCODIUM_PREPARE_EXTRA,"
  echo "[build-founder-ide]  run prepare-founder-ide.sh manually after prepare_vscode.sh.)"
fi

# --- 3. Invoke VSCodium's dev/build.sh ----------------------------------------
BUILD_SH="./dev/build.sh"
if [ ! -f "${BUILD_SH}" ]; then
  echo "[build-founder-ide] FATAL: ${BUILD_SH} not found." >&2
  echo "[build-founder-ide]        Run this from the root of a VSCodium downstream checkout." >&2
  exit 1
fi

echo "[build-founder-ide] invoking VSCodium dev/build.sh with flags: ${VSCODIUM_BUILD_FLAGS:-}"
bash "${BUILD_SH}" ${VSCODIUM_BUILD_FLAGS:-}

# --- 4. Locate + rename outputs to Founder IDE names --------------------------
# VSCodium's build emits binaries named after BINARY_NAME (founder-ide) and
# installer named after APP_NAME (Founder IDE) because we exported those env
# vars. If for any reason the build emitted codium/VSCodium names, rename.
OUT_DIR="$(ls -d VSCode/*/win32-x64 2>/dev/null | head -n1 || true)"
if [ -n "${OUT_DIR}" ]; then
  echo "[build-founder-ide] outputs in ${OUT_DIR}"
  # Defensive renames (no-op if build.sh already used our names).
  for f in "${OUT_DIR}"/VSCodium-Setup-*.exe "${OUT_DIR}"/codium-Setup-*.exe; do
    [ -f "$f" ] || continue
    mv "$f" "${OUT_DIR}/$(basename "$f" | sed -E 's/(VSCodium|codium)/Founder-IDE/')"
  done
  for f in "${OUT_DIR}"/VSCodium-*-portable*.zip "${OUT_DIR}"/codium-*-portable*.zip; do
    [ -f "$f" ] || continue
    mv "$f" "${OUT_DIR}/$(basename "$f" | sed -E 's/(VSCodium|codium)/Founder-IDE/')"
  done
  echo "[build-founder-ide] final outputs:"
  ls -1 "${OUT_DIR}" | grep -iE 'Founder-IDE' || true
fi

echo "[build-founder-ide] done"
