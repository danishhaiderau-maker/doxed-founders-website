# Collector / AI cadence starvation repair — 2026-08-29

Status: local repair validated; production post-deploy proof pending.

## Observed incident

- Production source revision: `198c70ff0b6a40e96785cfd0bbf45d79ca27f933`.
- The scheduled AI call began at `2026-08-29T02:45:57Z`, returned at
  `02:46:01Z`, and completed at `02:46:03Z`.
- Post-decision work then stalled while collector maturation processed the
  durable provisional backlog. Watchdog failures reached 6/6 and the paper bot
  exited with code 75 at `02:56:26Z` (`AI_CADENCE_STALLED`).
- Market WebSocket data remained fresh, the trade lock was available, and
  exchange execution was not implicated. The Fly machine and persistent volume
  did not restart; only the bot process restarted under its supervisor.
- Thread traces showed the AI owner waiting in compressed-shadow recovery and
  rejected-opportunity persistence behind `_collector_epoch_lock`, while the
  maturation poll repeatedly re-read the durable journal and processed the
  entire pending map.

## Repair contract

1. Compressed-shadow ownership uses `_compressed_shadow_lock`; recovery, arm,
   poll, terminal removal, and fresh-epoch reset share that lock.
2. The durable provisional journal is restored at startup. Cooldown-limited
   periodic reconciliation runs on a background daemon and takes the epoch
   lock for only one short map mutation at a time, so an individually missing
   row is recovered without blocking the AI owner.
3. Terminal-ready/closed paths are processed before waiting paths.
4. The effective batch adapts toward a 60-minute ready-backlog sweep, is capped
   at 25 records per minute by default, and releases the collector lock between
   records so AI persistence can interleave.
5. Runtime state publishes collector maturation backlog, sweep and age health.
6. Lifecycle restart skips the 212 MB order-intent identity scan when there are
   no awaiting signals, and expected-order reconciliation throttles from scan
   completion rather than its start.

## Startup restore evidence

- Revision `198c70ff0b6a` took 435.493 seconds from process boot at
  `03:18:41.805Z` to startup completion at `03:25:57.298Z`.
- The dominant input was `v3/ledgers/order_intent.jsonl`: 212,114,560 bytes in
  585 large JSON rows.
- `load_paper_lifecycle` spent 158.642 seconds building an order-intent index
  despite the snapshot containing zero awaiting signals.
- Forced expected-order reconciliation then decoded the V3 ledgers, and its
  pre-scan throttle timestamp allowed `reconcile_stale_signals` to repeat the
  same expensive scan immediately. This was deterministic startup debt, not
  data loss or a missing Fly volume.

## Local evidence

- Canonical `bot.py` / generated `engine.py` normalized hash:
  `29e33ef3853e`.
- Final combined collector, compressed-shadow, lifecycle, cleanup-lock and
  V3 reconciliation suite: 70 passed (53 + 17), including the Windows worker
  teardown regression and completion-time throttle regression.
- Signal parity including full import/flag probe: passed; zero cross-module
  mismatches.
- Canonical analyzer/store/sync/download contract group: 99 passed.
- Fly runtime Node contract: 18/18 passed; four deployment PowerShell scripts
  parsed with zero errors.
- Final broad service suite after all repairs: 1,274 passed plus 7 subtests in
  577.65 seconds. The earlier analyzer missing-report truthfulness failure is
  covered by a checked-in fail-closed regression and deployment CI.
- Five legacy script-style safety suites passed independently: 106 assertions
  total (22 + 30 + 11 + 16 + 27).

## Required production closure

- authenticated exchange and paper strict-flat proof;
- Bitfinex disarmed and forced paper proof;
- deploy exact repaired revision;
- runtime reaches `/ready` on the exact revision/epoch/signature;
- two complete advancing WebSocket, tick, AI, opportunity, tape and mirror
  cycles without `AI_CADENCE_STALLED`;
- fresh analyzer generation and later scheduled generation both pass parity;
- desktop/mobile production and analyzer visual QA.
