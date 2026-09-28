# Visual QA Receipt — 2026-08-28 — final revision 59d24e71126e

Status: **PRODUCTION PASS; ANALYZER QA PENDING EXACT-MIRROR GENERATION**

This receipt deliberately does not claim whole-platform readiness. Production was inspected first-hand after deployment. Analyzer inspection remains pending until the sole atomic Fly mirror publishes revision `59d24e71126e` and a new analyzer generation publishes that same revision and clean epoch.

## Identity

- QA timestamp: 2026-08-28 04:17:14 AEST
- Production URL: https://doxed-btc-bot.fly.dev/
- Exact production revision: `59d24e71126e`
- Bot version: `v31-five-family-atomic-paper`
- Epoch: `epoch-7a84c7ef272dd454a78cfe53`
- Tile-registry signature: `a6a84cf156f65c2c6f73eda5a2421fc0716480536135e15f112304eb68af124b`
- Execution boundary: PAPER_ONLY; Bitfinex blocked and disarmed

## Viewports

| Surface | Viewport | Result |
| --- | --- | --- |
| Production desktop | 1440 x 1000 | PASS |
| Production narrow/mobile | 390 x 844 | PASS |
| Analyzer desktop | Pending exact generation | NOT YET TESTED |
| Analyzer narrow/mobile | Pending exact generation | NOT YET TESTED |

## Production routes and sections

| Area | Expected | Actual | Result |
| --- | --- | --- | --- |
| Header identity | Exact revision, version, epoch and paper/live boundary visible | Exact `59d24e71126e`, version and epoch rendered; `BITFINEX LIVE: BLOCKED — DISARMED` visible | PASS |
| Navigation | Overview, Decisions and Data anchors navigate correctly | All three anchors navigated to the intended section | PASS |
| Research families | Five independent family tiles and Continuous benchmark visible | All five families plus Continuous rendered | PASS |
| Tile state and controls | State, counters, chase selections and paper eligibility usable | Chase 0/1/2 unchecked and 3/4/5+ checked; controls remained usable | PASS |
| Active Signals | Data or explicit empty reason | `No active signals right now.` rendered as a 14-column table row | PASS after repair |
| Virtual Chase Candidates | Data or explicit empty reason | Truthful no-live-candidate explanation rendered | PASS |
| Pending Orders | Data or explicit empty reason | `No pending orders right now.` rendered as an 11-column table row | PASS after repair |
| Positions | Data or explicit empty reason | `No open paper positions right now.` rendered as an 11-column table row | PASS after repair |
| Expired Orders | Data or explicit empty reason | Truthful zero-state text rendered | PASS |
| Trades | Data or explicit empty reason | Truthful zero-state text rendered | PASS |
| AI History | Recent shared calls and final decisions visible | Recent calls rendered; latest inspected decisions were NO_TRADE/REJECT | PASS |
| Storage | Runtime and mirror/storage status populated | Storage card populated and cleanup status visible | PASS |
| Mobile layout | No page-level clipping; wide tables remain usable | Document width 375 within 390 viewport; tables use intentional internal horizontal scrolling | PASS |
| Console | No production-origin errors | No production-origin console errors observed | PASS |

## Defect and retest

Initial inspection at revision `4692885fef8d` found header-only empty tables for Active Signals, Pending Orders and Positions. The smallest permanent repair added explicit empty-state rows and focused regression coverage. It was committed as `fd4c6ff88941`. The integrated regression then found that the canonical signal-engine copy and manifest hash had not been updated with the same UI source change. Exact parity was restored in `59d24e71126e`, which is the final deployed identity. Desktop and mobile retesting passed after both repairs.

## Post-repair runtime-cycle evidence

- Observation window ended: 2026-08-28 05:04:10 AEST
- Fly topology: one started machine `7844910f3024d8`; one dashboard owner `dashboard-7002-pid-664-9372970236cd`, PID 664
- Exact revision remained `59d24e71126e`; no restart, owner change or WebSocket reconnect occurred
- A `/ready` probe truthfully failed closed for 14.47 seconds with `READINESS_STABILIZING` while `/health` remained alive; it recovered without intervention and did not become false-ready
- After recovery, two additional shared AI cycles completed as `NO_TRADE`: `scan-f0056ba49aeb` and `scan-d40f6f946660`
- AI calls advanced 8 to 10; post-AI submitted/completed advanced 16/16 to 20/20; both post-AI workers advanced 8 to 10; coordinator returned `IDLE`
- Tape advanced 1,356 to 1,731 rows with zero write failures; WebSocket tick, price and BBO all advanced
- Paper positions/orders remained 0/0
- Authenticated Bitfinex audit remained authoritative, fresh and flat at 0/0 with no orphan IDs
- Effective boundary remained PAPER_ONLY; `live_armed=false`; Bitfinex execution disabled

Runtime-cycle retest status: **PASS**. The later scheduled analyzer-cycle gate remains separate and pending.

## Screenshots

- `C:\Users\danis\AppData\Local\Temp\production-59d24e7-desktop-data.png`
- `C:\Users\danis\AppData\Local\Temp\production-59d24e7-desktop-empty-states.png`
- `C:\Users\danis\AppData\Local\Temp\production-59d24e7-mobile.png`
- `C:\Users\danis\AppData\Local\Temp\production-59d24e7-mobile-empty-states.png`

Viewport screenshots were captured successfully. A single full-page CDP screenshot was not used because the approximately 8.9k-pixel page capture timed out; supported viewport and section screenshots provide the QA evidence above.

## Remaining before a complete Visual QA claim

1. Allow the sole Fly mirror worker to atomically publish exact revision `59d24e71126e`.
2. Regenerate the analyzer from that exact mirror and clean epoch.
3. Prove analyzer API, manifest and rendered-generation parity.
4. Inspect every analyzer primary tab, sub-tab, dedicated policy route, report explorer item, archive and download on desktop and mobile.
5. Append analyzer screenshots, defects and retest results to this receipt.
