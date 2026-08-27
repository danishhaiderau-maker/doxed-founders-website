# Production Visual QA Receipt — revision eae441b81eea

- Tested at: 2026-08-27 23:18 AEST
- Production: https://doxed-btc-bot.fly.dev/
- Runtime revision: `eae441b81eea`
- Full source revision: `eae441b81eea288f69077d5ed44e916e642ee52b`
- Fresh epoch: `epoch-7a84c7ef272dd454a78cfe53`
- Method: first-hand Codex in-app Browser inspection; read-only navigation only
- Browser console: no application-origin errors or warnings; unrelated wallet-extension warnings excluded

## Viewports and routes

| Viewport | Route / section | Expected | Actual | Result |
| --- | --- | --- | --- | --- |
| 1526×914 desktop | `/` Overview | Current runtime, controls and boundaries render | Revision and epoch current; live status populated | PASS |
| 1526×914 desktop | `/#pathwayLab` Decisions | Heading remains visible below sticky nav | nav bottom 52.3 px; target top 72.3 px; 20.0 px clearance | PASS |
| 1526×914 desktop | `/#activityTables` Data | Heading remains visible below sticky nav | nav bottom 52.3 px; target top 72.0 px; 19.7 px clearance | PASS |
| 390×844 mobile | `/` Overview | Single-column usable layout | target top 72.5 px; no body overflow | PASS |
| 390×844 mobile | `/#pathwayLab` Decisions | Heading and all six cards remain usable | target top 72.2 px; 19.9 px clearance | PASS |
| 390×844 mobile | `/#activityTables` Data | Heading visible; wide tables scroll inside containers | target top 72.0 px; 19.8 px clearance | PASS |

Desktop document width was `1511/1511` and mobile document width was `375/375`; neither viewport had body-wide horizontal overflow.

## Production content inspected

- Header, build, revision and clean-epoch identity.
- Overview, Decisions and Data navigation.
- Chandelier, Fixed ATR Target, ATR Trail, Hybrid Runner, MFE Giveback and Continuous panels.
- Tile state, counters, policy/protection text and paper-only labeling.
- Execution controls without mutation.
- WebSocket, price/BBO, AI, heartbeat and storage state.
- Active Signals, Virtual Chase Candidates, Pending Orders, Positions, Expired Orders, Trades and AI History.
- Explicit boundary labels: `PAPER ENTRIES: ALLOWED`, `BITFINEX LIVE: BLOCKED — DISARMED`, and tile-level `PAPER ONLY`.

## Defect and retest

Initial QA on revision `276199a67706` reproduced a sticky-navigation anchor defect on desktop and mobile: `#pathwayLab` and `#activityTables` landed at the top of the viewport and their headings were hidden beneath the approximately 52 px navigation bar.

Repair revision `38b8d99` added a 72 px scroll margin to all three navigation targets while preserving exact `bot.py` / `engine.py` parity. Regression coverage passed in `test_dashboard_mobile_activity_tables.py` and `test_v31_policy_dashboard_parity.py`.

Retest on deployed revision `eae441b81eea` passed for all six viewport/route cases above. No controls were changed.

## Screenshot evidence

The first QA and post-repair retest each captured the desktop and mobile Overview, Decisions and Data states directly in the Codex Browser session. The browser integration emitted those six captures as in-conversation visual artifacts rather than filesystem files; no screenshot path is claimed.

## Remaining scope

This receipt covers the production dashboard only. Analyzer desktop/mobile QA requires a completed exact-revision mirror synchronization and a fresh analyzer generation; it is tracked separately and is not claimed by this receipt.
