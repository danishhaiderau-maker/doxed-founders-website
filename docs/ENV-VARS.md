# Environment variables — runtime behavior switches

This doc covers env vars that change runtime behavior (not secrets). For secret
storage see `SECRETS_STORAGE.md`. For deploy infra see `railway-deploy.md` /
`vercel-deploy.md`.

## `EXCHANGE_DYNAMIC_STOPS_ENABLED`

**Default:** `false` (ships dark — feature OFF)
**Added:** 2026-07-08 (Option A — see `docs/EXCHANGE_DYNAMIC_STOPS.md`)

When `true`, the live-copy relay keeps the protective stop on Bitfinex synced
to the current Scenario C ladder rung, even in showcase-mirror-only + exit-
convergence mode. The showcase bot remains the decision maker for EXIT; this
only manages the protective stop between entry and that EXIT so unrealized
profit is actually protected on the exchange.

### Values

| Value | Behavior |
|---|---|
| unset / `false` / anything else (default) | OFF — Phase 2 exit convergence (wide disaster stop only, showcase EXIT authoritative). |
| `true` | ON — relay advances the protective stop through `10→6, 19→17, 40→28, 60→45, 80→60, 100→75, 150→120` as unrealized margin grows. Ships with never-loosen, cancel-then-replace, per-account isolation, restart idempotency, audit log to `logs/exchange-stop-manager.log`, and a 3-strike circuit breaker surfaced on `dashboardState.stopManagerCircuitOpen`. |

Read per call — flip via Railway env without a redeploy. Full details:
`docs/EXCHANGE_DYNAMIC_STOPS.md`.

## `RATE_LIMIT_FAIL_OPEN`

**Default:** `false` (fail closed)
**Added:** 2026-07-03 (API abuse hotfix — see `docs/API-ABUSE-AUDIT.md`)

Controls what happens when the rate limiter (`RateLimiterService.checkLimit`)
or the balance check (`PointsService.spend`) cannot reach the database to
verify a user's quota / balance.

### Values

| Value | Behavior |
|---|---|
| `false` (default) | **Fail closed.** If the DB is unreachable (Neon asleep, P1001 connection refused, P1008 timeout, or any error matching `/connection\|timeout\|unreachable/i`), the request is rejected with `503 Rate limiter unavailable — please try again`. The AI call never proceeds. |
| `true` | **Fail open (emergency debug only).** Reverts to the pre-hotfix behavior: if the DB is down, the request continues without rate limiting. Use ONLY for emergency debugging — never leave this on in production. |

### Why this exists

On 2026-07-02 the platform observed accounts burning through the platform
DeepSeek API key unusually fast despite rate limits being coded. Root cause:
Neon free-tier CU-hours were exhausted, the DB went to sleep, and the rate
limiter / balance check — both DB-backed — silently failed open. Every AI
endpoint became unlimited until the DB came back.

Fail-closed is the correct production default: a degraded AI experience
(503s) is better than an unbounded DeepSeek bill. The `=true` escape hatch
exists for one situation only — a deliberate decision to keep serving AI
traffic during a known DB outage where you'd rather eat the cost than break
the product.

### How to verify it's working

After deploy, on a staging environment (NOT prod):

1. Stop your local Postgres / put Neon to sleep
2. Hit any AI endpoint (e.g. `POST /copilot/ask`) with a valid auth token
3. Expected: `503 {"error": "Rate limiter unavailable — please try again"}`
4. Set `RATE_LIMIT_FAIL_OPEN=true`, redeploy, repeat the request
5. Expected: request proceeds (and the AI call fires) — this confirms the
   escape hatch works

In production, watch Vercel/Railway logs for the string
`Rate limiter unavailable` — a spike means Neon is sick again.

### Where it's read

- `apps/api/src/rate-limit/rate-limit.service.ts` — `checkLimit()` catch block
- `apps/api/src/points/points.service.ts` — `spend()` catch block

Both use `process.env.RATE_LIMIT_FAIL_OPEN === 'true'` to decide. Anything
else (unset, `false`, `0`, etc.) fails closed.

## `TWITTER_VERIFIED_FREE_TOKEN_GATE`

**Default:** `true` (gate enabled)
**Added:** 2026-07-03 (API abuse hotfix — patch 5)

When `true`, free token grants (the 30M-token platform promo, GLM 5.2 free
90 days, DDollar signup bonus) require the requesting user to have a
verified Twitter account linked. Raises the cost of mass-creating abuse
accounts — verified Twitter handles are harder to spin up than email
addresses.

Set to `false` to disable the gate for testing or for a temporary promotion
where you want to allow non-Twitter-verified users to claim free credits.

### Where it's read

- `apps/api/src/founder-os/founder-promo.service.ts` — promo eligibility
  check
- Any signup-bonus / DDollar-credit grant path that calls into the promo
  service

### Schema dependency

The gate reads `user.twitterVerified` (boolean). If the `User` model in
`prisma/schema.prisma` does not yet have this field, the patch falls back to
deriving eligibility from `user.twitterHandle` being non-null + non-empty.
To enable the proper boolean check, run on the deployed environment:

```bash
npx prisma migrate deploy
```

(after the schema change is merged to main). Until the migration runs, the
gate uses the fallback derivation.

## `PARASITE_DAILY_TOKEN_CAP`

**Default:** `25000` (tokens/day)
**Added:** 2026-07-03 (two-tier builder protection)

Per-day token cap for parasite-tier accounts (the default tier for new
signups without verification). When a parasite-tier user's platform-promo +
platform-brain token usage in the last 24h reaches this cap, the next AI
call is rejected with `429 Daily parasite-tier token cap reached`. Enforced
both in `FounderPromoService.resolvePromoApiKey` (before the LLM call) and
in `PointsService.spend` (for AI-gated spends like the wall summarizer).

## `BUILDER_DAILY_TOKEN_CAP`

**Default:** `500000` (tokens/day)
**Added:** 2026-07-03 (two-tier builder protection)

Per-day token cap for verified-builder-tier accounts (xVerified + GitHub or
Cursor connected + ≥1 commit in last 14d, score ≥ `BUILDER_SCORE_THRESHOLD`).
20× the parasite cap. When exceeded, the AI call is rejected with
`429 Daily builder token cap reached. Connect your own API key to continue.`

## `PROMO_POOL_PRESERVATION_PCT`

**Default:** `0.30` (30%)
**Added:** 2026-07-03 (two-tier builder protection)

When the global promo pool remaining drops below this fraction of the cap
(default 30M tokens), parasite-tier accounts are cut off entirely with
`429 Promo pool reserved for verified builders.` Verified builders keep full
access — the remaining pool is reserved for them. Set to `0` to disable
pool preservation (not recommended).

## `BUILDER_SCORE_THRESHOLD`

**Default:** `50`
**Added:** 2026-07-03 (two-tier builder protection)

Composite builder score ≥ this value → `VERIFIED_BUILDER` tier; below →
`PARASITE`. Score formula: +30 xVerified, +25 GitHub connected, +25 Cursor
connected, +1 per commit/PR in last 14d (cap +20), +10 account age > 7d,
−50 abuse flag (rate-limited 10+ times in 24h OR balance < 0 OR >100 AI
calls in 1h). Lower the threshold to be more permissive; raise it to be
stricter.

## `BUILDER_SCORE_REFRESH_TTL_MS`

**Default:** `3600000` (1 hour)
**Added:** 2026-07-03 (two-tier builder protection)

How stale a cached `User.builderScore` / `User.builderTier` can be before
`BuilderScoreService.getTier` recomputes it. Lower = fresher but more DB
load; higher = cheaper but slower to reflect tier changes (e.g. a user
connecting GitHub won't upgrade until the TTL elapses or
`refreshUserScore` is called explicitly).

## `AI_RUNTIME_ENABLED`

**Default:** unset / `false` (runtime off)
**Added:** 2026-07-06 (Founder AI Runtime Phase 0)

When `true`, Founder Copilot, share paraphrase, and wall summarizer check
the prompt hash cache in `FounderAiRuntimeService` before calling LLM
providers. Cache hits skip provider calls entirely. Context pruning and
output token caps apply on wired sections. When unset or `false`, cache
and pruning are off; share/wall still use the runtime gateway with no-op cache.

### Where it's read

- `apps/api/src/founder-ai-runtime/founder-ai-runtime.service.ts` — `isEnabled()`
- `apps/api/src/builder/builder.service.ts` — `tryCopilotChatCompletion` pilot path
- `apps/api/src/share/share.service.ts` — paraphrase via runtime gateway
- `apps/api/src/wall/wall.service.ts` — summarizer via runtime gateway

## `AI_RUNTIME_CONTEXT_PRUNING`

**Default:** enabled when runtime on (set `false` to disable)
**Added:** 2026-07-06

Trim oversized system/user prompts before provider calls.

## `AI_RUNTIME_MAX_SYSTEM_CHARS`

**Default:** `12000` · **Added:** 2026-07-06

## `AI_RUNTIME_MAX_USER_PROMPT_CHARS`

**Default:** `16000` · **Added:** 2026-07-06

## `AI_RUNTIME_MAX_OUTPUT_TOKENS`

**Default:** unset (per-intent defaults in `ContextBuilderService`)
**Added:** 2026-07-06

Per-intent override: `AI_RUNTIME_MAX_OUTPUT_{INTENT}` (e.g. `AI_RUNTIME_MAX_OUTPUT_CODE`).

## `AI_RUNTIME_WALL_MAX_MESSAGES`

**Default:** `40` · **Added:** 2026-07-06

Max messages sent to wall summarizer LLM per run.

## `AI_PROMPT_CACHE_TTL_SEC`

**Default:** `3600` (1 hour)
**Added:** 2026-07-06

TTL for in-memory (Phase 0) prompt hash cache entries. Phase 1 uses the
same variable when `REDIS_URL` backs the cache.

## `AI_PROMPT_CACHE_MAX_ENTRIES`

**Default:** `500`
**Added:** 2026-07-06

Maximum LRU entries in the in-process prompt cache per API instance.

## `AI_RUNTIME_FAST_MODEL`

**Default:** `deepseek-chat`
**Added:** 2026-07-06

Model slug for simple Q&A and social-draft routing (advisory in Phase 0).

## `AI_RUNTIME_REASONING_MODEL`

**Default:** `deepseek-reasoner`
**Added:** 2026-07-06

Model slug for reasoning / regulatory / architecture prompts.

## `AI_RUNTIME_CODE_MODEL`

**Default:** `glm-5.2`
**Added:** 2026-07-06

Model slug for code-draft and implementation prompts.

## `GLM_API_KEY`

**Default:** unset
**Added:** 2026-07-06 (z.ai GLM Coding Plan)

Platform GLM API key for Founder Brain coding tier. Preferred over encrypted
promo / AI Routing keys when set on the Railway API service. Never commit to
git — set in Railway only.

## `GLM_API_BASE`

**Default:** `https://api.z.ai/api/coding/paas/v4`
**Added:** 2026-07-06

OpenAI-compatible base URL for **GLM Coding Plan** subscription quota. Do not
use the general endpoint (`/api/paas/v4`) — it is not interchangeable with
the coding endpoint. See `docs/GLM-ZAI-PROVIDER-SETUP.md`.

## `DEEPSEEK_API_KEY`

**Default:** unset (falls back to Platform Brain / promo / AI Routing keys)
**Added:** 2026-07-06 (Founder Brain fast tier)

Optional env override for platform DeepSeek calls. Showcase bot may use a
separate encrypted key via Admin → Agent Control.

## `FOUNDER_BRAIN_TWO_MODEL_ROUTING`

**Default:** unset / `false`
**Added:** 2026-07-06

When `true`, Founder Brain runtime routes fast/simple intents to the fast
provider and coding/architecture intents to the coding provider. Admin can
also toggle via `/admin/control` → AI Keys → Founder Brain Providers (stored
in `PlatformSettings.founderBrainProvidersJson`).

## `FOUNDER_BRAIN_FAST_PROVIDER`

**Default:** `deepseek`
**Added:** 2026-07-06

Provider slug for fast tier when two-model routing is on (`deepseek` or `glm`).

## `FOUNDER_BRAIN_CODING_PROVIDER`

**Default:** `glm`
**Added:** 2026-07-06

Provider slug for coding / reasoning tier when two-model routing is on.

### Where Founder Brain provider env vars are read

- `apps/api/src/founder-ai-runtime/founder-brain-providers.service.ts`
- `apps/api/src/founder-ai-runtime/model-router.service.ts`
- `apps/api/src/founder-os/glm-config.ts` — `GLM_API_BASE`

## `REDIS_URL`

**Default:** unset (in-memory cache only)
**Added:** 2026-07-06 (documented for Phase 1)

When set, Phase 1 will persist prompt hash cache across Railway instances.
Phase 0 ignores this variable — safe to set in `.env` ahead of deploy.

## `DEMO_MODE_ENABLED`

**Default:** unset / false (demo endpoints disabled)
**Added:** 2026-07-06 (Demo Mode MVP)

Must be exactly `true` on the **Railway API service** to allow admin demo
seed, reset, and smoke routes. Fail closed in production unless explicitly
enabled.

### Where it's read

- `apps/api/src/demo/demo.constants.ts` — `isDemoModeEnabled()`
- `apps/api/src/demo/demo-mode.guard.ts` — guards all `/admin/demo/*` routes

### Related

- `DEMO_SEED_SCALE` — controls user/project counts when seeding
- `docs/DEMO-MODE-AND-VERIFICATION.md`

## `DEMO_SEED_SCALE`

**Default:** `medium`
**Added:** 2026-07-06 (Demo Mode MVP)

Controls how many synthetic records `POST /api/admin/demo/seed` creates.

| Value | Users | Projects | Founders |
|-------|-------|----------|----------|
| `small` | 20 | 10 | 8 |
| `medium` | 35 | 12 | 10 |
| `large` | 50 | 15 | 12 |
| `xlarge` | 2,500 | 150 | 500 |

Set on Railway API service only. Web admin panel reads scale from
`GET /api/admin/demo/status`.

## `DDOLLAR_RUNTIME_ENABLED`

**Default:** unset / false
**Added:** 2026-07-06 (Platform Readiness Slice 1)

When `true`, `PointsService.award()` / `spend()` delegate to
`DdollarRuntimeService` — incrementing both spendable balance and
`User.lifetimeContributionEarned` on earn; decrementing spendable only on spend.

Also enables marketplace treasury ledger writes on marketplace purchases.

### Related

- `docs/DDOLLAR-POC-TWO-LEDGER-SPEC.md`
- Admin audit: `GET /api/admin/ddollar/treasury`

## `PHASE_15_TRUST_LAYER_ENABLED`

**Default:** `false`
**Added:** 2026-07-06 (Platform Readiness Slice 4)

When `true`, enables the Phase 1.5 trust layer: regulatory questionnaire,
Launch Qualification score (0–100), progressive unlock stages 1–6, compliance
timeline API, and gates on Proof Raise + token metadata preview.

Requires regulatory classification + LQ ≥ 70 + launch stage ≥ Graduation.

### Where it's read

- `apps/api/src/phase15/phase15.constants.ts`
- `apps/api/src/regulatory/`, `launch-qualification/`, `trust/`

## `OBSERVATORY_ENABLED`

**Default:** `false` (admin-only)
**Added:** 2026-07-06 (Platform Readiness Slice 6)

When `true`, exposes `GET /api/admin/observatory` and the web control room at
`/admin/observatory`. Aggregates subsystem health, git SHA version, and last
demo smoke results.

### Where it's read

- `apps/api/src/phase15/phase15.constants.ts` — `isObservatoryEnabled()`
- `apps/api/src/observatory/observatory.service.ts`

## See also

- `docs/FOUNDER-AI-RUNTIME-SPEC.md` — runtime architecture + migration phases
- `docs/GLM-ZAI-PROVIDER-SETUP.md` — z.ai GLM Coding Plan API setup
- `docs/SECRETS_STORAGE.md` — where to put API keys / DB URLs
- `.env.example` (root) — connection string placeholders
- `.env.production.example`, `.env.neon.example` — production / Neon
  placeholders
