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
