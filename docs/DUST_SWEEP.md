# Dust Sweep — hands-free flatten of residual exchange qty

**Added:** 2026-07-08
**Scope:** Live-copy relay only (`apps/api/src/trading-agents/signal-subscriber-execution.service.ts`).
The showcase bot (`services/btc-conservative-agent/bot.py`) is untouched — this
feature is relay-side reconcile, not bot logic.

## Why

Fully hands-free trading. When a position on Bitfinex has residual quantity
below the noise threshold (`MIN_QTY_BTC = 0.00004` BTC), it is functionally
closed from the strategy's point of view but still sits on the exchange as a
dust position the user would otherwise have to manually flatten. Dust arises
from:

- Partial fills that left a sub-threshold tail
- Stop scraps where the protective stop fired on all but the last few sats
- Exchange rounding on reduce-only matches

Before this feature, dust positions accumulated on the exchange and broke the
"100% mirror" convergence guarantee (the reconcile loop treated
`|delta| <= MIN_QTY_BTC` as converged, but the dust was still real exposure).
The sweep closes that gap.

## Threshold

`MIN_QTY_BTC = 0.00004` (defined at L67 of the relay, unchanged). A position
is **dust** when:

```ts
Math.abs(position.amount) > 0 && Math.abs(position.amount) < MIN_QTY_BTC
```

## Kill switch

`RELAY_AUTO_DUST_SWEEP` (env var, read per tick via `this.config.get`).

| Value | Behavior |
|---|---|
| `1` / unset / anything else (default) | ON — relay places a flatten market order on detecting dust. |
| `0` | OFF — detection-only. `DUST_SWEEP` audit event still recorded, no market order placed. Operator must close dust manually. |

Default is ON so existing relays get hands-free behavior without a redeploy.

## Detection points

Dust is detected in two places, both inside the existing reconcile loop (no new
tick / no new abstraction):

1. **Per-participant** — inside `reconcileManualClose`, in the `hasExpected`
   branch where the exchange position matches the lot direction but the qty is
   below threshold. Each OPEN participant runs through this every tick.

2. **Aggregate backstop** — inside `reconcileLotLedger`, right after the merged
   exchange position is fetched and before the delta short-circuit. Catches
   dust that survives when all lots are already CLOSED on the ledger but the
   exchange still shows residual qty. If exactly one OPEN participant owns the
   lot, it delegates to the same `sweepDustPosition` path; otherwise it emits a
   detection-only event (no synthesized lot — synthesizing lots is the
   `reconcileUnattributedExchangeFills` path's job).

## Sweep action

`sweepDustPosition(agentId, userId, cycle, participant, meta, creds)`:

1. **Cancel resting stop** — `meta.stopOrderId` is cancelled first using the
   same pattern as `reconcileManualClose` (L6284-6290) so a stale protective
   stop can't fire mid-sweep and realize the wrong residual qty.
2. **Market close** — `this.activeTrading.submitMarketClose(creds, {
   positionDirection, qty: dustQty })`. This is the exact path used by
   `closeVirtualLot` (L5959) and the sim orphan heal (L1927). Direction is
   inferred from the live position sign (`amount < 0` → SHORT) so the sweep
   closes the correct way even if `meta.direction` is stale.

The market order amount is effectively `-position.amount` (the
`submitMarketClose` client negates the qty internally based on direction).

## Safety guards

Mirrors existing money-path patterns:

- **Instance status** — aggregate sweep only runs when
  `instance.status === ACTIVE` (PAUSED is exit-only; a dust flatten is a new
  market order, which we don't place on pause).
- **Participant status** — sweep only runs when the participant is `OPEN` or
  `CLOSED`, never `PENDING_ENTRY` (no real exposure to sweep yet).
- **Strict threshold** — only sweeps when `0 < abs(amount) < MIN_QTY_BTC`.
- **Stop cancel first** — see Sweep action step 1.
- **Try/catch everywhere** — every step is wrapped; the reconcile loop is
  never broken by a sweep failure.
- **Idempotency** — `dustSweepInFlight: Set<string>` keyed by
  `${userId}:${cycleId}` prevents stacking duplicate market orders within the
  same tick window. Cleared in a `finally` block after each attempt.

## Failure handling

If the market order fails (network blip, exchange rejection, etc.):

- The error is logged at `warn` level with the cycle id and message.
- A `DUST_SWEEP` event with `reason: 'market_order_failed'` is recorded.
- **No aggressive retry** — the sweep defers to the next tick (same pattern as
  `cancelStillLive` at L2104). The reconcile loop re-detects the dust on the
  next pass and tries again.

This matches the existing reconcile philosophy: never block the loop, never
throw out of the reconcile path.

## Audit event

Every dust detection (whether or not a sweep was placed) records a
`DUST_SWEEP` event via `this.cycles.recordHireExecutionEvent`:

```ts
{
  venue: 'bitfinex',
  source: 'hire',
  event: 'DUST_SWEEP',
  dust_qty: <abs amount, 8dp>,
  market_order_id: <oid if a market order was placed, else undefined>,
  reason: 'auto_flatten_below_min_qty' | 'market_order_failed' |
          'position_fetch_failed' | 'no_open_owner' | 'multiple_open_owners',
  sweep_enabled: <bool>,
}
```

The `reason` field lets operators filter:

| reason | Meaning |
|---|---|
| `auto_flatten_below_min_qty` | Normal sweep — dust detected and either flattened (sweep enabled) or surfaced (sweep disabled). |
| `market_order_failed` | Sweep attempted but the market order threw — deferred to next tick. |
| `position_fetch_failed` | Couldn't reach the exchange to read the position — deferred. |
| `no_open_owner` | Aggregate dust with no OPEN participant to attribute — surfaced for manual review. |
| `multiple_open_owners` | Aggregate dust with >1 OPEN participant — can't pick an owner, surfaced for review. |

## Files touched

- `packages/utils/src/signal-cycle.ts` — added `DUST_SWEEP` to
  `SignalCycleEventType`.
- `apps/api/src/trading-agents/signal-subscriber-execution.service.ts` — added
  `dustSweepInFlight` tracker, `sweepDustPosition`, `recordDustSweepEvent`,
  `sweepAggregateDust` methods; wired detection into `reconcileManualClose`
  and `reconcileLotLedger`.
- `docs/ENV-VARS.md` — documented `RELAY_AUTO_DUST_SWEEP`.
- `docs/DUST_SWEEP.md` — this file.

## Non-goals

- Does NOT change `MIN_QTY_BTC`.
- Does NOT modify the showcase bot (`services/btc-conservative-agent/bot.py`).
- Does NOT introduce a new tick / scheduler — runs inside the existing
  reconcile loop.
- Does NOT synthesize ledger lots from dust — that's
  `reconcileUnattributedExchangeFills`'s job.
