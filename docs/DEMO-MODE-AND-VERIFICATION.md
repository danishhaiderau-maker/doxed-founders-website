# Demo Mode and Verification

Admin-only **Demo Mode** seeds a synthetic Founder OS ecosystem so you can test
the platform end-to-end without manually fixing bugs one at a time. API-level
**smoke checks** validate golden journeys without Playwright (MVP).

## Quick start

### 1. Enable on Railway (API)

```bash
DEMO_MODE_ENABLED=true
DEMO_SEED_SCALE=medium   # optional: small | medium | large | xlarge
```

Redeploy the API service after setting vars. Demo seed/reset/smoke **fail closed**
when `DEMO_MODE_ENABLED` is not exactly `true`.

### 2. Open admin UI

- **Web:** [https://doxxedcrypto.digital/admin/demo](https://doxxedcrypto.digital/admin/demo)
- **From:** Admin Control → Platform & Treasury → Demo Mode

Requires **ADMIN** role (same as `/admin/control`).

### 3. Seed → Smoke → Browse

1. Click **Generate Demo Ecosystem**
2. Click **Run Smoke Checks** (target: 25+ passed on medium scale)
3. Visit sample links: `/projects/demo-payflow`, `/raise-room`, Founder OS dashboards

### 4. Reset when done

**Reset Demo Data** removes only records tagged with:

- User emails `*@doxxed.demo`
- Project/founder slugs `demo-*`
- Platform handles `demo_*`

Real users and production projects are never touched.

## API routes

All routes require `Authorization: Bearer <admin JWT>` and `DEMO_MODE_ENABLED=true`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/admin/demo/status` | Counts, scale, sample slugs |
| `POST` | `/api/admin/demo/seed` | Idempotent upsert of demo ecosystem |
| `POST` | `/api/admin/demo/reset` | Delete demo-tagged records only |
| `POST` | `/api/admin/demo/smoke` | Run golden-journey API checks |
| `GET` | `/api/admin/demo/smoke` | Same as POST (convenience) |

## What gets seeded

| Entity | Count (medium) | Notes |
|--------|----------------|-------|
| Users | 35 | Mix of founders, builders, scouts |
| Projects | 12 | Lifecycle IDEA → LIVE_TRADING |
| Founders | 10 | Linked to demo users |
| SimulatedRaise | ~8 active | Paper conviction / Raise Room heatmap |
| RaiseAllocation | ~6 per raise | Scout paper commits |
| PointLedger | per user | Two-ledger demo pattern (see DDOLLAR spec) |
| ProjectTrustReport | per project | Trust validation signals |
| FounderEvent | raise + build | Activity feed source data |

Demo names are prefixed `[Demo]` / `demo_` — clearly fake.

| `xlarge` | 2,500 | 150 | 500 |

Also seeds marketplace purchases, AI usage logs, leaderboards, notifications,
feed comments, paper trades, and graduation events (medium+ scales).

## Smoke checks

Golden journeys verified at API layer (25+ checks on medium scale):

1. Demo mode enabled
2. Demo users exist (`@doxxed.demo`)
3. `GET /projects` includes demo slugs
4. Raise Room demand heatmap has demo rows
5. Project detail loads for a demo slug
6. DDollar spendable + lifetime on real column
7. Point ledger entries exist
8. Trust validation signals on demo projects
9. Active simulated raises
10. Founder OS integrations endpoint healthy
11. Founder events for activity feed
12. Lifecycle stage coverage across demo projects
13. `ddollar_spend_lifetime_unchanged`
14. `marketplace_ledger_balanced`
15. `treasury_audit_trail`
16. AI usage history seeded
17. Leaderboard entries populated
18. Demo notifications seeded
19. Graduation events seeded
20. Feed comments + paper trades seeded
21. `golden_ddollar_business_journey` (Slice 7)

Example response:

```json
{
  "passed": 12,
  "failed": 0,
  "total": 12,
  "ok": true,
  "ranAt": "2026-07-06T08:55:00.000Z",
  "checks": [
    { "name": "projects_list_has_demo", "passed": true, "detail": "12 demo projects in GET /projects", "durationMs": 42 },
    { "name": "raise_room_heatmap", "passed": true, "detail": "8 demo rows in demand heatmap (8 total active raises)", "durationMs": 18 }
  ]
}
```

## End-to-End Demo Harness

One command that exercises every pillar of the platform (Founder OS, Raise
Room, DDollar, Trust Center, founder journey, BTC conservative bot,
analyzer, genome, relay, AI verdicts, and optional stress) and emits a
unified readiness scorecard to `logs/demo/demo-report.{json,md}`.

### Run it

```powershell
# Required: shared secret so the orchestrator can call the internal route.
$env:DEMO_HARNESS_TOKEN = "some-secret"
# Required only if you want relay cassette replay (recommended).
$env:BOT_CONTROL_SECRET  = "your-bot-control-secret"

# Default: replay mode (no paid API calls), small seed, no stress.
npm run demo:full

# Include the stress phase (peak RPS, p95 latency, DDollar invariant).
npm run demo:stress

# Re-record cassettes from the real DeepSeek + Bitfinex + relay (DEMO_CAPTURE=1).
# Only run this from a trusted shell with the real API keys set.
npm run demo:capture
```

Windows one-click launchers exist:

- `scripts\run-demo.cmd` — forwards all args to `node scripts/demo-harness.mjs`.
- `scripts\run-demo.ps1` — PowerShell equivalent (`.\scripts\run-demo.ps1 --stress`).

### What the orchestrator does

1. Pre-flight: confirms API (`/api/health`) reachable; aborts if
   `LIVE_TRADING_ENABLED=true` or if `config/bot-architecture.lock.json`
   has `allowBluntSync=true` (the safety contract).
2. `POST /api/admin/demo/reset` + `/seed` — clean synthetic ecosystem.
3. Spawns `python demo_mode.py` under
   `services/btc-conservative-agent/` (sim mode, :7002) — unless
   `--skip-bot`.
4. Waits for `GET /api/ping` on the bot to go green.
5. Replays the four relay cassettes (`APPROVE_PENDING` →
   `ORDER_PLACED` → `LIMIT_UPDATED` → `POSITION_CLOSED`) into
   `/api/trading-agents/conservative-btc/showcase-relay-event`.
6. Triggers the analyzer (best-effort, 90s timeout).
7. `POST /api/admin/demo/harness/internal` with the shared secret — runs
   the unified probe and returns the scorecard. The orchestrator writes
   `logs/demo/demo-report.{json,md}`.

### Cassette model

Replay by default. The Python bot and the NestJS API share
`cassettes/<bucket>/<key>.json`. See [cassettes/README.md](../cassettes/README.md)
for the layout and the stable DeepSeek hash function (mirrored in
`apps/api/src/demo/cassette-store.ts` and
`services/btc-conservative-agent/demo_mode.py`).

### Scorecard shape

The unified scorecard is `ReadinessScorecard`
([readiness-scorecard.types.ts](../apps/api/src/demo/readiness-scorecard.types.ts)):

| Pillar | Weight | Sample checks |
|--------|-------:|---------------|
| platform | 0.20 | the 25+ existing smoke checks |
| bot | 0.18 | ping, state parseable, paper orders, lane-size patch, LAB shadow tiles, tunnel |
| relay | 0.15 | APPROVE_PENDING / ORDER_PLACED / POSITION_CLOSED received, cycle completes, copy-sim consistent |
| analyzer | 0.10 | `report_manifest.json` + 3 new v1 reports parse |
| ai | 0.10 | verdicts emitted, median latency within budget |
| genome | 0.07 | decision/environment/market JSONL rows + `research.db` |
| founder | 0.10 | message dispatch + golden DDollar journey |
| stress | 0.10 | peak RPS, p95 latency, error rate, relay burst, DDollar two-ledger invariant, RSS |

Switches reported: `liveTrading` (must be OFF), `fundingSim`,
`demoMode`, `cassetteMode`, `laneSizePatch`, `labShadowTiles`,
`executionPaused`. Synthetic counts reported: `fakeUsers`,
`fakeProjects`, `fakeFounders`, `fakeRaises`, `fakeAllocations`,
`fakeTrades`, `fakeAiCalls`, `fakeMessages`, `fakeNotifications`.

Verdict thresholds: **PASS** ≥ 80, **DEGRADED** ≥ 60, else **FAIL**.

### API routes (all under `/api/admin/demo`)

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| `POST` | `/harness/internal` | `@Public()` + `DEMO_HARNESS_TOKEN` secret | orchestrator entry point |
| `POST` | `/harness` | admin JWT + `DemoModeGuard` | run harness from admin UI (forwards `body.skipStress`) |
| `POST` | `/smoke/full` | admin JWT + `DemoModeGuard` | extended smoke, stress skipped |
| `GET`  | `/smoke/full` | admin JWT + `DemoModeGuard` | same, GET convenience |
| `POST` | `/stress` | admin JWT + `DemoModeGuard` | standalone stress run |
| `GET`  | `/harness/quick` | admin JWT | bot ping + last scorecard |

## Internal tools roadmap (next sprints)

From platform verification framework — **not built in MVP**:

| Route | Purpose | Status |
|-------|---------|--------|
| `/admin/demo` | Seed, reset, smoke | **Shipped (MVP)** |
| `/testing` | Test run dashboard | Planned |
| `/simulator` | Time Machine / market sim | Planned |
| `/verification` | Full golden journey suite (Playwright) | Planned |
| `/health` | Deep service health matrix | Partial (`/api/health` exists) |

### Time Machine (planned)

Replay historical market states and conviction flows for regression testing.

### Simulators (planned)

- Raise Room conviction simulator
- DDollar earn/spend simulator
- Founder OS builder session simulator

### Golden journeys (planned — Playwright)

Browser E2E for: signup → Founder OS → build log → Raise Room allocate →
Trust Center vote → DDollar spend on AI.

## Safety

- Never seed in production unless `DEMO_MODE_ENABLED=true` (explicit opt-in)
- Demo emails use `@doxxed.demo` only — never real domains
- Reset scoped by email domain + slug prefix — no blanket deletes
- Non-admin users receive `403 Forbidden`

## Related docs

- `docs/DDOLLAR-POC-TWO-LEDGER-SPEC.md` — spendable vs lifetime contribution
- `docs/ENV-VARS.md` — `DEMO_MODE_ENABLED`, `DEMO_SEED_SCALE`
