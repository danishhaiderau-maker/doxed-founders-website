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
- Four tiles are registered, all honest paper experiments with no proven edge
  that consume the same shared AI call with independent locks, orders,
  positions, ledgers and analyzer cohorts. Dynamic Adaptive
  (`FAMILY_ADAPTIVE_REGIME`): trailing 15-minute realized-vol
  regime from Bitfinex 1m candles (CALM below the frozen p40, EXTREME above the
  frozen p90), a fast-move z-score, taker with a bounded protection cap in
  NORMAL or on a fast move, a short-lived maker limit within one tick in CALM,
  stand-aside in EXTREME or when the initial stop is at least 40 bps, and an
  ATR Trail exit (SL 1.5 / arm 0.75 / trail 1). Direction comes from score-led
  admission; a raw AI NO_TRADE or a score gap below 5 never trades. Adaptive +
  Profit Lock (`FAMILY_ADAPTIVE_REGIME_LADDER`) uses the identical entry and
  admission, ATR Trail, and the Scenario-C ladder (8→5 … 150→120 margin % at
  100x) with no break-even rung. Adaptive + Break-even + Profit Lock
  (`FAMILY_ADAPTIVE_REGIME_LADDER_BE`) is the same plus a break-even rung (peak
  +4% margin locks +1%, the round-trip fee plus a 1 bp exit-slip buffer). For
  both ladder tiles the effective stop is the more protective of the ATR
  stop/trail and the armed lock, locks book at the side-correct quote that
  crossed them, the 30% margin hard stop and 2 h cap apply, and each holds at
  most one position. Trend Fade 60 (`FAMILY_TREND_FADE_60`) is an owner-approved
  beta: its own lane admission trades the opposite of the shared call's
  score-led side (raw AI NO_TRADE and small gaps still trade; ties, invalid
  scores and AI errors refuse), enters taker at the signal with a 5 bp price
  cap and 15 s TTL, stands aside when the spread exceeds 1.68 bp or the BBO is
  stale, and exits only at 60 minutes or a 40 bp catastrophic stop booked at the
  crossing quote (no ladder, break-even, trail or target; one position). Its
  label discloses "in-sample +$1.30 / 47 trades; expected heavy decay". All
  four default OFF, are paper-only, and are relay-ineligible. The two ladder tiles carry a frozen registry
  `pre_registration` scored by the analyzer's paired comparison against Tile 1
  on identical signals: promotion (owner review, never relay) needs >=400 fills
  over >=14 days, conservative per-fill EV lower 95% CI > 0, both halves
  positive, Deflated Sharpe >= 0.95 across live hypotheses, and a positive
  paired lower CI against Tile 1; kill on per-fill EV upper CI < 0 after 150
  fills, a paired upper CI < 0 against Tile 1 after 300 signals, a stop failure,
  drawdown above $1.50, or 21 days without promotion. Trend Fade 60 has its own
  trade-count pre-registration: promotion (owner review) needs >=150 trades, a
  per-trade EV lower 95% CI > 0, both halves positive and no 2 h window above 30%
  of profit; kill when down $0.40 after 40 trades, not positive after 80, any
  trade worse than -60 bp, drawdown above $1.00, or day 14 without promotion.
  The paired view compares it with Tile 1 (the AI's own side). The five former family tiles
  and the Continuous comparison label are retired (`RETIRED_TILE_LANES`); a
  future tile is promoted only if it passes the OOS promotion gate.
  The number of tiles is not an architecture constant; the frozen
  toggle/paper/relay/identity rules above are.
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
- A tile roster change is one atomic registry transaction. It is incomplete
  until the signed registry receipt, runtime/API/dashboard roster, mirror,
  analyzer, monitoring, tests, and rendered visual-QA receipt all agree on the
  same ordered tile set and exact deployed revision.
- Do not preserve dormant policy branches "for later". Reusable generic
  execution and evidence primitives stay; tile-specific policy code, routes,
  labels, reports, tests, flags, and monitor checks leave with the retired tile.
- Every `paper_policy_*.py` module must have exactly one owner in the registry's
  `implementation_modules`. Orphan modules fail the tile-registry contract and
  must be registered or physically deleted. Follow `TILE_LIFECYCLE.md` for the
  bounded add/retire procedure.
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
