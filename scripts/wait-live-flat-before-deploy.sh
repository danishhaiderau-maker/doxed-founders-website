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
  live="$(api "$B/api/monitor/live/lifecycle" | python3 -c '
import json,sys; d=json.load(sys.stdin); bad=0
for ex in (d.get("exchange") or {}).values():
    if not ex.get("ok") or ex.get("flat") is not True or (ex.get("read_age_s") or 999) > 90 or ex.get("open_orders"): bad+=1
print(bad)' 2>/dev/null || echo ERR)"
  flat_ok=0; node scripts/check-relay-flat.mjs >/tmp/relay-flat.json 2>&1 && flat_ok=1
  echo "verify: exchange_mirror_not_flat=${live} relay_flat_check=${flat_ok}"
  if [ "$live" = 0 ] && [ "$flat_ok" = 1 ]; then echo "FLAT (paper + live): deploying now"; exit 0; fi
  if [ "$(date +%s)" -ge "$deadline" ]; then tail -c 2000 /tmp/relay-flat.json; echo "exchange not flat ${VERIFY_S}s after force-flat; refusing to deploy"; exit 1; fi
  sleep 10
done
