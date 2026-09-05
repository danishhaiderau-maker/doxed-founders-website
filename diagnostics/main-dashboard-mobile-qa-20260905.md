# Main bot mobile and pause-truth audit — 5 September 2026

## Actually exercised

Canonical Fly page `https://doxed-btc-bot.fly.dev/`, rendered revision df45887e1526. Read-only browser navigation at 390x844; viewport override reset afterwards. No settings, tile, wipe, resume, export or relay controls activated.

| Journey | Result |
|---|---|
| Overview navigation | PASS: jumps to market/status area; current pause is readable |
| Decisions navigation | PASS: active navigation styling and tile section shown; cards fit narrow width |
| Data navigation | PASS: jumps to virtual candidates, signals, positions and orders |
| Horizontal page layout | PASS for these samples: clientWidth=scrollWidth=375 (scrollbar excluded) |
| Wide data tables | Contained horizontal scrollers visible; incident 1118px, candidates 1147px, trades 2383px and AI history 1827px inside 351px regions |
| Trading/destructive controls | NOT exercised |
| Complete main-bot mobile content/selector coverage | NOT complete; this is three navigation/layout samples, not all controls |
| Updated local analyzer rendering | NOT tested here; its prior restart rejection remains unresolved |

## Failures found

1. Manual pause coexists with a tile banner saying counterfactual replays are collected. Source audit confirms new scheduled AI/opportunities stop under ADMIN_MANUAL; existing replay buffers may still finalize. The banner overstates new collection.
2. `AI reviewer: ON` is a setting, not evidence of current AI progress. The visible AI explanation says WAITING_FOR_PERIODIC_DIRECTION_SCAN while the active reason is manual pause. Show configuration, scheduler state and last actual call separately.
3. The chase explanation describes 5-minute signal-age windows, but the live allowlist hint still says tick count and virtual wait or cancel. Align it to actual window semantics without changing execution behavior.
4. Empty table messages sit inside horizontally scrolling rows; readable first text is present but the full explanation is off-screen. Put empty-state summaries outside wide tables or allow wrapping.
5. Historical settings tables make each tile very tall; retain their evidence behind disclosures and keep current settings/status prominent.

## Source/runtime verification

Independent read-only audit of dirty bot.py (not committed/deployed by this work): periodic scheduler requires not manually paused; heartbeat/market telemetry and skipped-cycle receipts continue. Existing shadow buffers may finish. `/api/resume` does not require mirror/ACK/analyzer; mirror waiting is an operational sequencing decision, not an execution-gate requirement.

Authenticated status during this audit: git_rev=df45887e1526, force_paper_mode=true, execution_paused=true, execution_reason=ADMIN_MANUAL, bitfinex_live_enabled=false, live_armed=false, system_ready=true. This endpoint's requested scheduled_ai_cycle field was null; no new cycle count was inferred from that response.

No production behavior was changed. These defects remain open for a separately reviewed dashboard patch; preserve the unrelated dirty bot.py changes.
