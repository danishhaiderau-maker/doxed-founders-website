# Exchange Dynamic Stops (Option A — Scenario C safety net)

**Status:** SHIPS DARK. Default OFF. Enable per account via Railway env.
**Added:** 2026-07-08
**Kill switch:** `EXCHANGE_DYNAMIC_STOPS_ENABLED` (default: `false`)

## Why this exists

The live-copy relay placed an initial protective stop at the FILLED tick but
then never moved it — the Phase 2 "exit convergence" path intentionally left
only a wide disaster stop on the exchange so the showcase bot's EXIT would be
authoritative. On a recent 21% peak run this caused ~$5 of drift vs the
showcase bot: the exchange stop stayed at `63138` (initial disaster level)
while the showcase bot advanced its internal exit ladder through `10→6`,
`19→17`, `40→28` as profit grew. Investigation confirmed `UPDATE_STOPS`
events were updating the relay ledger only, not calling the exchange.

Option A adds **one new responsibility to the relay**: keep the protective
stop on Bitfinex synced to the current Scenario C ladder rung, between entry
and the showcase bot's final EXIT. The showcase bot remains the decision
maker for entries, exits, and signal selection.

## The decision boundary

| Layer | Owner | What it does |
|---|---|---|
| Entries / signal selection / thesis EXIT | Showcase bot (`services/btc-conservative-agent/bot.py`) | Unchanged. Picks what to trade, when to enter, when to EXIT. |
| Protective stop on the exchange | Relay (this feature) | NEW. Advances the stop through the Scenario C ladder rungs as unrealized margin grows, so a sudden reverse realizes the rung's protected %. |

**Showcase EXIT always wins:** when the showcase bot decides to EXIT, the
relay cancels any pending protective stop before market-closing. The
protective stop is a safety net for the gap between entry and EXIT — it never
front-runs the showcase bot's exit decision.

## The ladder (Scenario C)

Canonical source: `services/btc-conservative-agent/scenario_c_config.py`
(`TRAIL_LADDER_SCENARIO_C`, profile `SCENARIO_C_RUNNER_10_v4`).

Mirrored in TypeScript at `packages/utils/src/subscriber-exit.ts` →
`SCENARIO_C_LADDER`. Each tuple is `[peak_margin_pct_trigger, protected_margin_pct_floor]`:

| Trigger (peak margin %) | Protects (floor margin %) |
|---:|---:|
| 10 | 6 |
| 19 | 17 |
| 40 | 28 |
| 60 | 45 |
| 80 | 60 |
| 100 | 75 |
| 150 | 120 |

When peak unrealized margin crosses a trigger, the protective stop advances
so that ~`protected`% of peak margin is locked in. The rung solver is a pure
function — `solveScenarioCRung(peakMarginPct)` — unit-tested in
`packages/utils/src/__tests__/scenario-c-ladder.test.ts`.

## Margin % → price conversion

With leverage `L`, a margin floor of `F` % corresponds to a price distance of
`F / (100 * L)` from the entry price:

- LONG stop = `entry * (1 + F/(100*L))` (above entry)
- SHORT stop = `entry * (1 - F/(100*L))` (below entry)

At 100x leverage, 6% margin = 0.06% price move. Verified against the Python
source and the showcase bot's per-trade exit path.

## Kill switch

```
EXCHANGE_DYNAMIC_STOPS_ENABLED=true   # enable (per account, Railway env)
# unset / "false" / any other value → OFF (default, ships dark)
```

- Read per call (no redeploy needed to flip).
- When OFF: behavior is unchanged (Phase 2 exit convergence — wide disaster
  stop only, showcase EXIT authoritative).
- When ON: PROFIT_LOCK_TRAIL fires even in showcase-mirror-only + exit-
  convergence mode, advancing the stop through the ladder rungs.

## Hardening (safety properties)

1. **Never loosen a stop.** Before placing a new stop, the relay compares
   the new price vs the current live stop price. For SHORT, the new stop
   must be ≤ current (tighter). For LONG, must be ≥ current. If a new stop
   would loosen, it logs `SKIP_LOOSEN` and skips. This is the single most
   important safety property.

2. **Cancel-then-replace.** The trail block cancels the previous stop order
   before placing the new one, tracked via `runtime.currentStopOrderId`
   (per participant — see isolation below). On INITIAL placement, the cancel
   is skipped (nothing to remove).

3. **Idempotent on restart.** When the relay starts and sees an open
   position with a live protective stop, it reconstructs the current rung
   from (peak MFE in persisted meta, side, fill price) and emits a
   `RECONSTRUCTED` audit line. It does NOT place duplicate stops if one is
   already live at the correct level.

4. **Per-account isolation.** All state (peak MFE, current rung, current
   stop order id, consecutive failure count) is keyed by `participantId`,
   which is unique per `(userId, cycleId)`. No shared mutable state between
   hire sessions. Safe for the 10 friends onboarding next.

5. **Showcase EXIT always wins.** In the EXIT handler, BEFORE market-closing
   the position, the relay cancels BOTH the persisted `meta.stopOrderId` AND
   any tracked `currentStopOrderId`. The showcase bot's exit is authoritative.

6. **Audit log.** Every stop adjustment writes one JSON line to
   `logs/exchange-stop-manager.log` with:
   ```
   {
     "tag": "[SCENARIO_C]",
     "cycle": "<id>",
     "user": "<id>",
     "participant": "<id>",
     "side": "LONG|SHORT",
     "entry": <price>,
     "peak_margin_pct": <pct>,
     "prev_rung": <index|null>,
     "new_rung": <index|null>,
     "old_stop": <price|null>,
     "new_stop": <price|null>,
     "bitfinex_old_order_id": <id|null>,
     "bitfinex_new_order_id": <id|null>,
     "action": "INITIAL|REPLACE|SKIP_LOOSEN|SKIP_SAME|SKIP_CIRCUIT_OPEN|RECONSTRUCTED|CIRCUIT_OPEN",
     "error": "<msg, only on failure>",
     "ts": "<iso>"
   }
   ```
   The log is gitignored (runtime artifact). On Railway it lands in the
   service's working directory under `logs/`.

7. **Circuit breaker.** Track consecutive stop-replacement failures per
   participant. After `STOP_MANAGER_CIRCUIT_THRESHOLD` (3) failures in a
   row, emit a `CIRCUIT_OPEN` audit line, surface `stopManagerCircuitOpen`
   (map of `participantId → lastError`) and `exchangeDynamicStopsEnabled`
   on the instance dashboard (via `dashboardState`, refreshed every tick),
   and stop attempting replacements for that participant until the next
   FILLED event resets the counter.

## Dashboard surfacing

The instance `dashboardState` now includes two new keys (refreshed every
tick via `persistCapacityState`):

- `exchangeDynamicStopsEnabled: boolean` — current kill-switch state.
- `stopManagerCircuitOpen: Record<participantId, lastError>` — empty object
  when feature is off or no participant has tripped the threshold.

## Unit tests

`packages/utils/src/__tests__/scenario-c-ladder.test.ts` — 28 tests covering:

- Ladder values match Python canonical exactly.
- `solveScenarioCRung` boundary behavior (0%, 9.99%, 10%, 15%, 19%, 40%,
  60%, 80%, 100%, 150%, 200%, 1M%).
- `getProfitLockFloor` per peak.
- `computeProfitLockStopPrice` margin%→price conversion at 100x and 50x
  leverage, plus monotonicity (LONG strictly increases, SHORT strictly
  decreases as floor rises — i.e. never loosens).

Run: `npx tsx --test packages/utils/src/__tests__/scenario-c-ladder.test.ts`

## Rollout

1. Default OFF — verify relay still works exactly as before on next deploy.
2. To enable for ONE account: set `EXCHANGE_DYNAMIC_STOPS_ENABLED=true` on
   the Railway API service (or locally in `.env.local`).
3. Watch `logs/exchange-stop-manager.log` for INITIAL → REPLACE progression
   as profit grows.
4. Confirm `dashboardState.stopManagerCircuitOpen` stays `{}`.
5. To roll back: unset the env var (no redeploy needed — read per call).

## Files

- `packages/utils/src/subscriber-exit.ts` — `SCENARIO_C_LADDER`,
  `solveScenarioCRung`, legacy `SUBSCRIBER_TRAIL_LADDER` alias.
- `apps/api/src/trading-agents/signal-subscriber-execution.service.ts` —
  `exchangeDynamicStopsEnabled()`, the un-gated + hardened `PROFIT_LOCK_TRAIL`
  block, the audit log writer, the circuit breaker, the restart
  reconstruction, the FILLED reset, and the closeVirtualLot stop cancel.
- `packages/utils/src/__tests__/scenario-c-ladder.test.ts` — unit tests.
- `scripts/bitfinex-policy-lock.json` — SHA-256 of the two source files
  (regenerated via `npm run lock:bitfinex-policy`).
