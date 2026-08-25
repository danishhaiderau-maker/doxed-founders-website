# BTC V3.1 Agent Contract

## Canonical workspace

- Work only from `C:\DoxxedCrypto\btc-v31-current`.
- Never use OneDrive for source, runtime data, reports, logs, temporary files, synchronization, deployment, analyzer input, or process working directories.
- Treat any OneDrive checkout or artifact as stale and non-authoritative.

## Frozen execution architecture

- Every research tile has an independent toggle, policy identity, lock, capacity, order lifecycle, position lifecycle, ledger, and analyzer cohort.
- Tile OFF means no paper or live orders; counterfactual/shadow collection may continue.
- Tile ON means paper-order eligibility only after its policy and safety gates pass.
- A tile toggle never arms Bitfinex.
- Relay OFF means paper only.
- Relay ON may copy only new, signed, allowlisted paper intents created after arming. It must never copy historical or already-open paper state.
- The user normally arms or disarms the Bitfinex relay. For the current goal,
  the user has explicitly delegated arming authority to the primary agent only
  after every technical-readiness, exact-size, protection, partial-reduction,
  restart-recovery, reconciliation, analyzer-parity, dashboard-truthfulness,
  visual-QA, and safe-boundary gate is current and GREEN. Any uncertainty must
  fail closed. This delegation never permits early arming, upward size rounding,
  strategy/risk expansion, copying historical paper state, or force-closing
  real exposure.

## Live-test safety

- The current requested live-test configuration is a maximum allocation/margin input of `$0.20-$0.25` per eligible trade at `100x`, subject to exchange minimums and actual accepted order size.
- Do not describe this as a `$0.20-$0.25 maximum loss`. At 100x it represents roughly `$20-$25` notional exposure, and realized loss can exceed the posted margin through liquidation, slippage, funding, partial fills, or missing stops.
- Verify the effective exchange quantity, leverage, margin mode, liquidation estimate, stop coverage, reduce-only behavior, and authenticated order/position state before calling live copy ready.
- If Bitfinex cannot accept this size or enforce its protection, fail closed; never silently round up or increase allocation, leverage, concurrent exposure, or risk limits.
- Automatic restart or deploy is allowed only when its documented safety gates pass. Never force-close real exchange exposure.

## Registry-driven tile lifecycle

- `services/btc-conservative-agent/combo_pathway_config.py` is the sole canonical
  active-tile registry. Runtime, API, production dashboard, collector, mirror,
  analyzer, monitoring, and tests must derive their roster from it; do not add a
  second hard-coded tile list.
- Current registered tiles are Patient Chase baseline, Continuous benchmark, and
  Protected W234 paper research. The number of tiles is not an architecture
  constant; the frozen toggle/paper/relay/identity rules above are.
- Adding a tile requires one registry specification with a unique lane, policy
  signature, ID prefix, toggle key, default state, relay eligibility, and complete
  entry/exit/risk metadata, followed by registry validation, cross-layer tests,
  signal-engine parity, analyzer parity, and rendered visual QA.
- Retiring a tile requires removing it from the active registry and display order,
  adding its lane token to `RETIRED_TILE_LANES` for at least one release, deleting
  its runtime/API/UI/analyzer/monitoring implementation and dedicated tests, and
  proving no executable or current-cohort reference remains. Merely hiding its
  card or disabling its toggle is not retirement.
- Generic platform stability, lifecycle, reconciliation, evidence, and safety
  primitives must be retained when a policy tile is retired. Policy-specific dead
  paths must be deleted so obsolete experiments cannot accumulate.
- Historical evidence is immutable, quarantined, and readable only as opaque
  archive data; it must never keep retired execution code or current analyzer
  cohorts alive.
- Create or promote a new relay-capable tile only after explicit user approval and
  the applicable qualification and technical-readiness gates pass. New research
  tiles default to paper-only and relay-ineligible.

## Cross-layer change rule

Any schema, strategy, lifecycle, risk, relay, policy-identity, or collection change must be assessed and updated as one atomic system across:

1. collector and execution runtime;
2. main Fly dashboard and authenticated API;
3. analyzer loader, reports, API, and local dashboard;
4. mirror/sync and manifests;
5. regression tests and operational monitoring;
6. documentation and the active goal when behavior changes materially.

Do not claim completion when only source wiring, one dashboard, or one report is updated. Verify exact deployed revision, current epoch, runtime behavior, dashboard truth, analyzer parity, and evidence integrity.

## Repair-first monitoring

- Positive progress is required; a live process or HTTP 200 is insufficient.
- Treat stale WebSocket/trade ticks, stale AI cadence, stopped counters, unavailable locks, orphan intents, provisional-after-terminal events, identity drift, mirror lag, analyzer staleness, or dashboard contradictions as failures.
- Preserve diagnostics and quarantine contaminated intervals before repair.
- Prefer the smallest safe repair, then test, deploy at a safe boundary, explicitly resume the intended mode, and prove at least two complete advancing cycles.
- Keep Bitfinex fail-closed when lifecycle, partial-close, reconciliation, or risk evidence is incomplete.
