# Bitfinex live readiness — checklist only

**Status: NOT ARMED.** This document does not authorize Bitfinex arming.

Canonical bot: `C:\DoxxedCrypto\btc-v31-current`  
Fly: https://doxed-btc-bot.fly.dev  
Code helper: `services/btc-conservative-agent/bitfinex_live_checklist.py`

## Required before any future arm request

1. `force_paper_mode=true` and `live_armed=false` on the exact deployed revision
2. Relay OFF (or empty allowlist); never copy historical / already-open paper
3. Size: `$0.20–$0.25` margin at `100x` only — fail closed on upward rounding
4. Exchange accepts qty; stops / reduce-only proven; partial reduction proven
5. Reconciliation green; analyzer parity on current epoch
6. Conservative OOS qualification pass (not ideal-touch labels)
7. **Explicit user arm authorization** after the above are current

## Out of scope for Phase 0–4

- Clearing `FORCE_PAPER`
- Setting `live_armed=true`
- Enabling Bitfinex copy of paper intents

## Guardian REDs that need an operator ack (PR #876, HM R4)

The guardian latches these REDs **ack-only**. Going FLAT does not clear them. Each one needs
`POST /api/live-copy/guardian/red-ack`, with strict admin auth, a named actor, a reason, the exact current code and
FLAT evidence newer than the RED:

- **R20 daily-cap REDs** (`DAILY_CAP_DISARM_CODES`). Every daily-cap hit needs a fresh ack. This is stricter than the old
  self-clear-on-flat behaviour, and that is intended.
- **R20 all-time total-loss REDs** (`LIVE_TOTAL_LOSS_RED_CODES`). Even after an ack, arming and `admit_entry` re-compute the
  all-time cap and stay refused while it is breached or unreadable.
- **`LIQ_CLAMP_FLATTEN_UNCONFIRMED`**.
- **`GUARDIAN_STATE_UNREADABLE`**. An unreadable or missing state file may have held a cap latch, so this RED fails closed.
