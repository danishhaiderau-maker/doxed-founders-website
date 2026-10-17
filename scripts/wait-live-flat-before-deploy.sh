#!/usr/bin/env bash
# Deploy gate (owner 11 Oct 21:53): deploys never wait for trades to finish.
# Immediately before `fly deploy`: force-flatten every Fly paper position and
# pending order with exit reason DEPLOY_FORCE_FLAT (stats-excluded). Mirrored
# live Bitfinex copies close with their paper parents (SHOWCASE_MIRROR path).
# Then prove flat on the exchange (website exchange mirror via Fly lifecycle,
# plus check-relay-flat.mjs) before deploying. Fails closed (no deploy) if the
# exchange is still not flat after LIVE_FLAT_VERIFY_S.
set -uo pipefail
VERIFY_S="${LIVE_FLAT_VERIFY_S:-180}"
B=https://doxed-btc-bot.fly.dev
api() { curl -fsS -m 20 -H "X-Bot-Admin-Token: ${BOT_ADMIN_TOKEN}" -H 'Cache-Control: no-cache' -H 'Content-Type: application/json' "$@"; }
for round in 1 2 3; do
  st="$(api "$B/api/relay-execution-state?fresh=1")" || { sleep 3; continue; }
  read -r POS ORD <<<"$(printf '%s' "$st" | python3 -c '
import json,sys; d=json.load(sys.stdin)
p=sorted({str(r.get("trade_id") or "") for r in d.get("positions") or []}-{""})
o=sorted({str(r.get("trade_id") or "") for r in d.get("orders") or []}-{""})
print(",".join(p) or "-", ",".join(o) or "-")')"
  echo "force-flat round ${round}: positions=${POS} orders=${ORD}"
  [ "$POS" = "-" ] && [ "$ORD" = "-" ] && break
  for t in ${POS//,/ }; do [ "$t" = "-" ] || api -X POST "$B/api/positions/close" -d "{\"trade_id\":\"$t\",\"reason\":\"DEPLOY_FORCE_FLAT\"}" | head -c 200; echo; done
  for t in ${ORD//,/ }; do [ "$t" = "-" ] || api -X POST "$B/api/orders/cancel" -d "{\"trade_id\":\"$t\",\"reason\":\"DEPLOY_FORCE_FLAT\"}" | head -c 200; echo; done
  sleep 3
done
deadline=$(( $(date +%s) + VERIFY_S ))
while :; do
  api "$B/api/monitor/live/lifecycle" >/tmp/live-lifecycle.json 2>/dev/null || echo '{}' >/tmp/live-lifecycle.json
  api "$B/api/monitor/live/exchange-health" >/tmp/live-exchange-health.json 2>/dev/null || echo '{}' >/tmp/live-exchange-health.json
  # A failed/unreadable fetch (no exchange map) prints ERR ERR, which never equals 0: not flat.
  live="$(python3 "$(dirname "$0")/live_flat_verify.py" /tmp/live-lifecycle.json /tmp/live-exchange-health.json)"
  read -r live stale <<<"$live"
  flat_ok=0; node scripts/check-relay-flat.mjs >/tmp/relay-flat.json 2>&1 && flat_ok=1
  echo "verify: exchange_mirror_not_flat=${live} mirror_stale_but_flat=${stale} relay_flat_check=${flat_ok}"
  # A stale-but-flat mirror (idle account: mirror only refreshes on activity) is
  # accepted only because check-relay-flat.mjs just did a fresh executor read of
  # Bitfinex. Any position / open order / orphan in the mirror still blocks.
  if [ "$live" = 0 ] && [ "$flat_ok" = 1 ]; then echo "FLAT (paper + live): deploying now"; exit 0; fi
  if [ "$(date +%s)" -ge "$deadline" ]; then tail -c 2000 /tmp/relay-flat.json; echo "exchange not flat ${VERIFY_S}s after force-flat; refusing to deploy"; exit 1; fi
  sleep 10
done
