#!/usr/bin/env bash
# Deploy gate: immediately before `fly deploy`, re-prove every live relay
# account is flat (Bitfinex snapshot via check-relay-flat.mjs) AND no live
# intent is in flight (Fly lifecycle: delivered/submitted but not exited).
# Waits (polls) for flat instead of failing; fails only after the budget.
set -uo pipefail
BUDGET_S="${LIVE_FLAT_WAIT_BUDGET_S:-3600}"
INTERVAL_S="${LIVE_FLAT_POLL_S:-30}"
deadline=$(( $(date +%s) + BUDGET_S ))
pending_intents() {
  curl -fsS -m 20 -H "X-Bot-Admin-Token: ${BOT_ADMIN_TOKEN}" -H 'Cache-Control: no-cache' \
    https://doxed-btc-bot.fly.dev/api/monitor/live/lifecycle | python3 -c '
import json, sys, time
d = json.load(sys.stdin)
now = time.time(); n = 0
for ex in (d.get("exchange") or {}).values():
    if ex.get("ok") and ex.get("flat") is False: n += 1
for it in d.get("intents") or []:
    if it.get("expired"): continue
    for a in it.get("accounts") or []:
        dec = ((a.get("decision") or {}).get("decision"))
        if a.get("open"): n += 1
        elif dec == "SUBMITTED" and not a.get("fills") and not a.get("errors") and not a.get("exit"):
            at = (a.get("decision") or {}).get("at_ts") or 0
            if now - at < 900: n += 1
    if it.get("entry_allowed") and not it.get("accounts") and (it.get("age_sec") or 1e9) < 300: n += 1
print(n)'
}
while :; do
  flat_ok=0; node scripts/check-relay-flat.mjs >/tmp/relay-flat.json 2>&1 && flat_ok=1
  pend="$(pending_intents 2>/dev/null || echo ERR)"
  echo "live-flat gate: exchange_flat=${flat_ok} pending_live=${pend}"
  if [ "$flat_ok" = 1 ] && [ "$pend" = 0 ]; then echo "LIVE FLAT: deploy may proceed"; exit 0; fi
  if [ "$(date +%s)" -ge "$deadline" ]; then tail -c 2000 /tmp/relay-flat.json; echo "live account not flat within ${BUDGET_S}s; refusing to deploy"; exit 1; fi
  sleep "$INTERVAL_S"
done
