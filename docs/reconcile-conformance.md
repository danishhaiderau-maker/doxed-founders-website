# Reconcile-and-Adopt Conformance

> Companion to [`reconcile-spec.json`](./reconcile-spec.json). Per-layer coverage of the orphan scenarios **S1–S8** from the design, mapped to phases.

## Layers

| Layer | Code | Runtime | Role |
|---|---|---|---|
| **Layer A** | `services/btc-conservative-agent/bot.py` (+ `bitfinex_live_executor.py`) | python `:7002` (canonical showcase) | Signal source. Phase 3 will add adoption hooks here. **Not edited in Phase 1.** |
| **Layer B** | `apps/api/src/trading-agents/signal-subscriber-execution.service.ts` | node Live Copy relay | Subscriber-side reconcile + (Phase 2) adopt. **Phase 1 + Phase 2 live here.** |

## Phases

| Phase | Scope | Status |
|---|---|---|
| **Phase 1** | Foundation + bleeding-stop (cancel orphans, surface foreign, heal cancel-by-exchange, conformance spec) | ✅ done by this task |
| **Phase 2** | Layer B adoption (RECONCILE_ADOPT_A_TO_B, RECONCILE_ADOPT_FILLED) | 🟡 TODO |
| **Phase 3** | Layer A adoption (RECONCILE_ADOPT_B_TO_A) — `bot.py` edits | 🟡 TODO |
| **Phase 4–5** | Cross-layer PnL attribution, success-fee reconciliation, full bidirectional adopt | 🟡 TODO |

## Scenario coverage (S1–S8)

| # | Scenario | Layer A | Layer B | Phase | Notes |
|---|---|---|---|---|---|
| **S1** | Orphan ledger lot — ledger open qty > exchange position qty (a real Bitfinex order left resting, freezing new Live Copy entries) | n/a | `closeOrphanLedgerLots` | ✅ **Phase 1** | Cancels `meta.bitfinexOrderId` + `meta.stopOrderId` with a `findOrder` fill-guard (if `filled > 0`, defers to fill reconcile). Emits `ORPHAN_LEDGER_RECONCILE` EXIT (with `cancelled_order_ids`) **and** a dedicated `ORPHAN_LEDGER_RECONCILE_CANCEL` audit event. All cancels try/catch + idempotent. |
| **S2** | Foreign / unmanaged resting order on the exchange (not in `managedOrderIds`) | n/a | `evaluateEntryEligibility` → `persistOrphanOrderIds` | ✅ **Phase 1** | **Surface-only**. Writes `{ id, amountRemaining, amountOrig, price, status, orderType }` into `TradingAgentInstance.dashboardState.orphanOrderIds` + `orphanOrderDetectedAt`. New entries stay blocked. **No auto-cancel** (Q1 default). Cleared once no foreign orders remain. |
| **S3** | Cancel-by-exchange — `PENDING_ENTRY` participant whose `bitfinexOrderId` is absent from `listActiveOrders` AND has no `FILLED` event | n/a | `reconcileCancelByExchange` (called from `reconcileFilledParticipants`) | ✅ **Phase 1** | Marks participant `CLOSED` at `pnl_usd = 0` (no fill = no PnL). Emits `RECONCILE_CANCEL_BY_EXCHANGE` EXIT event with `bitfinex_order_id` + `participant_id`. Guards: participant `updatedAt` older than 120s, AND absent from BOTH the active set AND a direct `findOrder` (defense against transient nonce gaps). |
| **S4** | Stuck `PENDING_ENTRY` with a `FILLED` event (heal to `OPEN`) | n/a | `healStuckPendingFill` | existing (unchanged) | Heals to `OPEN` with the real fill price. |
| **S5** | Ghost `OPEN` lot missing qty/direction | n/a | `reconcileGhostOpenLots` | existing (unchanged) | Records `GHOST_LOT_REPAIRED` EXIT. |
| **S6** | Immediate exchange flat (manual or stop close) | n/a | `reconcileImmediateExchangeFlat` | existing (unchanged) | Reconstructs PnL, records EXIT. |
| **S7** | Adopt a Layer A order into the Layer B ledger | 🟡 TODO | 🟡 TODO | 🟡 **Phase 2** | `RECONCILE_ADOPT_A_TO_B` event + ledger attribution. Bot.py crashed mid-place; NestJS continues managing the resting order. |
| **S8** | Adopt a Layer B order into the Layer A bot.py ledger | 🟡 TODO | n/a | 🟡 **Phase 3** | `RECONCILE_ADOPT_B_TO_A` audit log entry. Live Copy relay died; bot.py takes over. **Edits `bot.py` — separate task, do NOT do in Phase 1.** |

## Phase 1 deliverables (this task)

1. **Item 1** — `closeOrphanLedgerLots` cancels leftover orders on orphan exit (S1).
   - File: `apps/api/src/trading-agents/signal-subscriber-execution.service.ts`
   - Event: `ORPHAN_LEDGER_RECONCILE_CANCEL` (eventType stored = `EXIT`).
   - Money-path guard: `findOrder` before each cancel; skip if `filled > 0`.

2. **Item 2** — Foreign-order surfacing into `dashboardState.orphanOrderIds` (S2).
   - File: same.
   - Field: `TradingAgentInstance.dashboardState.orphanOrderIds` (JSON array; `dashboardState` is already JSON, no schema migration).
   - Surface-only. Auto-cancel is intentionally Phase 2.

3. **Item 3** — Cancel-by-exchange detection in `reconcileFilledParticipants` (S3).
   - File: same.
   - Event: `RECONCILE_CANCEL_BY_EXCHANGE` (eventType stored = `EXIT`).
   - Heals `PENDING_ENTRY` → `CLOSED` at `$0` PnL.

4. **Item 4** — Conformance spec (this doc + `reconcile-spec.json`).

## Money-path invariants (Phase 1)

- Every cancel is preceded by a `findOrder` state check; if `filled > 0` the order is **not** cancelled (defer to `reconcileFilledParticipants`).
- Every cancel is wrapped in try/catch — a 404/nonce error must not crash the reconcile loop.
- Every cancel emits an audit event with the cancelled oids.
- Cancel-by-exchange healing only fires for participants older than 120s (don't race order placement).
- Cancel-by-exchange healing requires the `bitfinexOrderId` to be absent from **both** `listActiveOrders` **and** a direct `findOrder`.
- Cancel-by-exchange healing requires **no** `FILLED` event in the participant's history (filled rows are healed with real PnL).

## Out of scope (Phase 2/3)

- Auto-cancel of foreign orders (S2 stays surface-only in Phase 1).
- Adoption of Layer A orders by Layer B (S7, `RECONCILE_ADOPT_A_TO_B`).
- Adoption of Layer B orders by Layer A (S8, `RECONCILE_ADOPT_B_TO_A`) — requires `bot.py` edits.
- Filled-order adoption + PnL attribution across layers (`RECONCILE_ADOPT_FILLED`).
- Success-fee reconciliation for adopted fills.

## Verify

```
# Typecheck (your files must be clean; pre-existing errors in other agents' files are NOT yours)
cd apps/api && npx tsc --noEmit

# Grep-confirm the Phase 1 events/fields
rg "ORPHAN_LEDGER_RECONCILE_CANCEL" apps/api/src/trading-agents/signal-subscriber-execution.service.ts
rg "RECONCILE_CANCEL_BY_EXCHANGE"   apps/api/src/trading-agents/signal-subscriber-execution.service.ts
rg "orphanOrderIds"                 apps/api/src/trading-agents/signal-subscriber-execution.service.ts

# Only your files should be touched
git diff --stat
```
