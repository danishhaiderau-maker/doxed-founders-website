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
- Eight tiles are registered, in this display order (tile numbers are derived
  from `COMBO_EXECUTION_LANES` and are never hard-coded): Danish, Danish - no
  early stop, Danish - all sessions, the Continuous baseline benchmark,
  Committed fade (maker), Committed fade (taker) H11, NO_TRADE follow H9 and
  Cross-venue session follow H10. Each has its own lock, orders, positions,
  ledger and analyzer cohort. All are paper-only and relay-ineligible; every
  experiment defaults OFF in source and the baseline defaults ON. Every tile
  card on the Fly dashboard and the :9001 analyzer shows ENTRY / EXIT / RISK
  MANAGEMENT sections generated from registry metadata (`tile_card_sections.py`):
  live exits in first-trigger-wins order, shadow-only exits listed separately,
  and size as "$0.25 margin @100x ~ $25 notional" (never "max loss"). The
  registry validator fails when any tile lacks `signal_summary`,
  `live_exit_order`, known `shadow_exits` or a complete card.
- Composite exits (`COMPOSITE_FIRST_TRIGGER_WINS`) run HARD_STOP ->
  BREAKEVEN_LOCK -> ATR_TRAIL -> EARLY_CUT -> TIME_EXIT, each optional, in the
  order `family_policy_common.exit_action` evaluates them; the registry
  `exit_order` must equal that runtime order. The shadow-exit set
  (`SHADOW_EXIT_SET`, schema `tile_shadow_exit_set_v1`) is recorded for
  analysis only and never executes.
- The three Danish tiles (`FAMILY_DANISH_CF`, `FAMILY_DANISH_CF_NOES`,
  `FAMILY_DANISH_CF_ALL_SESSIONS`; prefixes `dcf`/`dcn`/`dca`) fade shared calls
  where the AI committed to an explicit LONG/SHORT equal to the score-led side
  (NO_TRADE, mismatches, ties and errors refuse). Entry
  (`maker_confirm_market_time_exit_binding.py`): a passive limit 0.10% better
  than the decision-time price; if the mid first moves 3 bp in the trade
  direction, the limit is replaced by one marketable limit capped 5 bp beyond
  the confirmation price (skipped when the book is already past the cap,
  dropped if unfilled after 3 s); neither within 30 min drops the signal;
  spread >3 bp or BBO >5 s stands aside. Exit: 40 bp catastrophic stop,
  break-even armed at +20 bp locking +5 bp, conditional early cut (-12 bp
  within 5 min while MFE <=+2 bp; Danish A and all-sessions only - live on
  no-early-stop it is shadow-only), 90-minute backstop; three concurrent
  signals. Danish A and no-early-stop trade only ASIA/EU (UTC 0-16); all
  sessions also trades US. Kill: mean <=0 after 80 closes or drawdown > $1.00
  (owner rule); promotion (owner review, never relay) needs >=150 fills over
  >=7 UTC days with a 1 h-cluster lower 95% CI > 0.
- Continuous - August 2026 replica (`FAMILY_CONTINUOUS_AUG_ORIGINAL`,
  prefix `caug`) is the permanent baseline benchmark: paper-only, never
  relay-eligible, and the one tile that defaults ON. It replicates the
  early-August Continuous: its own DeepSeek call on the shared 180 s cadence
  (v3 prompt verbatim, temperature 0, purpose
  `trading_direction_continuous_aug`; no call while the tile is OFF), side =
  higher score, gap >=5, reject when long+short <50, hard reject on structure
  conflict, R2 floor 4; 0.1% maker limit with the 25% remaining-gap chase every
  60 s for 10 min; Scenario C ladder, -12% thesis cut (MFE protect 5%), 30%
  stop, -32% early fail, 40/10 peak-never-loser, 2 h cap; $0.25 flat paper
  margin; tile cap 10 plus the August $15/0.25% same-side duplicate rule.
  Realistic BBO/depth fills are its ledger; the August touch fill is recorded
  as the labelled `aug_touch_fill_shadow`. It is not rechecked against the
  shared call at fill time. Every other tile is paired against it
  (`vs_baseline`); it is never a deflated-Sharpe trial.
- Committed fade (maker) (`FAMILY_COMMITTED_FADE_MAKER_90`, prefix `cfm`),
  "HINT - 3-day REALISTIC_V1 walk-forward, CI spans 0", is frozen: same
  committed-call fade, one passive maker limit 0.10% beyond the decision-time
  last price (never past the touch, no chase, expires unfilled after 30 min,
  BBO older than 5 s stands aside), 90 minutes after fill or a 40 bp
  catastrophic stop; three concurrent signals (`maker_time_exit_binding.py`).
  Every shadow exit is recorded against it; none executes. Pre-registration
  H8: promotion needs >=150 fills over >=7 UTC days with >=3 each of
  ASIA/EU/US sessions, 1 h-cluster lower 95% CI > 0, the 5 s-delay shadow
  positive, both sides >= 0, both halves positive, no day > 30% of profit and
  replay parity <=1 bp; kill when the mean is <=0 after 80 fills, the upper CI
  < +2 bp after 150, a trade < -60 bp or >1% stale-feed share, drawdown > $1,
  day 21, or pause on any defect.
- The hypothesis tiles share `tile_pre_registration_hypothesis_v1`:
  promotion (owner review, never relay) needs the tile's minimum fills over
  >=7 UTC days with >=3 days in each of its sessions, 1 h-cluster lower 95%
  CI > 0, both halves positive, no day > 30% of profit and replay parity
  <=1 bp; kill when the mean is <=0 after K1 fills, the upper CI < +2 bp after
  K2 fills (when registered), a trade < -60 bp or >1% stale-feed share,
  drawdown above K4, the day-K5 time box (when registered), or pause on any
  defect.
- Committed fade (taker) (`FAMILY_COMMITTED_FADE_TAKER_90`, prefix `cft`,
  H11): the committed-call fade entered as a taker (5 bp cap, 3 s TTL, spread
  <=3 bp), ASIA/EU sessions only; 40 bp stop, break-even +20 -> +5 bp, ATR
  trail 1.5 ATR armed at +2 ATR (fill-time 3-minute ATR14), conditional early
  cut -12 bp in 5 min while MFE <=+2 bp, 90-minute backstop; three concurrent
  signals. K1 80, K2 150, min fills 150, K4 $1.00.
- NO_TRADE follow (maker) (`FAMILY_NOTRADE_FOLLOW_MAKER_60`, prefix `ntf`,
  H9): trades only shared calls where the AI said NO_TRADE, on the score-led
  side (ties, errors and committed calls refuse); passive maker limit 0.15%
  better than the decision-time last price, chased in windows 2/3/4 by 25% of
  the remaining gap every 180 s, never through the touch, unfilled after
  60 min expires; 40 bp stop, break-even +20 -> +5 bp, ATR trail 1.5 ATR armed
  at +2 ATR, 60-minute backstop (the early cut is shadow-only); ten concurrent
  signals (`maker_chase_time_exit_binding.py`). K1 300, K2 600, min fills 500,
  K4 $3.00.
- Cross-venue session follow (`FAMILY_XVENUE_SESSION_FOLLOW_60M`, prefix
  `xvs`, H10), no AI: fires when either the generic cross-venue lead rule
  (`cross_venue_lead.py`) or premium rule (`cross_venue_premium.py`) triggers
  (opposite triggers = no trade), only in UTC sessions allowed by the frozen
  session map; taker with a 5 bp cap and 3 s TTL; 40 bp stop, break-even
  +20 -> +5 bp, 60-minute backstop (trail and early cut are shadow-only);
  three positions, >=5 s between submissions, <=60/hour
  (`cross_venue_session_follow.py`, shadow rows in `xvs_shadow_signals.jsonl`).
  K1 300, K2 500, min fills 500, K4 $1.00. Clock tiles are excluded from
  shared-AI pairing and scored alone.
- Cross-venue lead (`FAMILY_XVENUE_LEAD_60S`), Cross-venue premium
  (`FAMILY_XVENUE_PREMIUM_60S`), Trend Fade 60 (`FAMILY_TREND_FADE_60`), Trend
  Fade 60 - committed calls only (`FAMILY_TREND_FADE_60_COMMITTED`), the Trend
  Fade 60 ladder, the three Dynamic Adaptive tiles
  (`FAMILY_ADAPTIVE_REGIME`, `FAMILY_ADAPTIVE_REGIME_LADDER`,
  `FAMILY_ADAPTIVE_REGIME_LADDER_BE`), the five former family tiles and the
  legacy `CONTINUOUS` lane token are retired (`RETIRED_TILE_LANES`); their
  history is opaque archive data. The cross-venue evaluator loop, feeds,
  shadow collection and latency instrumentation stay as generic primitives
  serving the session-follow tile. The `CONTINUOUS` token stays retired: the
  baseline is a new lane with its own signed identity, not a revival of
  archived rows. A future tile is promoted only if it passes the OOS
  promotion gate.
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
