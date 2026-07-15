#!/usr/bin/env bash
# prepare-founder-ide.sh
#
# Run AFTER VSCodium's prepare_vscode.sh, BEFORE dev/build.sh.
# Layers Founder IDE branding on top of VSCodium's already-patched VS Code
# tree. Additive only — we do not undo VSCodium's patches, we sit on top.
#
# What this does:
#   1. Patches product.json via jq (brand-product.json.patch):
#        - name/applicationName/dataFolderName -> "Founder IDE" / ".founder-ide"
#        - FRESH win32AppId / win32x64UserAppId GUIDs (side-by-side w/ VS Code)
#        - extensionsGallery -> Open VSX
#        - tunnelApplicationName, documentation/shortcuts/videos/tips/license URLs
#        - builtInExtensions += founder-ide-extension .vsix
#   2. sed build/win32/code.iss: VSCodium -> Founder IDE, publisher, URLs, app ids
#   3. Drops our icon.ico into resources/app/resources/win32/code.ico (if present)
#
# Env:
#   FOUNDER_IDE_EXTENSION_VSIX  - path to founder-ide-extension .vsix (default: ./founder-ide-extension.vsix)
#   FOUNDER_IDE_EXTENSION_VERSION - extension version (default: 0.0.1)
#   VSCODE_DIR                  - the extracted vscode tree (default: ./VSCode) — same as VSCodium's build
#
# Exits non-zero on any failure. Idempotent-ish: re-running re-applies the
# patches (jq rewrites product.json from the current state, sed re-substitutes
# already-substituted text harmlessly).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VSCODE_DIR="${VSCODE_DIR:-./VSCode}"
EXTENSION_VSIX="${FOUNDER_IDE_EXTENSION_VSIX:-${SCRIPT_DIR}/../assets/founder-ide-extension.vsix}"
EXTENSION_VERSION="${FOUNDER_IDE_EXTENSION_VERSION:-0.0.1}"

echo "[prepare] Founder IDE branding — vscode_dir=${VSCODE_DIR} vsix=${EXTENSION_VSIX}"

# --- 0. Sanity ---------------------------------------------------------------
command -v jq  >/dev/null || { echo "[prepare] FATAL: jq not on PATH" >&2; exit 1; }
command -v sed >/dev/null || { echo "[prepare] FATAL: sed not on PATH" >&2; exit 1; }

PRODUCT_JSON="${VSCODE_DIR}/product.json"
CODE_ISS="${VSCODE_DIR}/build/win32/code.iss"

[ -f "${PRODUCT_JSON}" ] || { echo "[prepare] FATAL: ${PRODUCT_JSON} not found — did prepare_vscode.sh run?" >&2; exit 1; }
[ -f "${CODE_ISS}"     ] || { echo "[prepare] FATAL: ${CODE_ISS} not found" >&2; exit 1; }

# --- 1. product.json via jq ---------------------------------------------------
echo "[prepare] patching product.json via jq"
export FOUNDER_IDE_EXTENSION_VSIX
export FOUNDER_IDE_EXTENSION_VERSION
jq --from-file "${SCRIPT_DIR}/brand-product.json.patch" "${PRODUCT_JSON}" > "${PRODUCT_JSON}.founder"
mv "${PRODUCT_JSON}.founder" "${PRODUCT_JSON}"
echo "[prepare] product.json: name=$(jq -r '.nameLong' "${PRODUCT_JSON}") appId=$(jq -r '.win32AppId' "${PRODUCT_JSON}")"

# --- 2. code.iss via sed ------------------------------------------------------
# Layer our strings on top of whatever VSCodium already wrote. We target the
# strings VSCodium sets so this is safe to run after prepare_vscode.sh.
echo "[prepare] patching build/win32/code.iss via sed"

sed -i \
  -e 's/VSCodium/Founder IDE/g' \
  -e 's/codium/founder-ide/g' \
  -e 's|VSCodium/vscodium|doxxedcrypto/founder-ide|g' \
  -e 's|vscodium\.com|doxxedcrypto.digital|g' \
  -e 's|VSCodium\.io|doxxedcrypto.digital|g' \
  -e 's|The VSCodium authors|Doxxed Crypto|g' \
  -e 's|VSCodium Team|Doxxed Crypto|g' \
  -e 's|publisher=VSCodium|publisher=Doxxed Crypto|g' \
  -e 's|Publisher: VSCodium|Publisher: Doxxed Crypto|g' \
  "${CODE_ISS}"

# Bump the Inno Setup AppId inside code.iss to our fresh GUID so Founder IDE
# and VSCodium get separate Add/Remove Programs entries. VSCodium sets
# `AppId={{...}}` — we rewrite that line wholesale.
if grep -q '^AppId=' "${CODE_ISS}"; then
  sed -i 's|^AppId=.*|AppId={{2557F919-8736-40CC-A9A6-D9AC45C21CBF}|' "${CODE_ISS}"
else
  echo "[prepare] WARN: no AppId= line in code.iss — Inno Setup AppId left as VSCodium's. Check manually." >&2
fi

echo "[prepare] code.iss patched"

# --- 3. App icon --------------------------------------------------------------
ICON_SRC="${SCRIPT_DIR}/../assets/icon.ico"
ICON_DST="${VSCODE_DIR}/resources/app/resources/win32/code.ico"
if [ -f "${ICON_SRC}" ]; then
  echo "[prepare] dropping app icon -> ${ICON_DST}"
  cp "${ICON_SRC}" "${ICON_DST}"
else
  echo "[prepare] WARN: ${ICON_SRC} not found — app icon left as VSCodium's. Add assets/icon.ico before release." >&2
fi

# --- 4. Built-in extension .vsix must be resolvable ---------------------------
# product.json now references FOUNDER_IDE_EXTENSION_VSIX. VS Code's build
# resolves builtInExtensions relative to the build root. Copy it next to the
# vscode tree if it isn't already there.
if [ -f "${EXTENSION_VSIX}" ]; then
  cp "${EXTENSION_VSIX}" "${VSCODE_DIR}/founder-ide-extension.vsix"
  echo "[prepare] copied ${EXTENSION_VSIX} -> ${VSCODE_DIR}/founder-ide-extension.vsix"
else
  echo "[prepare] WARN: ${EXTENSION_VSIX} not found — build will fail to inline the chat extension. Run npm run package in packages/founder-ide-extension/ first." >&2
fi

echo "[prepare] done"
