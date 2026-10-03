# Bitfinex live readiness — checklist only

**Status: NOT ARMED / NOT YET.** This document does not authorize Bitfinex arming.

Canonical: `C:\DoxxedCrypto\btc-v31-current` · Fly: https://doxed-btc-bot.fly.dev  
Deployed (2026-09-10): `0430145` / `v31-five-family-score-led-paper-v1` · `FORCE_PAPER=1` · `live_armed=false`

## Live snapshot (2026-09-10 ~01:05 AEST)

| Gate | State |
|------|--------|
| Paper fence | **HELD** (`force_paper_mode=true`, `live_armed=false`, `bitfinex_live_enabled=false`) |
| Armable | **false** — `live_entry_arm_block_reason=FORCE_PAPER_MODE` |
| Relay configured | **true** (disarmed) |
| Score-led paper | **ON** (`SCORE_LED_PAPER_V1`; pending≈14, open=0) |
| Fresh epoch wipe | **NOT READY** — execution unpaused + pending orders |
| Mirror ≡ Fly | **INCOMPLETE** — local ~0.43GiB vs Fly `/app/data` 3.1G |
| Analyzer `:9001` | **DOWN** — do not start until verified ACK mirror |
| Bitfinex arm | **NOT YET** — requires Danish explicit “arm” |

Receipt: `diagnostics/bitfinex-disarmed-snapshot-20260910.json`

## Prior snapshot (2026-09-04 ~20:57 AEST) — historical

| Gate | State |
|------|--------|
| Paper fence | **HELD** (`force_paper=true`, `live_armed=false`, `bitfinex_live=false`) |
| Receipt bootstrap | **COMPLETE** (lifecycle started after full 40-char `SOURCE_GIT_REV` deploy) |
| Inventory | **CURRENT** (`ack_eligible=true`, **32754** files) |
| Mirror ≡ Fly HEAD | **IN PROGRESS** — oneshot PID **22936** paging manifest (prior run reached 113/31077 incl. 403MB `research_events_v22.jsonl`) |
| Analyzer `:9001` | **STALE** until mirror MATCH + regen |
| Paper dashboard | Up; Continuous Paper ON; **5× PnL CONTAMINATED** (slim nested-schema deployed — banners clear on new closes / schema-stamped rows) |
| Strategy STATIC/DYNAMIC | **UNKNOWN** (blocked on fresh analyzer + mature QUALIFICATION_READY) |
| Bitfinex arm | **NOT YET** |

## Cleanup done (AI-speed)

- Freed **~56.2 MB** (127 paths). Kept quarantine (~575 MB) until regen OK. Kept live mirror.
- **NEED FROM USER:** quit Founder IDE Next QA to delete locked `Final-Bot\qa-installed-v1.0.45` (~43.9 MB).
- Scheduled task `DoxxedResearchStabilitySupervisor` **Disabled** during oneshot (was respawning competing sync loop). Re-enable after MATCH + analyzer regen.

## Deploy note (ops)

Lifecycle pipeline requires **exact 40-char** `SOURCE_GIT_REV`. Short `df45887` build-arg prevented pipeline start → inventory starved on bootstrap. Fixed in latest deploy.
