# Visual QA receipt — 2026-08-26

Scope: V3.1 four-tile production dashboard, local read-only analyzer, canonical
tile registry, and the BBO/report-truth repairs in this candidate.

## Frozen tile lifecycle

- `combo_pathway_config.py::ACTIVE_TILE_REGISTRY` is the sole active roster.
- Runtime, API, dashboard, analyzer, sync and monitoring consume the registry
  manifest/signature instead of maintaining independent active-tile lists.
- Adding or retiring a tile is an atomic registry migration governed by
  `TILE_LIFECYCLE.md`; retirement physically removes tile-specific executable,
  UI/API, analyzer, monitor and test branches after a verified flat boundary.
- Registry garbage-collection and execution-graph tests reject unregistered
  policy modules, executable retired lanes and cross-layer roster drift.

## Automated evidence

- 29 passed: analyzer route/visual contracts, tile registry and active execution
  graph, plus dashboard overlay regressions.
- 65 passed: WebSocket/readiness, non-blocking order-book refresh and dashboard
  live-truth overlay regressions.
- Production download endpoints `/api/export_debug` and `/api/export_csv`
  returned valid ZIP signatures.
- Analyzer `/download/everything` returned a 48,893,929-byte, 500-entry archive
  containing the report manifest and rebuild/readme evidence.

## First-hand browser QA

- Production: Overview, Decisions and Data links worked; all four distinct tile
  cards rendered; collector, revision, epoch and tile states were visible; no
  console warnings/errors were observed.
- Analyzer: every primary group and every report subview was clicked and
  rendered without a dead end; no console warnings/errors were observed.
- Pathway Audit now labels old startup/contract receipts `STALE` and missing
  runtime receipts `NOT PUBLISHED` instead of presenting them as current PASS
  or falsely claiming they were never generated. Current analyzer integrity is
  displayed separately.
- Mobile 390x844 checks showed no document-level horizontal overflow on either
  dashboard; all four production tiles remained in the rendered document.

## Repaired contradiction

Production showed fresh WebSocket ticks while Bid/Ask/Spread were blank and
`Last Fetch` was `never`. The fast API cache overlay now publishes current BBO,
book timestamp, fetch timestamp, support/resistance, funding, epoch, revision
and tile-registry identity. Ticker processing also publishes standardized BBO
size/spread fields used by the dashboard.

## Safety and qualification

- Bitfinex remains disarmed; this receipt does not authorize live trading.
- Tile profit is descriptive only. No strategy is qualified until conservative
  execution, independent chronological OOS and drawdown/risk gates pass.
- Unsafe mutating controls (tile toggles, pause/flat, wipe/fresh epoch and relay)
  were not clicked during visual QA; their contracts are covered by tests and
  authenticated boundary checks.
