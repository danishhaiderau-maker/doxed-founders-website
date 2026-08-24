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
- Only the user may arm or disarm the Bitfinex relay.

## Live-test safety

- The current requested live-test configuration is a maximum allocation/margin input of `$0.20-$0.25` per eligible trade at `100x`, subject to exchange minimums and actual accepted order size.
- Do not describe this as a `$0.20-$0.25 maximum loss`. At 100x it represents roughly `$20-$25` notional exposure, and realized loss can exceed the posted margin through liquidation, slippage, funding, partial fills, or missing stops.
- Verify the effective exchange quantity, leverage, margin mode, liquidation estimate, stop coverage, reduce-only behavior, and authenticated order/position state before calling live copy ready.
- If Bitfinex cannot accept this size or enforce its protection, fail closed; never silently round up or increase allocation, leverage, concurrent exposure, or risk limits.
- Automatic restart or deploy is allowed only when its documented safety gates pass. Never force-close real exchange exposure.

## Four-tile contract

- Tile 1: Patient Chase baseline (`OFFSET_0.29_CHASE... | atr_tp_k2.5`).
- Tile 2: Continuous benchmark.
- Tile 3: protected static Patient Chase candidate with account-risk sizing, initial ATR stop, partial profit-taking, break-even, trailing runner, final ATR target, and time cap.
- Tile 4: protected regime-adaptive Patient Chase candidate. Regime may change during a trade, but protection, risk, and position size must never widen after entry; every transition must be recorded.
- Tiles 3 and 4 must remain fail-closed for live copy until the Bitfinex relay supports and proves idempotent reduce-only partial exits and reconciliation.

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
