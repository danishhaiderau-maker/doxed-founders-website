# Platform Request Audit

**Generated:** 7 July 2026  
**Branch audited:** `master` @ recovery after Cursor shutdown  
**Purpose:** Full ownership checklist — what shipped on `origin/master`, what this recovery push completes, what remains.

---

## Legend

| Status | Meaning |
|--------|---------|
| **DONE** | On `master` and deployable after push |
| **PARTIAL** | Core slice shipped; follow-up spec-only or Phase 2 |
| **MISSING** | Not started |

---

## Master checklist

| # | User request | Status | Commit(s) | Notes |
|---|--------------|--------|-----------|-------|
| 1 | **Demo Mode** — admin seed, reset, smoke, xlarge | **DONE** | `a6c8d861a`, `bc318fdbb` | `/admin/demo`, `DEMO_MODE_ENABLED` |
| 2 | **DDollar runtime** — two-ledger spendable + lifetime | **DONE** | `b2a357c50`, `1a9e11da2` | `DDOLLAR_RUNTIME_ENABLED`, treasury admin |
| 3 | **Phase 1.5 trust layer** — regulatory, LQ, progressive unlock | **DONE** | `d865ef47c` | `PHASE_15_TRUST_LAYER_ENABLED` |
| 4 | **Observatory** — admin control room | **DONE** | `200810de5` | `/admin/observatory`, `OBSERVATORY_ENABLED` |
| 5 | **AI runtime Phase 1** — gateway, pruning, share/wall | **DONE** | `4b0d3c820` | `AI_RUNTIME_ENABLED` |
| 6 | **Founder Brain AI OS docs** | **PARTIAL** | `7299e7f23`, `11792aaa6` | Spec on master; Jul-6 addendum optional |
| 7 | **Raise Room nav** — prominent hub entry | **DONE** | `0f45689b5`, `7d4492995` | Site nav + Founder Den bar |
| 8 | **Founder Den UI** — hide vendors, Brain modes | **PARTIAL** | `0f45689b5` | Mode picker WIP in `minimal-dev-workspace.tsx` |
| 9 | **GLM + DeepSeek admin** — two-model routing | **DONE (this push)** | recovery commit | `/admin/control` → AI Keys → Founder Brain Providers |
| 10 | **Raise Room PRODUCT redesign** | **PARTIAL (this push)** | recovery commit | Discovery hub shipped; DDollar escrow **MISSING** |
| 11 | **Platform readiness doc** | **DONE** | `3adf95e55` | `docs/PLATFORM-READINESS-PLAN.md` |
| 12 | **GLM z.ai setup runbook** | **DONE (this push)** | recovery commit | `docs/GLM-ZAI-PROVIDER-SETUP.md` |
| 13 | **Platform audit doc** | **DONE (this push)** | this file | |
| 14 | **Repo housekeeping / gitignore** | **DONE (this push)** | `chore: repo housekeeping and gitignore` | logs/, tmp/, lock files |
| 15 | **Digital twin / verification vision** | **DONE (doc)** | recovery commit | `docs/PLATFORM-VERIFICATION-VISION.md` — implementation Phase 2+ |
| 16 | **Vercel build fix** | **DONE** | recovery | `minimal-dev-workspace` onDone destructure fix |

**Bot sync:** No blunt sync performed (`config/bot-architecture.lock.json`).

---

## Git housekeeping (7 Jul 2026)

### Product files (committed in feature slices)

| Path | Category |
|------|----------|
| `apps/api/src/raise-room/*` | Raise Room API |
| `apps/api/src/founder-ai-runtime/founder-brain-providers.*` | GLM admin backend |
| `apps/api/src/founder-os/glm-config.ts` | z.ai endpoint config |
| `apps/web/src/components/raise-room/*` | Discovery hub UI |
| `apps/web/src/components/admin/admin-founder-brain-providers-panel.tsx` | Admin panel |
| `prisma/migrations/20260706220000_founder_brain_providers/` | DB column |
| `docs/GLM-ZAI-PROVIDER-SETUP.md`, audit/vision docs | Docs |

### Ignored locally (not deleted)

| Path | Action |
|------|--------|
| `logs/**`, `*.log` | gitignored |
| `tmp/`, `*.lock` runtime markers | gitignored |
| `services/btc-conservative-agent/*.jsonl`, research runtime | gitignored — **left on disk** |

---

## Railway API env checklist

Set on **Railway API service** (NestJS). Vercel web auto-deploys from `master`.

### Required production toggles

```bash
DATABASE_URL=postgresql://…
JWT_SECRET=…
NODE_ENV=production

# Platform slices (enable when GA)
DEMO_MODE_ENABLED=true
DEMO_SEED_SCALE=medium          # small|medium|large|xlarge
DDOLLAR_RUNTIME_ENABLED=true
PHASE_15_TRUST_LAYER_ENABLED=true
OBSERVATORY_ENABLED=true
AI_RUNTIME_ENABLED=true

# Founder Brain two-model routing
FOUNDER_BRAIN_TWO_MODEL_ROUTING=true
FOUNDER_BRAIN_FAST_PROVIDER=deepseek
FOUNDER_BRAIN_CODING_PROVIDER=glm
GLM_API_KEY=…
GLM_API_BASE=https://api.z.ai/api/coding/paas/v4
DEEPSEEK_API_KEY=sk-…
AI_RUNTIME_FAST_MODEL=deepseek-chat
AI_RUNTIME_CODE_MODEL=glm-5.2
AI_RUNTIME_REASONING_MODEL=deepseek-reasoner

# Safety defaults
RATE_LIMIT_FAIL_OPEN=false
TWITTER_VERIFIED_FREE_TOKEN_GATE=true
```

### Optional

```bash
REDIS_URL=…
AI_PROMPT_CACHE_TTL_SEC=3600
PARASITE_DAILY_TOKEN_CAP=25000
```

Full reference: `docs/ENV-VARS.md`, `docs/GLM-ZAI-PROVIDER-SETUP.md`, `docs/DEMO-MODE-AND-VERIFICATION.md`.

### Deploy flow

| Target | Trigger | Notes |
|--------|---------|-------|
| **Vercel** (web) | Push `master` | `apps/web` |
| **Railway** (API) | Push `master` | Set env vars; run `prisma migrate deploy` |
| **Neon** | API boot / CI | Includes `20260706220000_founder_brain_providers` |

---

## MISSING — Raise Room Phase 2

1. `TokenLaunch`, `LaunchInterestCommit` schema
2. DDollar escrow commit/cancel API
3. Points ledger types for whitelist commits
4. Tier picker + DDollar heatmap UI
5. Escrow legal copy
6. Merkle / on-chain claim workstream

---

## Verification commands (post-deploy)

```bash
GET  /api/admin/demo/status              # DEMO_MODE_ENABLED
POST /api/admin/demo/smoke
GET  /api/admin/observatory              # OBSERVATORY_ENABLED
GET  /api/admin/ddollar/treasury         # DDOLLAR_RUNTIME_ENABLED
GET  /api/admin-control/founder-brain-providers
POST /api/admin-control/founder-brain-providers/test
GET  /api/raise-room/dashboard
GET  /api/raise-room/projects?filter=trending
```

Web: `/admin/demo`, `/admin/observatory`, `/admin/control`, `/raise-room`, Founder Den.

---

## See also

- [PLATFORM-READINESS-PLAN.md](./PLATFORM-READINESS-PLAN.md)
- [RAISE-ROOM-P0-INSPIRED-PLAN.md](./RAISE-ROOM-P0-INSPIRED-PLAN.md)
- [GLM-ZAI-PROVIDER-SETUP.md](./GLM-ZAI-PROVIDER-SETUP.md)
- [PLATFORM-VERIFICATION-VISION.md](./PLATFORM-VERIFICATION-VISION.md)
