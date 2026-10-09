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
- FREEZE21B (owner-approved 2026-10-04 15:02 AEDT, re-declared 17:53/17:54
  AEDT: "every strategy must be a visible paper tile";
  `diagnostics/FREEZE21-PROTOCOL-20261004.md`): eleven tiles are registered, in
  this display order (tile numbers are derived from `COMBO_EXECUTION_LANES` and
  are never hard-coded): H-A Committed fade (taker), H-B NO_TRADE follow
  (taker), H-C Premium reversion (no AI), the Random control, then Grok
  Strategist's GS-01 premium follow + ATR TP, GS-02 NO_TRADE follow with a
  regime entry, GS-03 CVD divergence (no AI), GS-04 NO_TRADE follow + ATR TP,
  B1 CVD divergence regime-managed, B2 regime switcher and B3 committed fade
  regime-managed. The GS/B tiles share `regime_bars_3m.py` (3 m ATR/ADX/CVD
  bars from the 1 s tape), `gs_regime_exit_stack.py` and
  `regime_adaptive_binding.py`; each carries
  `tile_pre_registration_gs20261004_v1`. Each has its
  own lock, orders, positions, ledger and analyzer cohort. All are paper-only
  and relay-ineligible and all default ON for the 21-day freeze. Every tile
  card on the Fly dashboard and the :9001 analyzer shows ENTRY / EXIT / RISK
  MANAGEMENT sections generated from registry metadata (`tile_card_sections.py`):
  live exits in first-trigger-wins order, shadow-only exits listed separately,
  and size as "$0.25 margin @100x ~ $25 notional" (never "max loss"). The
  registry validator fails when any tile lacks `signal_summary`,
  `live_exit_order`, known `shadow_exits` or a complete card.
- FREEZE21B mid-epoch additions (owner, 2026-10-05; corrected spec
  `diagnostics/GS05-GS06-TILE-SPECS-CORRECTED-20261005.md`): tiles 12 GS-05
  Premium regime router (`gs5`, H-C/GS-01 premium trigger on its own `gs5xvp`
  evaluator; QUIET stands aside, TREND = H-C's cadence and 60-min hold,
  VIOLENT = GS-01's exits) and 13 GS-06 Committed fade, patient exit (`gs6`,
  H-A entry, every regime traded with VIOLENT as its own cell, BE +25 -> +8,
  2.5 ATR trail, 120-min, cap 2) are appended after the frozen eleven
  (`research_freeze.MID_EPOCH_ADDITIONS`). The frozen eleven stay
  byte-identical (CI recomputes their signature over the first eleven tiles);
  the additions' window starts at their deploy. Both tiles stamp
  `regime_at_signal`/`regime_at_entry`, `regime_cell`, `pre60_side_bp` and
  `atr_bp` on every decision, `shadow_would_have` on stand-asides and
  `shadow_old_gate` on GS-06 VIOLENT entries.
- 21-day research freeze (`research_freeze.py`): one declared data epoch
  (`DATA_EPOCH_ID = ce-20261004-v31-freeze21b` in `fly.toml`); the freeze runs
  21 days from that epoch's `data_epoch.json` start. Inside it every reset path
  (`/api/reset`, `/api/toggle_fresh_collection`, `/api/wipe_fly_only`,
  `/api/fresh_epoch_reset`, `clean_epoch_wipe.py --pre-start execute`) refuses
  with HTTP 409 `RESEARCH_FREEZE_ACTIVE`, except the deploy workflow's one
  boundary reset in the first 60 minutes (status `OPENING`), and
  `/api/toggle_research_lane` may turn a frozen tile ON but not OFF. The only
  way through is the documented override: request body `freeze_override =
  {"confirmation": "BREAK_21_DAY_RESEARCH_FREEZE", "reason": "..."}` (kill-rule
  reason `KILL_RULE:<lane>:<rule>`) or the env pair
  `RESEARCH_FREEZE_OVERRIDE` / `RESEARCH_FREEZE_OVERRIDE_REASON`. CI
  (`test_research_freeze.py`) fails any change to the roster, order, registry
  signature, stack version or `DATA_EPOCH_ID` while the freeze is ACTIVE unless
  `research_freeze.CODE_OVERRIDE` records the owner's approval. `/api/status`
  publishes `research_freeze`.
- Composite exits (`COMPOSITE_FIRST_TRIGGER_WINS`) run HARD_STOP ->
  BREAKEVEN_LOCK -> ATR_TRAIL -> EARLY_CUT -> TIME_EXIT, each optional, in the
  order `family_policy_common.exit_action` evaluates them; the registry
  `exit_order` must equal that runtime order. The shadow-exit set
  (`SHADOW_EXIT_SET`, schema `tile_shadow_exit_set_v1`) is recorded for
  analysis only and never executes.
- Every tile carries `tile_pre_registration_freeze21_v1`: role HYPOTHESIS (the
  three Bonferroni trials, family alpha 0.05, per-test 0.05/3) or CONTROL;
  sample counted as n_eff = distinct UTC hours with a closed fill (target
  >= 150 hours, >= 14 UTC days); kills K1 mean <=0 bp after 80 distinct hours
  (hypotheses), K3 a trade < -60 bp or >1% stale-feed share, K4 drawdown cap
  (hypotheses), K6 defect = pause and quarantine. Day-21 decision (anchored to
  the freeze epoch start; the analyzer counts from registration): PASS = n_eff
  target met AND the 1 h-cluster 98.33% CI lower bound > 0 AND (H-A) the paired
  difference versus the control > 0 -> owner review, never relay; FAIL = mean
  <= 0 or the CI upper bound < +2 bp -> retire; anything else is INCONCLUSIVE
  -> retire (a return needs a new pre-registration). A kill is an owner action:
  toggle OFF with the freeze override, then retire after the freeze.
  `tile_paired_comparison.py` reports `n_eff_distinct_hours`, the Bonferroni CI,
  `day21_status` and the control's `execution_cost_bp`.
- H-A Committed fade (taker) (`FAMILY_COMMITTED_FADE_TAKER_90`, prefix `cft`):
  fades shared calls where the AI committed to an explicit LONG/SHORT equal to
  the score-led side (NO_TRADE, mismatches, ties and errors refuse), taker
  (5 bp cap, 3 s TTL, spread <=3 bp), ASIA/EU sessions only (UTC 0-16); 40 bp
  stop, break-even +20 -> +5 bp, ATR trail 1.5 ATR armed at +2 ATR (fill-time
  3-minute ATR14), conditional early cut -12 bp in 5 min while MFE <=+2 bp,
  90-minute backstop; three concurrent signals. Rule and policy signature are
  unchanged from v11 H11. K1 80 hours, K4 $3.00, min fills 300. Control:
  the Random control.
- H-B NO_TRADE follow (taker) (`FAMILY_NOTRADE_FOLLOW_TAKER_60`, prefix `ntt`):
  trades only shared calls where the raw AI said NO_TRADE, on the score-led
  side (explicit LONG/SHORT, ties and errors refuse), as a taker (the maker
  version got 0 fills); 40 bp stop, break-even +20 -> +5 bp, ATR trail 1.5 ATR
  armed at +2 ATR, 60-minute backstop; all sessions; ten concurrent signals.
  K1 80 hours, K4 $5.00, min fills 600.
- H-C Premium reversion (`FAMILY_PREMIUM_REVERSION_60M`, prefix `pmr`), no AI:
  the per-second `cross_venue_premium.PremiumEvaluator` (shadow rows in
  `xvp_shadow_signals.jsonl`) takes a Bitfinex taker toward Binance/Bybit when
  their premium leaves its trailing 60-minute mean by the fixed +1.75/-1.88 bp
  tails (Bitfinex convergence); 5 bp cap, 3 s TTL; at most one submission per
  15 minutes and 4 per hour; 60-minute hold and 40 bp stop only (every other
  exit is shadow); three positions. K1 80 hours, K4 $3.00, min fills 300.
  Clock tiles are excluded from shared-AI pairing and scored alone.
- Random control (`FAMILY_RANDOM_CONTROL_TAKER_90`, prefix `rnd`): admits
  exactly H-A's committed calls with H-A's entry, session gate and exits, but
  takes its side from a deterministic coin (`sha256("FREEZE21-RANDOM-CONTROL-v1|"
  + shared_ai_call_id)`, first byte even = LONG; no call id refuses). Its mean
  is pure execution cost; it is never a trial or promoted, has no K1/K4, and a
  control CI lower bound > 0 flags `FILL_MODEL_SUSPECT`.
- Retired (`RETIRED_TILE_LANES`; history is opaque archive data): v12 (FREEZE21)
  retired the three Danish tiles (`FAMILY_DANISH_CF`, `_NOES`,
  `_ALL_SESSIONS`), the Continuous August baseline
  (`FAMILY_CONTINUOUS_AUG_ORIGINAL`; replaced as yardstick by the Random
  control), Committed fade (maker), NO_TRADE follow (maker) and Cross-venue
  session follow; earlier: Cross-venue lead/premium 60 s, Trend Fade 60 (all
  variants), the three Dynamic Adaptive tiles, the five former family tiles and
  the legacy `CONTINUOUS` lane token. The generic bindings
  (`maker_time_exit_binding.py`, `maker_chase_time_exit_binding.py`,
  `maker_confirm_market_time_exit_binding.py`), the cross-venue evaluators
  (`cross_venue_lead.py`, `cross_venue_session_follow.py`), feeds, shadow
  collection and latency instrumentation stay as generic primitives (H-C and
  `research/lead_lag_report.py` use them). A future tile is promoted only if
  it passes the OOS promotion gate.
  The number of tiles is not an architecture constant; the frozen
  toggle/paper/relay/identity rules above are.
- A deploy never changes a tile's paper on/off setting
  (`scripts/fly_postdeploy_active_gate.py`): each lane is restored to the
  operator state captured before maintenance (`PRIOR_OPERATOR_STATE`), a lane
  new in that revision starts at its registry `default_enabled`, and
  restart/recovery jobs keep the bot's persisted toggle state (on the
  `/app/data` volume). `PAPER_TILES_HOLD_OFF` still forces listed lanes OFF.
- Adding a tile requires one registry specification with a unique lane, policy
  signature, ID prefix, toggle key, default state, relay eligibility, and complete
  entry/exit/risk metadata, followed by registry validation, cross-layer tests,
  the canonical signal probe (`scripts/signal_probe.py --full`), analyzer parity,
  and rendered visual QA.
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
