# Demo Mode — Current State (v12)

> Snapshot taken 2026-07-17 after the v12-type-b-overhaul merge.
> Demo Mode itself was not modified in v12; this doc records the validated
> state so the operator can run the harness without re-investigation.

## What Demo Mode covers (19 pillars)

All 19 scorecard pillars are wired and exercised by
`scripts/demo-harness.mjs` → `POST /api/admin/demo/harness/internal`:

| Pillar | What it probes | Last score |
|--------|----------------|-----------:|
| platform | 26 smoke checks (founder OS, raise room, DDollar, trust) | 96/100 |
| bot | :7002 ping, state parseable, paper orders, lane-size patch, LAB tiles | 88/100 |
| analyzer | `report_manifest.json` + v1 reports | 50/100 |
| genome | decision/environment/market JSONL + research.db | 100/100 |
| relay | APPROVE_PENDING / ORDER_PLACED / POSITION_CLOSED cycle | 20/100 |
| ai | DeepSeek verdicts (cassette-replayed) | 100/100 |
| founder | message dispatch + golden DDollar journey | 100/100 |
| stress | peak RPS, p95 latency, DDollar two-ledger invariant | 100/100 |
| aiProxy | AI proxy routing | 100/100 |
| routingEngine | routing cache + dispatch | 100/100 |
| memoryEngine | founder-os memory service | 100/100 |
| learningEngine | learning loop | 100/100 |
| doxxing | doxxing/identity verification | 100/100 |
| ideaValidator | idea validator flow | 100/100 |
| lam | LAM (Ledger-Aware Messaging) | 100/100 |
| deploymentModes | deployment mode matrix | 100/100 |
| raiseRoom | raise-room heatmap + allocation | 100/100 |
| debugSquasher | debug-squasher pipeline | 100/100 |
| founderEconomics | treasury + multisig constraints | 100/100 |
| intentEngine | intent mirror + dispatch | 100/100 |

**Last full run**: 2026-07-16 01:11 UTC — overall **PASS, 89/100**, 74/81 checks.

## v12 compatibility

v12 changes are all additive or backward-compatible from Demo Mode's
viewpoint:

| v12 change | Demo Mode impact |
|------------|------------------|
| `execution_mode_for_lane` resolver | Bot-side only; demo harness sees the same `/api/state` shape |
| `type_b_trade_reconciliation` field on `/api/state` | New field, no existing check breaks |
| Tile 2 frozen policy `sr_micro_static_long_adx40_no_london_v1` | Bot-side; relay cassettes unaffected |
| `RESEARCH_STACK_VERSION` v11.8 → v12 | Cosmetic; harness does not pin on version string |
| Bitfinex cancel + EXIT_ONLY transition safety | Bot-side; relay events unchanged |
| SHORT disabled on Tile 2 | Bot-side; reduces relay event surface |

No harness or smoke-check code changes required for v12.

## How to run the full harness (operator gate)

The harness is invasive: it kills the production bot and restarts it in
`demo_mode.py` mode (`EXECUTION_PAUSED=True`) so paper orders flow
deterministically. Do not run mid-trading-session.

Prerequisites:
1. Neon DB reachable (vault `.env.neon`)
2. Vault `home-bot.env` loaded for the bot side
3. Ports :4000 (API), :7002 (bot), :7810 (relay bridge if used) free
4. `DEMO_HARNESS_TOKEN` set to a known value on both sides

PowerShell one-liner using the existing helpers:

```powershell
# Terminal 1 — API server (Neon DB required)
& .\logs\demo\_start-api.ps1

# Terminal 2 — orchestrator (after API is up on :4000)
$env:DEMO_MODE_ENABLED = 'true'
$env:DEMO_HARNESS_TOKEN = 'demo-local-token'
$env:DEMO_API_URL = 'http://127.0.0.1:4000'
$env:DEMO_BOT_URL = 'http://127.0.0.1:7002'
$env:DEMO_SKIP_TUNNEL = '1'
$env:DEMO_SEED_SCALE = 'small'   # small|medium|large|xlarge
npm run demo:full
```

Report is written to `logs/demo/demo-report.{json,md}`.

To include the stress phase (peak RPS, p95 latency, DDollar invariant):

```powershell
npm run demo:stress
```

## Known transient failures (last run, 2026-07-16)

These were runtime/transient, not code gaps:

| Pillar | Check | Cause |
|--------|-------|-------|
| platform | `graduation_events_seeded` | demo seed produced 2 events, harness expected more |
| bot | `bot_state_parseable` | bot was not in `EXECUTION_PAUSED=True` when harness attached (home bot was running in live-pending mode) |
| analyzer | `analyzer_new_v1_reports` | 3 v1 report files missing from the analyzer output |
| relay | `relay_approve_pending_received` / `_order_placed_received` / `_position_closed_received` / `_cycle_completes` | relay round-trip did not complete; relay bridge (:7810) was likely not running |

All seven are expected to clear once the harness is run with the relay
bridge up and the bot restarted in `demo_mode.py`.

## Genuinely missing (deferred, post-MVP)

Per `docs/DEMO-MODE-AND-VERIFICATION.md` "Internal tools roadmap":

| Route | Status |
|-------|--------|
| `/testing` (test run dashboard) | Planned |
| `/simulator` (Time Machine / market sim) | Planned |
| `/verification` (Playwright E2E) | Planned |
| `/health` (deep service health matrix) | Partial (`/api/health` exists) |

These are larger platform features, not Demo Mode configuration gaps. They
are tracked in `docs/PLATFORM-READINESS-PLAN.md`.
