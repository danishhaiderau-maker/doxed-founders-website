# BTC V3.1 visual QA receipt — 2026-08-26

## Scope and identity

- QA time: 2026-08-26 22:11–22:15 AEST
- Production URL: `https://doxed-btc-bot.fly.dev/`
- Analyzer URL: `http://127.0.0.1:9001/`
- Production source revision: `ba9a4f1cb8e5`
- Analyzer candidate revision: `30d48b485abf3d3c6d0abcde9fafefbba0ca00d1`
- Signed collection epoch: `epoch-f2ea95e53b3ff599a9419514`
- Tile-registry signature: `a6a84cf156f65c2c6f73eda5a2421fc0716480536135e15f112304eb68af124b`
- Evidence status: candidate visually verified locally; production deployment remains blocked by a non-flat paper boundary.

## Production dashboard

Browser QA inspected the rendered production dashboard at desktop width without mutating controls.

| Surface | Expected | Actual | Status |
| --- | --- | --- | --- |
| Header and provenance | Current runtime, collector, revision and research boundary are explicit | `v31-five-family-atomic-paper`, `collector_v3.1`, revision `ba9a4f1cb8e5`, paper-only labels rendered | PASS |
| Navigation | Overview, Decisions and Data targets exist | All three navigation controls rendered with the correct anchors | PASS |
| Five family tiles | Independent family labels, parameters, state and current counters | All five family cards rendered with policy IDs, pending/open/closed/PnL and settings-period rows | PASS |
| Continuous benchmark | Separate benchmark card and independent counters | Continuous rendered as Tile 6 and remained clearly labelled benchmark | PASS |
| Activity tables | Current signals, positions, pending orders, trades and AI history are visible | Active Signals and Positions rendered current signed rows; the lower activity section was reachable and usable | PASS |
| Safety boundary | Tile ON is paper-only; Bitfinex relay is not implied | Cards state PAPER ONLY and relay fail-closed; authenticated check separately proved zero exchange exposure and relay PAUSED | PASS |
| Runtime freshness | Fresh WebSocket, AI and process state | `/health` and `/ready` were genuinely ready during QA; live ages advanced | PASS |

Production screenshot evidence was captured in the in-app browser during this QA run. It showed the current Active Signals and Positions tables and the five-family/Continuous layout without placeholder content.

## Analyzer dashboard

Desktop and narrow/mobile browser QA was performed against the local candidate. Tables intentionally use horizontal scrolling at narrow width rather than truncating columns.

| Route or report | Expected | Actual | Status |
| --- | --- | --- | --- |
| Safe Policy Genome V3.1 | Current epoch, independent episodes, integrity and deduplicated policies | 23 opportunities, 138 decisions, 112 terminal lifecycles, 26 segments, 280 policies; integrity passed | PASS |
| Top 100 Policy Combos | Family-balanced, no more than two rows per family, no fabricated qualification | Ten current shortlist rows, two per each of five families; qualification remained blocked | PASS |
| Exit Combos | Current executed paper and shadow/lab evidence kept separate | 19 executed combinations and 2 shadow/lab combinations; executed left-on-table `$2.76` | PASS |
| Exit Reason Leak | Current peak-to-close evidence, no JSON/NaN failure | Five executed reasons and two shadow/lab reasons rendered; no blank page or JSON parse failure | PASS |
| Exit evidence labels | No legacy/current ambiguity | `CURRENT EXECUTED PAPER + SHADOW/LAB — SEPARATED` rendered on both exit pages | PASS |
| Sparse metrics | Missing MFE cannot produce bare `NaN` in API JSON | Missing values normalize safely; API and page rendered | PASS |
| Policy identity | One entry identity plus one candidate exit identity | Candidate policies contain exactly one `|`; duplicated `entry|old_exit|new_exit` identities removed | PASS |
| Mobile usability | Controls and tables remain usable at narrow width | Navigation wrapped cleanly; wide evidence tables remained horizontally scrollable | PASS |

Analyzer screenshots were captured in the in-app browser for desktop Exit Combos, desktop Exit Reason Leak and the narrow/mobile report layout.

## Regression verification

- Collectable pytest suite: `1034 passed, 7 subtests passed`.
- Script-style checks run separately because they call `sys.exit()` during pytest collection:
  - dashboard timestamps: 11 passed;
  - toggle matrix: 27 passed;
  - API resume deadlock: 22 passed;
  - UTC rollover: 30 passed;
  - paper risk-pause behavior: 16 passed.
- Focused analyzer/dashboard suites from this candidate also passed before the full run.

## Remaining blockers and retest

- Production still runs `ba9a4f1cb8e5`; the analyzer candidate is intentionally not deployed across an active paper boundary.
- At the last authenticated check, Bitfinex had zero positions and zero active orders and remained disarmed, but paper state had 15 open positions and 3 pending orders.
- Required next retest: after the paper boundary naturally reaches zero, deploy the exact candidate, explicitly resume PAPER_ONLY collection, prove two advancing WebSocket/AI/event/tape/mirror cycles, run a later analyzer cycle, and repeat production plus analyzer desktop/mobile QA against revision parity.

## Earlier baseline receipt preserved

The following evidence predates the five-family candidate and remains useful as
historical QA context rather than current-runtime proof.

### Frozen tile lifecycle

- `combo_pathway_config.py::ACTIVE_TILE_REGISTRY` is the sole active roster.
- Runtime, API, dashboard, analyzer, sync and monitoring consume the registry
  manifest/signature instead of maintaining independent active-tile lists.
- Adding or retiring a tile is an atomic registry migration governed by
  `TILE_LIFECYCLE.md`; retirement physically removes tile-specific executable,
  UI/API, analyzer, monitor and test branches after a verified flat boundary.
- Registry garbage-collection and execution-graph tests reject unregistered
  policy modules, executable retired lanes and cross-layer roster drift.

### Earlier automated evidence

- 29 passed: analyzer route/visual contracts, tile registry and active execution
  graph, plus dashboard overlay regressions.
- 65 passed: WebSocket/readiness, non-blocking order-book refresh and dashboard
  live-truth overlay regressions.
- Production download endpoints `/api/export_debug` and `/api/export_csv`
  returned valid ZIP signatures.
- Analyzer `/download/everything` returned a 48,893,929-byte, 500-entry archive
  containing the report manifest and rebuild/readme evidence.

### Earlier first-hand browser QA

- Production: Overview, Decisions and Data links worked; the then-active tile
  cards rendered; collector, revision, epoch and tile states were visible; no
  console warnings/errors were observed.
- Analyzer: every primary group and every report subview was clicked and
  rendered without a dead end; no console warnings/errors were observed.
- Pathway Audit labelled old startup/contract receipts `STALE` and missing
  runtime receipts `NOT PUBLISHED` instead of presenting them as current PASS.
- Mobile 390x844 checks showed no document-level horizontal overflow.

### Earlier repaired contradiction

Production showed fresh WebSocket ticks while Bid/Ask/Spread were blank and
`Last Fetch` was `never`. The fast API cache overlay publishes current BBO,
book timestamp, fetch timestamp, support/resistance, funding, epoch, revision
and tile-registry identity. Ticker processing publishes standardized BBO
size/spread fields used by the dashboard.

### Safety and qualification

- Bitfinex remains disarmed; this receipt does not authorize live trading.
- Tile profit is descriptive only. No strategy is qualified until conservative
  execution, independent chronological OOS and drawdown/risk gates pass.
- Unsafe mutating controls were not clicked during visual QA; their contracts
  are covered by tests and authenticated boundary checks.
