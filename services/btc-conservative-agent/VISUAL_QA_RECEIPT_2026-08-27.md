# Visual QA Receipt — 2026-08-27

- Deployed revision: `e64598b9cfdb5616d9cc15d6bac9b320d67d1778`
- Runtime: `v31-five-family-atomic-paper`
- Collector: `collector_v3.1`
- Epoch: `epoch-7a84c7ef272dd454a78cfe53`
- Production: `https://doxed-btc-bot.fly.dev/`
- Analyzer: `http://127.0.0.1:9001/`
- Viewports: desktop and 390 x 844 mobile

## Production dashboard

- Overview, Decisions, and Data navigation passed.
- No off-viewport interactive controls at either viewport.
- Global chase first-submit selection rendered truthfully: 0, 1, and 2 unchecked; 3, 4, and 5+ checked.
- Tile text distinguishes the per-tile reprice template from the global first-submit bucket gate.
- Revision, collector, epoch, paper-only state, and disarmed relay labels were current.

## Analyzer dashboard

- Four primary tabs and all nine Chase & Exits routes passed navigation QA.
- Exit Combos rendered all 16 expanded analysis views.
- Evidence remained separated into PAPER, SHADOW/LAB, CONSERVATIVE BBO/DEPTH, and IDEAL TOUCH DIAGNOSTIC worlds.
- Empty current-epoch terminal cohorts displayed `SOURCE_EMPTY_OR_UNAVAILABLE`; no zero-filled or fabricated performance metrics were shown.
- Top Policies, Legacy Gap, Exit Reason Leak, Ladder Simulator, and Historical Leakage displayed current data or explicit insufficiency states.
- Analyzer revision matched the Fly mirror revision and reports regenerated during QA.

## Result

PASS. No current visual blocker was reproduced. This receipt verifies presentation and navigation; it does not qualify any strategy for live trading.
