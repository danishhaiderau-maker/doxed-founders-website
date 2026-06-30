#!/usr/bin/env bash
# fly-setup-volume.sh — POSIX equivalent of the .ps1 helper (for macOS/Linux).
# Creates the bot_data volume in syd and prints machine IDs.
set -euo pipefail

APP_NAME="${FLY_APP_NAME:-doxed-btc-bot}"
REGION="${FLY_REGION:-syd}"
SIZE_GB="${FLY_VOLUME_GB:-1}"

echo "==> Ensuring flyctl is authenticated..."
flyctl auth whoami

echo "==> Creating volume bot_data (${SIZE_GB}GB, ${REGION}) for ${APP_NAME} (idempotent)..."
if flyctl volumes list --app "$APP_NAME" 2>/dev/null | grep -q "bot_data"; then
  echo "Volume bot_data already exists — skipping create."
else
  flyctl volumes create bot_data --app "$APP_NAME" --region "$REGION" --size "$SIZE_GB"
fi

echo ""
echo "==> Machines for ${APP_NAME} (copy one machine ID into FLY_MACHINE_ID on Railway):"
flyctl machines list --app "$APP_NAME"

echo ""
echo "Done. Next: set secrets (flyctl secrets set ...), then 'fly deploy'."
