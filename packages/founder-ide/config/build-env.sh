#!/usr/bin/env bash
# Founder IDE branding env vars.
#
# Source this before running VSCodium's dev/build.sh. The build.sh script
# reads APP_NAME / BINARY_NAME / ORG_NAME / ASSETS_REPOSITORY / VSCODE_QUALITY
# from the environment (see VSCodium dev/build.sh) and substitutes them into
# product.json, code.iss, binary names, and asset URLs.
#
# See docs/FOUNDER-IDE-FORK-PLAN.md §1.3.
#
# These are overrides on top of VSCodium's defaults:
#   VSCodium defaults ->  APP_NAME="VSCodium"  BINARY_NAME="codium"  ORG_NAME="VSCodium"
#   Founder IDE      ->  APP_NAME="Founder IDE" BINARY_NAME="founder-ide" ORG_NAME="Doxxed Crypto"

set -euo pipefail

export APP_NAME="Founder IDE"
export BINARY_NAME="founder-ide"
export ORG_NAME="Doxxed Crypto"
# GitHub repo where build artifacts (icons, splash, release assets) are hosted.
export ASSETS_REPOSITORY="doxxedcrypto/founder-ide"
# Match VSCodium's stable track (insider is also valid for pre-release builds).
export VSCODE_QUALITY="stable"

# Win32 app-user-model id — used for taskbar grouping, window mutex, and
# the registry value name. Keeping it in sync with BINARY_NAME avoids
# accidentally grouping Founder IDE windows with VS Code/VSCodium.
export WIN32_APP_USER_MODEL_ID="digital.doxxedcrypto.FounderIDE"

# Marketplace = Open VSX (set in product.json template as well, but also
# available as env fallbacks for ad-hoc runs that don't apply the patch).
export VSCODE_GALLERY_SERVICE_URL="https://open-vsx.org/vscode/gallery"
export VSCODE_GALLERY_ITEM_URL="https://open-vsx.org/vscode/item"

echo "[build-env] APP_NAME=${APP_NAME} BINARY_NAME=${BINARY_NAME} ORG_NAME=${ORG_NAME} VSCODE_QUALITY=${VSCODE_QUALITY}"
