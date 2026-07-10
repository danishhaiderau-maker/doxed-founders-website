# Intent-Based Copy ("Intent Mirror") — File-Level Implementation Plan

**Status:** PLAN ONLY — no code has been written or modified.
**Author:** Investigation pass, 2026-07-10.
**Scope:** `doxedcryptofounder` monorepo only. Showcase bot (`services/btc-conservative-agent/bot.py`) + platform API relay (`apps/api/src/trading-agents/`).

> ⚠️ **REAL MONEY.** The showcase bot holds a real Bitfinex API key with a real balance. The hard invariant, restated:
>
> **Real money must only ever be placed on an account whose owner has EXPLICITLY started copy trading. The showcase must NEVER be force-armed LIVE just to make copying work. Each hire (copier) must Connect API + Start before any order flows to their Bitfinex.**

---

## 0. Workspace rule reminder (read before implementing)

`.cursor/rules/no-blunt-bot-sync.mdc` is `alwaysApply: true`. Summary for the implementer:

- **Do NOT run** `sync:btc-research-bot`, `sync-local-btc-bot.mjs`, or any `sync:*bot*` script.
- **Do NOT** mirror `bybit_bot.py` into this repo. Edit the showcase bot **in place** under `services/btc-conservative-agent/`.
- Safe scripts: `sync:production` / `sync:all`, `wire:home-bot`, `RECOVER-GLOBAL-STACK.cmd`, `start-home-bot.ps1`.
- The showcase bot on `:7002` / `https://bot.doxxedcrypto.digital` is **canonical**. The local lab `bybit_bot.py` on `:7800` is a **different architecture** that diverged. Blunt sync clobbers monorepo-only fixes.

This plan's bot-side change is a **single new fire-and-forget function + one call site** inside `bot.py`. It does not touch sync.

---

## 1. Current-state diagram (what happens today)

### 1.1 The signal → relay → hire data flow

```
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ SHOWCASE BOT  services/btc-conservative-agent/bot.py  (:7002 / bot.doxxed…) │
 │                                                                             │
 │  3-factor pipeline → AI (DeepSeek) approval → record_approve_outcome()      │
 │    · line ~6147: status="PENDING"  → _push_showcase_relay_event             │
 │                                       ("APPROVE_PENDING", trade_id)         │
 │    · line ~6161: status="EXECUTED" → _push_showcase_relay_event             │
 │                                       ("ORDER_PLACED", trade_id)            │
 │                                                                             │
 │  Trade IDs are lane-prefixed: allocate_lane_trade_id() (line ~10257)        │
 │    RESEARCH_LANE_CONTINUOUS → "cont-<12hex>"   ← ONLY mirrorable lane       │
 │    vc603-, szdc1-, slav1-, a160v2-, scan-      ← research/paper, blocked    │
 │                                                                             │
 │  execute_order(signal) (line ~15648):                                       │
 │    GUARD: if not live_armed AND strategy_mode != "RESEARCH": return False   │
 │           (line ~15658)  ← key: RESEARCH mode lets paper orders through     │
 │    → create_limit_order / execute_market_order                             │
 │    → _maybe_bitfinex_limit_entry()  (line ~20308)                           │
 │        GUARD: _bitfinex_live_active() AND _private_api_keys_ok()            │
 │        ← only fires if bitfinex_live_enabled flag set on the showcase       │
 │    → _relay_mirror("FILLED"/"ORDER_PLACED", …)  (line ~20288)               │
 │        → local_bitfinex_relay.safe_mirror()  (local sim only, NOT hires)    │
 └─────────────────────────────────────────────────────────────────────────────┘
                         │
                         │  (1) webhook POST  (if SHOWCASE_RELAY_WEBHOOK_URL set)
                         │      headers: X-Bot-Control-Secret: $BOT_CONTROL_SECRET
                         │      body: {event, trade_id, ts, …}
                         ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ PLATFORM API  apps/api  (Vercel/Railway)                                    │
 │                                                                             │
 │  POST /api/trading-agents/conservative-btc/showcase-relay-event             │
 │    trading-agents.controller.ts line ~152                                   │
 │    → ShowcaseRelayEventsService.ingest()  (showcase-relay-events.service.ts)│
 │       · assertAuthorized(X-Bot-Control-Secret)  — timing-safe compare       │
 │       · cycles.wakeFromShowcase()  → creates INTENT SignalCycle row         │
 │       · execution.wakeNow(event)   → kicks the relay tick                   │
 │       · persistRelayEvent()         → SignalCycleEvent row (audit)          │
 │                                                                             │
 │  SEPARATELY (the path that actually creates copyable intents):              │
 │  SignalCyclesService.pollBotForIntents()  (signal-cycles.service.ts ~220)   │
 │    · fetches full bot state via BotBridgeService                            │
 │    · extractBotApproveSnapshot(bot) → bot.last_approve_outcome              │
 │    · GATE: lao.status must be "EXECUTED" or "PENDING"  (line ~231)          │
 │    · GATE: isMirrorableLaneTradeId(intentTradeId)  ← F7 whitelist: cont-    │
 │    · buildIntentEnvelope() → creates SignalCycle INTENT row                 │
 └─────────────────────────────────────────────────────────────────────────────┘
                         │
                         │  (2) relay tick (every ~2s + webhook wake)
                         ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ RELAY / HIRE EXECUTOR  SignalSubscriberExecutionService                     │
 │ apps/api/src/trading-agents/signal-subscriber-execution.service.ts          │
 │                                                                             │
 │  tick()  (line ~603):                                                       │
 │    · query TradingAgentInstance where                                       │
 │        status in [ACTIVE, PAUSED]  AND  exchangeProvider != 'paper'         │
 │      ← ACTIVE = user clicked Start. PAUSED = still reconciled (exit-only).  │
 │    · skip if !simActive AND hire expired (expiresAt < now)                  │
 │    · processInstance(agentId, instance, simActive)                          │
 │        · creds = exchanges.getUserCredentials(userId, 'bitfinex')           │
 │            ← IntegrationCredential row (encrypted token)                    │
 │        · evaluateEntryEligibility()  — margin cap, slots, orphan orders     │
 │        · submitLimitOrder(creds, {direction, qty, price, leverage})         │
 │            (line ~2675)  ← REAL BITFINEX ORDER ON HIRE ACCOUNT              │
 │                                                                             │
 │  mirrorCatchup pass (line ~4937):                                           │
 │    · reads botState.positions (showcase book)                               │
 │    · GATE F7: isMirrorableLaneTradeId(tradeId) — cont- only (line ~4968)    │
 │    · GATE F6: isPaperLaneTradeId(tradeId) — belt-and-suspenders (line ~4976)│
 │    · then submitLimitOrder(creds, …)                                        │
 └─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Why the dashboard shows "active" while Bitfinex stays flat

This is the core diagnosis, confirmed by reading the code:

1. The showcase bot runs in **paper/research mode** by default: `state.strategy_mode == "RESEARCH"` and `state.live_armed == False` (defaults at `bot.py:5792,5794`). In this mode `execute_order()` still routes through `create_limit_order()` / `execute_market_order()` and **fills a paper position into the in-memory `open_positions` list** — but the `_maybe_bitfinex_limit_entry()` hook early-returns because `_bitfinex_live_active()` is false (no real Bitfinex order is placed on the *showcase* account).

2. The relay's `pollBotForIntents()` reads `bot.last_approve_outcome` and sees `status: "EXECUTED"` (the paper fill set it). It passes the F7 `cont-` whitelist. It creates an INTENT `SignalCycle` row. The dashboard's "active Continuous signal" pill reads from this row / from `botState.positions` — so it lights up green.

3. The relay tick then tries to mirror. **But** the hire's `processInstance()` path that actually places the hire's Bitfinex order keys off `botState.positions` containing a real, still-open showcase position it can chase. When the showcase is running pure paper and the real Bitfinex side is flat / the paper position has already rotated out of the bot's short `positions` ring, the mirror-catchup loop finds nothing to copy, and the entry path's `showcaseCopyEntryReady()` / `showcaseEntryAbandoned()` checks (lines ~1336, ~1362) drop or defer the copy. Net effect: **the intent exists, the dashboard says active, no order ever lands on the hire's Bitfinex.**

4. Root cause in one line: **the relay only mirrors *executed showcase fills*, and a paper-mode showcase either has no real fill to mirror or the fill lives only briefly in the paper book.** There is no path that says "an approved `cont-` *intent* is itself copyable, regardless of whether the showcase ever filled it."

### 1.3 The flags, precisely

Read these from `bot.py` `state` dict and `bot-state.mapper.ts`.

| Flag | Where set | What it gates | Paper vs Live |
|---|---|---|---|
| `strategy_mode` | default `"RESEARCH"` (`bot.py:5792`) | Routes sizing/edge logic; enables paper fills | RESEARCH = paper path active |
| `live_armed` | default `False` (`bot.py:5794`); toggled by dashboard button (`bot.py:22046`) | `execute_order()` guard at `bot.py:15658`: **if not live_armed AND strategy_mode != RESEARCH → skip**. So RESEARCH mode bypasses the live_armed check for paper fills. | False + RESEARCH = paper fills allowed |
| `bitfinex_live_enabled` | bot state; read by `bitfinex_live_executor.is_enabled()` | `_maybe_bitfinex_*()` hooks — gates whether the **showcase's own** Bitfinex account gets a real order | False = showcase places no real order |
| `EXECUTION_PAUSED` env | read at `bot.py:3395,24797`; set by `demo_mode.py` | Forces `execution_reason = "SIMULATION_ONLY"` sticky | True = hard paper lock |
| `VIRTUAL_CHASE` | `SIGNAL_STATUS_VIRTUAL_CHASE` (`bot.py:5760`) + chase ladder logic | Hides a signal from the exchange book while chasing price; relay's `showcaseCopyEntryReady()` defers on these statuses | Orthogonal — a chase state, not a paper/live flag |

**Combinations that matter:**
- `strategy_mode=RESEARCH` + `live_armed=False` + `bitfinex_live_enabled=False` → **pure paper**. Paper fills land in `open_positions`. *This is the current showcase state and the source of the dashboard illusion.*
- `strategy_mode=RESEARCH` + `live_armed=True` + `bitfinex_live_enabled=True` → showcase places real Bitfinex orders (`V2_GUARD` at `bot.py:10877` still lets V2 paper run alongside).
- The invariant this plan enforces: **the showcase stays in pure-paper; copying still works via intent mirror.**

---

## 2. Invariant enforcement (the most important section)

### 2.1 Existing guards already in code (do NOT remove any)

| # | Guard | Location | What it prevents |
|---|---|---|---|
| G1 | `execute_order()` live_armed/RESEARCH gate | `bot.py:15658` | Showcase places nothing outside RESEARCH/paper without arming |
| G2 | `_bitfinex_live_active()` + `_private_api_keys_ok()` | `bot.py:20298,20309,20340,20359` | Showcase's own Bitfinex account untouched unless flag set |
| G3 | `V2_GUARD` — V2 paper path never rides live | `bot.py:10877` | Paper lanes can't leak to live exchange |
| G4 | F7 lane whitelist `isMirrorableLaneTradeId()` | `packages/utils/src/trade-id-match.ts:109`; called at `signal-cycles.service.ts:242,346` and `signal-subscriber-execution.service.ts:4968` | Only `cont-` trade IDs ever reach the hire order path. Fail-closed for bare UUIDs. |
| G5 | F6 paper-lane blocklist `isPaperLaneTradeId()` | `trade-id-match.ts:77`; called at `signal-subscriber-execution.service.ts:4976` | Belt-and-suspenders: blocks `a160v2-`, `paper-`, `sim-`, `shadow-` even if F7 is bypassed |
| G6 | F5 lane-prefix isolation `lanePrefixesCompatible()` / `tradeIdsMatch()` | `trade-id-match.ts:31,38` | A `vc603-` cycle can never pair with a `cont-` showcase position (no cross-lane orphan risk) |
| G7 | Relay only processes `ACTIVE`/`PAUSED` instances | `signal-subscriber-execution.service.ts:617-625` | Hires that are `PENDING`/`ERROR` or paper never get orders |
| G8 | Hire-expiry skip | `signal-subscriber-execution.service.ts:634` | Expired rental → no live copy (sim still runs) |
| G9 | Credentials-missing early return | `signal-subscriber-execution.service.ts:753-759` | No creds → no order, clear `lastError` |
| G10 | F8 Start-button pre-validation | `trading-agent-instances.service.ts:364-395` | Start throws `BadRequestException` if Bitfinex key missing/rejected or `< $5` Derivatives balance — flips to ACTIVE only after validation |
| G11 | `evaluateEntryEligibility()` margin/slot/orphan gates | `signal-subscriber-execution.service.ts:2233` | Per-tick: available margin, max concurrent legs, foreign orders → blocks entry |
| G12 | `subscriberMaxMarginUsd` cap | `PlatformSettings.subscriberMaxMarginUsd` (default $20); `loadSubscriberMaxMarginUsd()` | Platform-enforced per-trade USD ceiling |
| G13 | `assertAuthorized()` timing-safe secret compare | `showcase-relay-events.service.ts:68` | Webhook rejects without `BOT_CONTROL_SECRET` |
| G14 | Lane-prefix isolation in trade_id allocation | `bot.py:10257 allocate_lane_trade_id()` | Continuous lane always emits `cont-<hex>`; no ambiguity |

### 2.2 The full chain — "Real money reaches a hire's Bitfinex only if…"

ALL of the following are true simultaneously:

1. The showcase bot approved a `cont-` signal (G14 prefix, G4 whitelist).
2. The webhook reached the platform API and passed the secret check (G13).
3. A `SignalCycle` INTENT row was created with a `cont-` trade_id (G4 at `signal-cycles.service.ts:242`).
4. The hire's `TradingAgentInstance.status === ACTIVE` (G7) — which requires:
   - The user hired (paid DDollar, connected API key → encrypted `IntegrationCredential`).
   - The user pressed **Start**, which ran F8 validation: real Bitfinex key that authenticates + ≥$5 Derivatives balance (G10).
   - The hire has not expired (G8).
5. The hire's Bitfinex credentials decrypt successfully (G9).
6. `evaluateEntryEligibility()` returns `canEnter: true` — sufficient margin, free slots, no foreign orders, direction not conflicting (G11, G12).
7. The trade_id passes F7 (G4) **and** F6 (G5) at the mirror-catchup site.
8. `submitLimitOrder(creds, …)` fires (`signal-subscriber-execution.service.ts:2675`).

**Nowhere in this chain does the showcase's `live_armed` state appear.** That is correct and intentional — the showcase being paper must not block copying. The hire's `ACTIVE` status is the consent gate, not the showcase's arm state.

### 2.3 New guards this plan adds

| # | New guard | Location | Purpose |
|---|---|---|---|
| N1 | HMAC-SHA256 signature on the webhook | bot side: new `emit_signal_webhook()`; API side: `ShowcaseRelayEventsService` | Replaces the bearer-secret-only auth with payload-level signing so a replay/forge can't inject a fake `cont-` intent. Keeps the existing `X-Bot-Control-Secret` as a secondary check (don't remove G13). |
| N2 | `intent_source: "paper" \| "live"` field in payload + cycle | bot side payload; `SignalCycle.intentEnvelope` | Makes the paper/live provenance explicit and auditable. The mirror path does NOT gate on this (paper intents ARE copyable — that's the feature), but it's logged for every fill. |
| N3 | `INTENT_MIRROR_KILL_SWITCH` env | relay: `signal-subscriber-execution.service.ts` entry path | Single env var that disables the new intent-mirror path (reverts to fill-only mirroring) without touching the existing paths. Ops panic button. |
| N4 | `INTENT_MIRROR_DRY_RUN` env | relay: wrap the new mirror entry | Logs the would-be order (full payload) instead of calling `submitLimitOrder`. For paper-mode integration tests. |
| N5 | Explicit "intent mirror" cycle tag | `SignalCycleEvent.eventType = "INTENT_MIRROR_ENTER"` | Audit trail distinguishing mirror-from-intent vs mirror-from-showcase-fill. |
| N6 | Re-affirm F7 at the new entry point | new mirror path calls `isMirrorableLaneTradeId()` before placing | The new path must not bypass the whitelist "because the webhook said so". |

---

## 3. Proposed architecture

Two parts. Part A is a small, additive change to the existing webhook. Part B is the new mirror decision in the relay. **Neither touches the showcase's live-arm path.**

### Part A — Webhook-push (signed, intent-aware)

**Goal:** when the showcase approves a `cont-` signal (paper OR live), fire a signed webhook to the relay within ~0.5–2s. The webhook already exists (`_push_showcase_relay_event`, `bot.py:6118`); we extend it.

**Payload schema (JSON), versioned:**

```json
{
  "schema": "dcf-showcase-intent-v1",
  "event": "APPROVE_PENDING",
  "trade_id": "cont-48d547bf2d12",
  "ts": "2026-07-10T13:28:00Z",
  "intent_source": "paper",
  "direction": "LONG",
  "signal_price": 63250.5,
  "margin_usdt": 20,
  "leverage": 100,
  "win_prob": 68,
  "edge_score": 1.3,
  "effective_threshold": 1.1,
  "research_lane": "CONTINUOUS",
  "pullback_pct": 0.001,
  "bot_version": "1.1.42"
}
```

- `intent_source`: `"paper"` when `not state.get("live_armed")` (the default), `"live"` otherwise. **Informational only — the relay copies either way.** This is N2.
- `event`: existing vocabulary `APPROVE_PENDING` / `ORDER_PLACED` / `POSITION_CLOSED` / `LIMIT_UPDATED` (unchanged, preserves `ShowcaseRelayEventType`).

**Signing scheme (N1):** HMAC-SHA256 over the canonical JSON body using `SHOWCASE_WEBHOOK_SECRET` (new env, separate from `BOT_CONTROL_SECRET`). Header: `X-Showcase-Signature: sha256=<hex>`. The existing `X-Bot-Control-Secret` header is kept (G13 stays). Verification is timing-safe.

**Retry policy:** fire-and-forget in a daemon thread (matching the existing `_post` pattern at `bot.py:6128`). On failure, log + bump `_relay_push_state` counters (existing). The platform's `pollBotForIntents()` (every ~2s) is the fallback — the webhook is a latency optimization, not the only path. No in-bot retry queue (the polling backstop makes it unnecessary and a retry queue risks duplicate intents, which the `(agentId, tradeId)` unique constraint already de-dupes anyway).

**Secret location:** `SHOWCASE_WEBHOOK_SECRET` env var on the showcase bot (Railway) and platform API (Vercel/Railway). Rotated via `admin-control/showcase-runtime.service.ts` alongside the existing `BOT_CONTROL_SECRET` plumbing (`showcase-runtime.service.ts:346,385`).

**Files:**

| File | Change |
|---|---|
| `services/btc-conservative-agent/bot.py` | NEW `emit_signal_webhook(event, signal, ai)` (~15 lines) that builds the v1 payload, signs it, and calls the existing `_post` pattern. Called from `record_approve_outcome()` at line ~6159 (the existing `_push_showcase_relay_event` site) — augment, don't replace. |
| `apps/api/src/trading-agents/showcase-relay-events.service.ts` | Add `verifySignature(rawBody, sigHeader)`; call before `ingest()` body parse. Persist `intent_source` into `intentEnvelope` via `minimalIntentEnvelope()` / a new field. |
| `apps/api/src/trading-agents/trading-agents.controller.ts` | The `@Post(':slug/showcase-relay-event')` handler (line ~152) needs the raw body for HMAC — use Nest's `@Req()` with raw-body middleware, or verify in a guard. |
| env | Add `SHOWCASE_WEBHOOK_SECRET` (bot + API). |

### Part B — Intent mirror path (the core new behavior)

**Goal:** the relay treats an approved `cont-` webhook as copyable for any ACTIVE + Bitfinex-connected + lane-whitelisted hire, **even if the showcase fill was virtual (paper)**.

Today the relay's entry path keys off the showcase's *executed fill* (`botState.positions` / `lao.status === 'EXECUTED'`). The new path keys off the **intent** — the `SignalCycle` INTENT row created from the webhook — and proceeds to `submitLimitOrder` if the hire is eligible, regardless of whether the showcase book still holds the position.

**Decision flow (new, inside `processInstance` or a sibling method):**

```
For each ACTIVE hire instance:
  For each SignalCycle in [INTENT, PENDING_ENTRY] for this agent:
    tid = cycle.tradeId
    if INTENT_MIRROR_KILL_SWITCH env set: continue          # N3
    if not isMirrorableLaneTradeId(tid): skip               # N4 — re-affirm F7
    if isPaperLaneTradeId(tid): skip                        # F6 retained
    eligibility = evaluateEntryEligibility(creds, managed, …)   # G11
    if not eligibility.canEnter: continue
    if INTENT_MIRROR_DRY_RUN env set:                       # N4
        log "[DRY-RUN] would place " + JSON(order)
        record SignalCycleEvent("INTENT_MIRROR_ENTER_DRY")
        continue
    orderId = submitLimitOrder(creds, …)                    # existing call
    record SignalCycleEvent("INTENT_MIRROR_ENTER")          # N5
```

This is almost exactly the existing entry path at `signal-subscriber-execution.service.ts:2600-2700`, minus the dependency on `showcaseCopyEntryReady()` / `botState.positions`. The cleanest implementation is a new private method `maybeEnterFromIntent(agentId, instance, creds, …)` called from `processInstance()` when the existing fill-based path finds nothing to do.

**Files:**

| File | Change |
|---|---|
| `apps/api/src/trading-agents/signal-subscriber-execution.service.ts` | NEW private method `maybeEnterFromIntent(...)`. Calls existing `evaluateEntryEligibility` + `submitLimitOrder`. Honors N3/N4/N5. Called from `processInstance()` after the existing fill-mirror pass. |
| `packages/utils/src/trade-id-match.ts` | No change — `isMirrorableLaneTradeId` already correct. Re-exported and re-used. |
| env | `INTENT_MIRROR_KILL_SWITCH`, `INTENT_MIRROR_DRY_RUN`. |
| Prisma | No new table. Add `intent_source` to `SignalCycle.intentEnvelope` JSON (already a `Json` column — no migration needed). Optionally add to `SignalCycleEvent.payload` JSON. |

---

## 4. Concrete file-level change list (implementer checklist)

Copy-paste checklist. **Order matters** — do the relay-side guards before enabling the bot-side push.

### Relay / API side (do first, deploy, then enable bot push)

- [ ] **`apps/api/src/trading-agents/showcase-relay-events.service.ts` :: `verifySignature()` — NEW method.** HMAC-SHA256 verify of raw body against `SHOWCASE_WEBHOOK_SECRET`. Timing-safe. Guard N1. Called from `ingest()` before any state mutation.
- [ ] **`apps/api/src/trading-agents/showcase-relay-events.service.ts` :: `ingest()` — MODIFY.** Add `intent_source` from body into `minimalIntentEnvelope()` / `ensureCycle()`. Persist into `SignalCycle.intentEnvelope`. (Guard N2.)
- [ ] **`apps/api/src/trading-agents/trading-agents.controller.ts` :: `@Post(':slug/showcase-relay-event')` (line ~152) — MODIFY.** Enable raw-body access for HMAC verify (Nest raw-body middleware or a dedicated guard). Do NOT remove the existing `X-Bot-Control-Secret` path (G13).
- [ ] **`apps/api/src/trading-agents/signal-subscriber-execution.service.ts` :: `maybeEnterFromIntent()` — NEW private method.** The Part B mirror path. Re-uses `evaluateEntryEligibility` (G11) and `submitLimitOrder` (line ~2675). Guards: N3 (kill switch), N4 (dry-run), N5 (audit event), N6 (re-call `isMirrorableLaneTradeId`).
- [ ] **`apps/api/src/trading-agents/signal-subscriber-execution.service.ts` :: `processInstance()` (line ~742) — MODIFY.** Call `maybeEnterFromIntent()` after the existing fill-based entry/catchup passes, when `entriesThisTick === 0` and the instance is `ACTIVE` (not sim).
- [ ] **env (API)** — add `SHOWCASE_WEBHOOK_SECRET`, `INTENT_MIRROR_KILL_SWITCH` (default unset = feature on), `INTENT_MIRROR_DRY_RUN` (default unset = real orders).

### Bot side (do after API deployed + secret set)

- [ ] **`services/btc-conservative-agent/bot.py` :: `emit_signal_webhook()` — NEW function (~15 lines).** Builds `dcf-showcase-intent-v1` payload from `signal` + `ai` + `state`. Sets `intent_source = "paper" if not state.get("live_armed") else "live"` (N2). HMAC-SHA256 signs the JSON with `SHOWCASE_WEBHOOK_SECRET`. Fire-and-forget daemon thread (mirror `_post` at line ~6128). **Does NOT gate on `live_armed`.** This is the explicit design decision: paper approvals are copyable.
- [ ] **`services/btc-conservative-agent/bot.py` :: `record_approve_outcome()` (line ~6147, specifically ~6159-6162) — MODIFY.** Where `_push_showcase_relay_event("APPROVE_PENDING", trade_id)` fires today, also call `emit_signal_webhook("APPROVE_PENDING", signal, ai)` — pass the full signal context so the v1 payload is populated. Keep the legacy call for backward compat during rollout.
- [ ] **env (bot)** — add `SHOWCASE_WEBHOOK_SECRET` (same value as API). Rotated via `admin-control/showcase-runtime.service.ts`.

### Prisma

- [ ] **No migration.** `SignalCycle.intentEnvelope` is `Json`; `intent_source` is a new key inside it. `SignalCycleEvent.payload` is `Json`; new `INTENT_MIRROR_ENTER*` event types are just strings. No schema change = no deploy risk.

### Documentation / config

- [ ] **`docs/INTENT-MIRROR-PLAN.md`** — this file (already the deliverable).
- [ ] Optional: append a note to `.cursor/rules/` or `docs/ANTI-ABUSE-STRATEGY.md` recording that the showcase is intentionally paper-only and the intent-mirror is the sanctioned copy path.

---

## 5. Test plan (verify WITHOUT risking real money)

**Layered so each layer is independently safe.**

### 5.1 Bot-side unit (no network, no orders)

- `emit_signal_webhook()` with a fixture `signal`/`ai` produces the exact v1 JSON; HMAC verifies against the same secret in a TypeScript test. Assert `intent_source === "paper"` when `live_armed=False`.
- Assert the function is a no-op when `SHOWCASE_WEBHOOK_SECRET` is unset (don't crash the bot on misconfig).

### 5.2 API-side unit

- `verifySignature()` rejects tampered body, missing header, wrong secret. Timing-safe (no early return).
- `isMirrorableLaneTradeId()` still rejects `vc603-`, `szdc1-`, bare UUIDs — regression test for G4/G6.

### 5.3 Paper-mode integration test (the important one)

1. Set `INTENT_MIRROR_DRY_RUN=1` on the API. (N4 — logs, no real order.)
2. Showcase in default paper mode (`live_armed=False`).
3. Wait for / force a `cont-` APPROVE.
4. Assert: webhook fires → API creates INTENT cycle → relay tick logs `[DRY-RUN] would place …` → `SignalCycleEvent` row `INTENT_MIRROR_ENTER_DRY` exists.
5. **No `submitLimitOrder` call. No Bitfinex API hit.** Safe to run against production DB.

### 5.4 Sim-mode relay test (real Bitfinex API, capped)

The relay already has a "sim" tier (`isCopyRelaySimActive`) that uses the real Bitfinex API but is capped at 1 order / $20 / 100x (`signal-subscriber-execution.service.ts:643-672`). Use this as the next layer:

1. Hire with a test Bitfinex account (small balance).
2. Start relay sim.
3. Disable `INTENT_MIRROR_DRY_RUN`, keep `INTENT_MIRROR_KILL_SWITCH` unset.
4. Wait for a `cont-` APPROVE.
5. Assert: exactly one real Bitfinex order lands on the test account, matching the intent direction/size. `INTENT_MIRROR_ENTER` event recorded.

### 5.5 Live hire, paper showcase (the production target state)

1. Real hire (paid DDollar, connected API, pressed Start — G10 validated).
2. Showcase still paper (`live_armed=False`).
3. `INTENT_MIRROR_KILL_SWITCH` unset, `INTENT_MIRROR_DRY_RUN` unset.
4. On `cont-` APPROVE: hire's Bitfinex account receives the copy order. Dashboard "active" pill now corresponds to a real position.
5. On showcase `POSITION_CLOSED` webhook: existing close-mirror path fires; hire's position closes.

### 5.6 Kill-switch test

- Set `INTENT_MIRROR_KILL_SWITCH=1`. Repeat 5.5. Assert: **no intent-mirror entry**; only the legacy fill-based path runs (which, in paper-showcase, finds nothing → hire stays flat). Confirms the panic button works.

### 5.7 Abuse tests (must NOT place money)

- Webhook with bad signature → 401, no cycle created (N1).
- Webhook with `vc603-` trade_id → INTENT created but `maybeEnterFromIntent` skips (N6 / G4).
- Webhook for a hire that is `PAUSED` → skipped (G7).
- Webhook for a hire with expired rental → skipped (G8).
- Webhook for a hire with no credentials → skipped (G9).
- Webhook for a hire with insufficient Derivatives margin → `evaluateEntryEligibility` returns `canEnter:false` (G11).

---

## 6. Explicit non-goals (do NOT do these)

- **Do NOT force-arm the showcase LIVE** (`live_armed=True`, `bitfinex_live_enabled=True`) to make copying work. The entire point of Part B is that paper approvals are copyable. Arming the showcase puts the showcase owner's real balance at risk and violates the invariant.
- **Do NOT run `sync:btc-research-bot`, `sync-local-btc-bot.mjs`, or any `sync:*bot*` script.** Workspace rule `no-blunt-bot-sync.mdc`. Edit `bot.py` in place.
- **Do NOT touch `C:/Users/user/Desktop/Final Bots/bybit_bot.py`.** Different bot, different architecture.
- **Do NOT remove or weaken any guard in §2.1 (G1–G14).** The new path *adds* guards (§2.3); it does not relax existing ones.
- **Do NOT change `isMirrorableLaneTradeId`'s allowlist** (`MIRRORABLE_LANE_PREFIXES = new Set(['cont'])`). Adding a lane is a separate, deliberately-hard decision.
- **Do NOT gate the new mirror path on `live_armed`.** That reintroduces the exact bug this feature fixes.
- **Do NOT place any trades, even paper, while investigating.** (This plan was produced read-only.)
- **Do NOT modify `prisma/schema.prisma`.** No migration needed; use existing JSON columns.
- **Do NOT add an in-bot retry queue for the webhook.** The ~2s polling backstop (`pollBotForIntents`) already de-dupes via the `(agentId, tradeId)` unique constraint. A retry queue adds duplicate pressure for no reliability gain.

---

## 7. Open questions for the user

Things I could not fully resolve from the code and do not want to guess on, given real money:

1. **Secret rotation boundary.** `SHOWCASE_WEBHOOK_SECRET` — do you want it distinct from the existing `BOT_CONTROL_SECRET`, or the same value reused? Distinct is safer (a leaked relay secret can't drive the showcase control API) but is one more var to rotate. The plan assumes **distinct**. Confirm.

2. **Should the intent-mirror path also fire for `PENDING_ENTRY` cycles, or only `INTENT`?** Today `PENDING_ENTRY` means a hire limit is already resting. Re-entering on a `PENDING_ENTRY` cycle risks duplicate orders. The plan's `maybeEnterFromIntent` targets `INTENT` only and leaves `PENDING_ENTRY` to the existing chase/catchup logic. Confirm this is the desired boundary.

3. **Margin sizing on intent-mirror entries.** The existing path uses `computeQty(marginUsd, leverage, limitPrice, MIN_QTY_BTC)` with `marginUsd` from the envelope. For paper-showcase intents, the envelope's `margin_usdt` comes from the showcase's `FIXED_MARGIN_USDT`. Do you want the hire's margin to (a) mirror the showcase's margin exactly, or (b) clamp to `subscriberMaxMarginUsd` ($20 today) regardless? The plan assumes **(b) — the platform cap always wins** (matches `evaluateEntryEligibility`). Confirm.

4. **Limit price source when the showcase is paper.** The existing fill-mirror path anchors the hire's limit to `botState` showcase limit (`resolveBotLimitPrice`, line ~2611). A paper showcase that never placed a real order may have a synthetic/paper limit. Do you want the intent-mirror to (a) use the paper limit from the webhook payload's `signal_price` + `pullback_pct`, or (b) use the hire's local mark at receipt time? The plan assumes **(a) signal_price ± pullback**, matching `buildIntentEnvelope`'s `offset_pct` logic. Confirm.

5. **Is the showcase actually running pure-paper in production right now?** The code defaults say yes (`strategy_mode=RESEARCH`, `live_armed=False`), but an admin may have armed it. The plan is safe either way (intent_source tags it), but I want to confirm the current production showcase state before you implement, so the test plan's "paper-mode integration test" reflects reality.

6. **Does the relay live entirely in this repo?** Yes — confirmed: `apps/api/src/trading-agents/signal-subscriber-execution.service.ts` is the hire-side executor and runs in the platform API (Vercel/Railway). There is no separate relay service elsewhere. The only out-of-repo piece is the showcase bot itself on Railway, which this repo deploys. So the plan is **fully scoped within this monorepo**.

---

## 8. TL;DR for the user

- **Current state:** showcase runs paper, fills paper positions, fires webhooks; relay only mirrors *executed showcase fills*, so when the showcase is paper-only the hire dashboard lights up "active" but no order lands on the hire's Bitfinex.
- **Key invariants already in code:** F7 `cont-` whitelist, F5 lane isolation, F8 Start validation, hire-must-be-ACTIVE, credentials-must-decrypt, margin/slot gates. **The showcase's `live_armed` is correctly absent from the hire-side consent chain.**
- **The fix:** one new bot function (`emit_signal_webhook`) that signs and pushes an intent-aware payload on every `cont-` APPROVE (paper or live), and one new relay method (`maybeEnterFromIntent`) that places a hire order from the INTENT cycle when the hire is ACTIVE + credentialed + eligible — with a kill switch, a dry-run mode, and re-affirmation of F7.
- **Plan status:** fully scoped within this repo. Six open questions in §7, none blocking the architecture — all are parameter confirmations.
- **Plan document:** `docs/INTENT-MIRROR-PLAN.md` (this file).
