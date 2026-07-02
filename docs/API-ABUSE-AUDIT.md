# DoxxedCrypto Platform — AI API Abuse Audit

**Date:** 2026-07-03
**Scope:** Code-only audit of `apps/api/src/` to find how certain accounts are burning through the platform's AI API spend (GLM 5.2 promo, DeepSeek platform-brain, Gemini, plus BYOK pathways) despite rate limits.
**Method:** Read-only code review. No DB access was available locally; all "queries to run" below are read-only SELECTs to execute against your Neon production console.

---

## TL;DR

Your rate limiting is **almost entirely missing on the endpoints that actually call the platform's own AI keys**, and the one custom limiter that exists (`RateLimiterService` on `copilot:ask`) is (a) trivially bypassable via sibling Copilot endpoints that share the same LLM code path, and (b) itself non-atomic. Combined with **non-atomic DDollar/credit balance checks** in `PointsService.spend` and `FounderOsService.runCursorBuildRoom`, a single user with parallel requests can drive balances negative and blow past the 30M-token promo cap. The single biggest hole is **`POST /share/paraphrase`**: `@SkipThrottle()`, no balance check, no per-user cap, calls the platform DeepSeek key on every request.

**Bypass paths found:** 4 Critical, 3 High, 3 Medium.

**Top 3 most likely abuse mechanisms (ranked):**
1. `POST /share/paraphrase` hammered in parallel — no rate limit, no balance check, uses platform DeepSeek key directly. (Critical)
2. `POST /copilot/hands-free`, `/copilot/social-draft`, `/copilot/mission-build` — share `FounderCopilotService.ask()` → `BuilderService.tryCopilotChatCompletion` but **skip the `RateLimiterService` check** that wraps only `copilot/ask` and `copilot/ask/stream`. (Critical)
3. Race condition in `PointsService.spend` (non-atomic check-then-decrement) + promo token-cap race (`getUserPromoStatus` aggregate-then-log) — parallel requests drive balance negative and exceed the 30M promo token cap. (Critical)

---

## 1. Every AI / credit-consuming endpoint

| Route | Method | Controller | AI provider called | Cost / balance mechanism | Rate limit guard | Notes |
|---|---|---|---|---|---|---|
| `/copilot/ask` | POST | `EventsController` | GLM / DeepSeek / Gemini / Ollama / OpenAI / Anthropic / Phala (via `BuilderService.tryCopilotChatCompletion`) | Promo token cap (30M/user/90d) checked via aggregate; BYOK path bills user's own key; token usage logged to `AiTokenUsageLog` **after** the call | `RateLimiterService.checkLimit('copilot:ask')` — 10/hr, 50/day per user (DB-backed) + global Throttler 100/min/IP | Only endpoint pair with the custom limiter |
| `/copilot/ask/stream` | POST (SSE) | `EventsController` | Same as above | Same as above | Same `RateLimiterService` check on connection start; **per-token streaming has no limiter** | Long streams count as 1 rate unit |
| `/copilot/hands-free` | POST | `EventsController` | Same as above (calls `this.ask()` internally for `weekly_summary`/`launch_report`/`community_update`) | Same as `ask` | **NONE** — no `RateLimiterService`, falls under global Throttler 100/min/IP only | **Bypasses copilot:ask limiter entirely** |
| `/copilot/social-draft` | POST | `EventsController` | Same as above (`draftSocialUpdate` → `tryCopilotChatCompletion`) | Same as `ask` | **NONE** | **Bypasses copilot:ask limiter** |
| `/copilot/mission-build` | POST | `EventsController` | Cursor/OpenHands dispatch (BYOK); LLM only for mission intelligence | Founder Credits | **NONE** | |
| `/copilot/resume` | POST | `EventsController` | Possibly via `ask`/builder | — | **NONE** | |
| `/copilot/autopilot` | POST | `EventsController` | Possibly LLM | — | **NONE** | |
| `/share/paraphrase` | POST | `ShareController` (`@SkipThrottle()`) | **Platform DeepSeek key** (`getDecryptedPlatformDeepseekKey`) — NOT promo path, NOT BYOK | **NONE** — no DDollar spend, no balance check, no quota check | **NONE** — class-level `@SkipThrottle()` disables the global 100/min limiter | **Critical: any authed user can hammer the platform DeepSeek key** |
| `/wall/projects/:slug/summarize` | POST | `WallController` (`@SkipThrottle()`) | **Platform GLM 5.2 key** (`getDecryptedPlatformGlmKey`) | `PointsService.spend(1000 DDollar)` *before* LLM call; billed `platform_promo` | **NONE** — `@SkipThrottle()` | Raceable balance check (see §3b) |
| `/founder-os/build-room` | POST | `FounderOsController` | None directly (rule-based `buildSuggestionFromBuildPrompt`) | `founder.founderCredits` decrement of `CURSOR_BUILD_SESSION_CREDITS` | Global 100/min/IP (no `@SkipThrottle`) | Raceable balance check (see §3b) |
| `/founder-os/projects/:projectId/bounties` | POST | `FounderOsController` | None | `founder.founderCredits` decrement | Global 100/min/IP | Raceable balance check |
| `/wall/messages/:messageId/pin` | POST | `WallController` (`@SkipThrottle()`) | None | `PointsService.spend(amount)` | **NONE** | Raceable balance check |
| `/founder-node/inference-usage` | POST | `FounderNodeController` | None (logs only) | Logs client-supplied token counts to `AiTokenUsageLog` via `recordUsageBatch` | FounderNodeGuard (nodeToken) | **Token counts are client-controlled** — analytics poisoning, not real spend |
| `/founder-node/inference/pending` + `/inference/:jobId/complete` | GET/POST | `FounderNodeController` | Ollama on user's local machine (NOT platform spend) | — | FounderNodeGuard | Not a platform-spend vector |
| `/builder/openhands/dispatch`, `/cursor/dispatch`, `/execute-task` | POST | `BuilderController` | Cursor / OpenHands via user's BYOK key | — | Global 100/min/IP | Not platform-spend |

**Total AI-consuming endpoints found:** 9 distinct HTTP routes invoke an external LLM (`/copilot/ask`, `/copilot/ask/stream`, `/copilot/hands-free`, `/copilot/social-draft`, `/copilot/mission-build`, `/copilot/resume`, `/copilot/autopilot`, `/share/paraphrase`, `/wall/projects/:slug/summarize`). Only **2 of the 9** have the custom per-user rate limiter.

---

## 2. Rate limit architecture

There are **two** layers, both with significant gaps:

### 2a. Global `@nestjs/throttler` — `apps/api/src/app.module.ts:55`

```ts
ThrottlerModule.forRoot([{ ttl: 60000, limit: 100 }]),
// ...
{ provide: APP_GUARD, useClass: ThrottlerGuard },
{ provide: APP_GUARD, useClass: JwtAuthGuard },
```

- **Limit:** 100 requests / 60 seconds.
- **Key:** Default throttler key = client IP (`req.ip`). No custom `getTracker` is provided, so it is **IP-only, not userId**.
- **Storage:** In-memory (no `@nestjs/throttler-storage-*` provider is registered). On Vercel/Railway with multiple API replicas this means **each replica keeps its own counter** — a user can multiply their budget by hitting different replicas. Even on a single replica, in-memory state resets on every deploy/cold start.
- **Bypassed by:** 17 controllers set `@SkipThrottle()` at class level: `share`, `wall`, `admin-control`, `paper-trading` (mostly), `trading-agents`, `founder-den`, `airdrop` (×2), `privacy`, `feed`, `prediction-markets`, `listing-applications`, `trust-center`, `town-hall`, `watchlist`, `paper-trading-payments`, `reputation`, `projects` (mostly). The two most-expensive AI routes (`/share/paraphrase` and `/wall/.../summarize`) are in this list.

### 2b. Custom `RateLimiterService` — `apps/api/src/events/rate-limiter.service.ts`

- **Limits:** `rateLimitHourly` default **10/hr**, `rateLimitDaily` default **50/day** per `(userId, endpoint)`, configurable via `PlatformSettings`.
- **Key:** `userId` + endpoint string. Stored in the `RateLimit` Prisma table (Neon, durable).
- **Coverage:** Used in **exactly two** places: `EventsController.ask` (`copilot:ask`) and `EventsController.askStream` (`copilot:ask/stream`). Confirmed by grepping `RateLimiterService` / `checkLimit` across the whole `apps/api/src/` tree — only `events.controller.ts` references it.
- **Implementation:** Reads aggregate count → checks → upserts increment. **Not in a `$transaction`**, so two parallel requests can both pass the check before either increments (bounded race, ~2× overshoot per concurrency burst).
- **Storage key issue:** `windowStart` is bucketed to `hourStart` (top of the hour), so the limiter is a fixed-window counter — a user can fire 10 requests at 11:59:59 and another 10 at 12:00:00 (effectively 20 in one second).

### 2c. Promo token cap — `apps/api/src/founder-os/founder-promo.service.ts:165`

- **Limit:** `founderPromoTokenCap` default **30,000,000 tokens** per user over a 90-day window.
- **Check:** `getUserPromoStatus()` aggregates `aiTokenUsageLog` where `billingSource='platform_promo'` and compares the sum to the cap. The check is run inside `resolvePromoApiKey()` **before** the LLM is called, and the usage row is written by `logAiTokenUsage()` **after** the LLM returns. **Not atomic.** N parallel requests can all read `tokensUsed < cap`, all proceed, all call GLM, and only then log their usage — overshooting the cap by N × (tokens per call).

---

## 3. Bypass / abuse paths

### a. Missing rate limit on a subset of AI endpoints — **Critical**

The custom `RateLimiterService` is wired into `copilot/ask` and `copilot/ask/stream` only. Three sibling endpoints share the **same** `FounderCopilotService.ask()` → `BuilderService.tryCopilotChatCompletion` code path but skip the limiter:

| Endpoint | Handler | LLM call site |
|---|---|---|
| `POST /copilot/hands-free` | `FounderCopilotService.handsFree` (`apps/api/src/events/founder-copilot.service.ts:2490`) | calls `this.ask(userId, text)` at line 2504 and 2508 for `weekly_summary`/`launch_report`/`community_update` actions |
| `POST /copilot/social-draft` | `FounderCopilotService.draftSocialUpdate` (line 2625) | calls `this.builder.tryCopilotChatCompletion` at line 2935 |
| `POST /copilot/resume` | `FounderCopilotService.resumeWork` (line 1264) | reuses `ask()` indirectly |

The rate limiter is enforced at the **HTTP handler** in `events.controller.ts:156` and `:196`, not in the service-layer `ask()` method — so any other caller of `ask()` bypasses it.

**Severity:** Critical
**Remediation:** Move the `rateLimiter.checkLimit(userId, 'copilot:ask')` call into `FounderCopilotService.ask()` and `tryCopilotChatCompletionStream`'s caller, or wrap every Copilot POST endpoint with the limiter in the controller. Better: gate at the `BuilderService.tryCopilotChatCompletion*` entry point so **every** LLM call goes through one chokepoint.

### b. Race condition on balance check — **Critical**

`apps/api/src/points/points.service.ts:38` `spend()`:

```ts
const user = await this.prisma.user.findUnique({ where: { id: userId }, select: { reputationPoints: true } });
if (!user || user.reputationPoints < amount) throw new BadRequestException(...);
await this.prisma.user.update({ where: { id: userId }, data: { reputationPoints: { decrement: amount } } });
```

The read and the decrement are **two separate queries with no transaction and no atomic guard**. With `reputationPoints = 1000` and 10 parallel `spend(1000)` calls, all 10 read 1000, all 10 pass the check, all 10 decrement → balance = -9000. Each call returns success and proceeds to make an AI call (e.g. `/wall/.../summarize` → GLM).

The same pattern is in `FounderOsService.runCursorBuildRoom` (`apps/api/src/founder-os/founder-os.service.ts:665-675`) and `createBounty` (lines 1075-1082): read `founderCredits`, check, then `update({ decrement })` — not in a `$transaction`.

**Severity:** Critical
**Remediation:** Replace with an atomic conditional update:
```ts
const result = await this.prisma.user.updateMany({
  where: { id: userId, reputationPoints: { gte: amount } },
  data: { reputationPoints: { decrement: amount } },
});
if (result.count === 0) throw new BadRequestException('Insufficient DDollar');
```
`updateMany` with a `where` clause on the balance is a single atomic statement in Postgres. Apply the same pattern to `founder.founderCredits` decrements.

### c. Promo token-cap race — **Critical**

`apps/api/src/founder-os/founder-promo.service.ts:165-183` computes `tokensUsed` by aggregating `aiTokenUsageLog`, and `tokensUsed >= tokenCap` gates eligibility. `resolvePromoApiKey()` (line 137) calls `getUserPromoStatus()` then returns the key. The usage row is only written by `logAiTokenUsage()` after the LLM returns (`apps/api/src/builder/builder.service.ts:2336`). Between the eligibility check and the usage log, an attacker can fan out N parallel requests that all see `tokensUsed < cap` and all proceed.

**Severity:** Critical
**Remediation:** Add a pre-increment "reservation" row in `aiTokenUsageLog` (or a separate `AiTokenReservation` table) inside a transaction before the LLM call, then update it with actual tokens after. Or, simpler, drop the cap to a per-minute request count and enforce via the same `RateLimiterService`.

### d. `/share/paraphrase` — wide-open platform DeepSeek key — **Critical**

`apps/api/src/share/share.controller.ts:8` sets `@SkipThrottle()` at the class level. `apps/api/src/share/share.service.ts:57` calls `this.founderPromo.getDecryptedPlatformDeepseekKey()` — the **platform's own** DeepSeek key, not the user's BYOK, not the promo path with token cap. There is **no `PointsService.spend`, no balance check, no per-user counter, no throttle**. Any authenticated user can `while true; curl -X POST /share/paraphrase` and burn the platform DeepSeek account until DeepSeek's own hard limit kicks in.

**Severity:** Critical
**Remediation (today):**
1. Remove `@SkipThrottle()` from `ShareController`, or add an explicit `@Throttle({ default: { limit: 5, ttl: 60000 } })`.
2. Add a `RateLimiterService.checkLimit(userId, 'share:paraphrase')` call at the top of `paraphraseTweet`.
3. Better: route `share_paraphrase` through `BuilderService.tryCopilotChatCompletion` so it inherits the promo cap + BYOK resolution + token logging instead of using the raw platform-brain key.

### e. `RateLimiterService` itself is non-atomic + fixed-window — **High**

`apps/api/src/events/rate-limiter.service.ts:28-66`: aggregate read → check → upsert increment, with no `$transaction` and no compare-and-swap. Two parallel requests can both pass the `hourlyUsed >= hourlyLimit` check (line 42) before either upserts. The overshoot is bounded by the user's concurrency, but it means a determined user can do ~2-3× the configured limit per hour. Also, `windowStart` is bucketed to the top of the hour, so calls at 11:59:59 and 12:00:01 land in different windows.

**Severity:** High
**Remediation:** Wrap the check + increment in a `prisma.$transaction` with `Serializable` isolation, or replace with a single `updateMany ... where count < limit` atomic increment and check the affected rows count.

### f. `@SkipThrottle()` on `/wall/.../summarize` — **High**

`apps/api/src/wall/wall.controller.ts:10`. The endpoint charges 1000 DDollar via `points.spend` (which is itself raceable — see §3b) and then calls the platform GLM key (`apps/api/src/wall/wall.service.ts:757 runSummarizerLlm`). With unlimited DDollar (via the §3b race or referral farming), a user can fire summarizer activations in parallel — each one a GLM call billed as `platform_promo`, which also blows through the §3c promo cap.

**Severity:** High
**Remediation:** Remove `@SkipThrottle()`. Add `RateLimiterService.checkLimit(userId, 'wall:summarize')` with a low hourly cap (e.g. 3/hr). Fix `points.spend` atomicity (§3b).

### g. Keying on IP — VPN/proxy rotation — **High**

The global `ThrottlerGuard` uses the default IP tracker. A user on a VPN/proxy pool or a residential rotating network can rotate IPs and bypass any IP-keyed limit. The custom `RateLimiterService` is userId-keyed (good), but only covers 2 of 9 AI endpoints. Any endpoint relying solely on the global throttler is effectively unbounded for a rotating-IP user.

**Severity:** High
**Remediation:** Make `RateLimiterService` (userId-keyed, DB-backed) the **primary** limiter for every AI-consuming route, and treat IP-throttling as defense-in-depth only.

### h. Referral / signup bonus farming — **Medium**

`apps/api/src/account/referral.service.ts:135` `tryCompleteReferralRewards` awards DDollar to the referrer per referee (keyed `REFERRAL:<refereeId>`, so a referrer with N referrals gets N × `REFERRAL_REWARD`). The only gate is the referee must have a Twitter OAuth connection and (for the larger bonus) be `xVerified`. Twitter accounts can be created in bulk. Combined with §3b, a farmer can funnel DDollar into one account and then race `points.spend` to drive it negative, multiplying AI calls.

**Severity:** Medium
**Remediation:** Cap total referral earnings per user per 30 days. Add fraud signals (same IP across referrer + multiple referees, sequential signup timestamps). Require email verification before referral payout.

### i. `FounderNodeInferenceService.recordUsageBatch` — client-supplied token counts — **Medium**

`apps/api/src/founder-node/founder-node.controller.ts:174` `POST /founder-node/inference-usage` accepts an array of `{ promptTokens, completionTokens, ... }` from the paired Founder Node and writes them verbatim to `aiTokenUsageLog` via `PlatformAdoptionService.recordAiUsage`. A malicious paired node can report billions of tokens, poisoning the adoption chart and the promo token cap computation (which sums `aiTokenUsageLog`). This doesn't directly cost platform API $, but it can make the `tokensUsed` aggregate artificially high (forcing you to buy more quota) or, if the attacker reports tokens *under* their real usage, hide their actual burn.

**Severity:** Medium
**Remediation:** Don't trust client-supplied token counts for billing/cap decisions. Either (a) ignore `founder_os_local` rows in the promo cap aggregate (the current `where: { billingSource: 'platform_promo' }` filter in `getUserPromoStatus:170` already excludes them — good, verify it stays that way), and (b) clamp/sanity-check reported token counts (reject entries > 1M tokens).

### j. Long-running streaming calls have no per-token cap — **Medium**

`POST /copilot/ask/stream` checks `RateLimiterService` once on connection start (`events.controller.ts:196`) then streams an arbitrary number of tokens over a single SSE connection. The promo cap (§3c) is the only thing that would catch a runaway stream, and it's raceable. A user who opens a stream with a huge prompt and `max_tokens` high can burn well beyond what the "1 request" rate unit implies.

**Severity:** Medium
**Remediation:** Cap `max_tokens` server-side in `tryCopilotChatCompletionStream`. Track running token count per stream and abort when it exceeds a per-user per-hour token budget.

---

## 4. Logging / analytics — what data exists to identify abusers

### Tables that capture AI spend

- **`AiTokenUsageLog`** (`prisma/schema.prisma:580`) — written by `PlatformAdoptionService.recordAiUsage` (`apps/api/src/projects/platform-adoption.service.ts:43`). Fields: `userId`, `projectId`, `provider`, `source` (e.g. `copilot`, `copilot_forced`, `share_paraphrase`, `wall_summarizer`, `founder_node_local`), `billingSource` (`byok` | `platform_promo` | `platform_brain` | `founder_os_local`), `promptTokens`, `completionTokens`, `createdAt`. Indexed on `(userId, createdAt)` and `(projectId, createdAt)`. **This is the canonical per-user AI spend log.**
- **`PointLedger`** (`prisma/schema.prisma:1488`) — every DDollar award/spend. Use this to see DDollar balances going negative or referral farming.
- **`FounderCreditLedger`** — every Founder Credit delta (build room, bounties).
- **`RateLimit`** (`prisma/schema.prisma:2628`) — `(userId, endpoint, windowStart, count)`. Use this to see who is hitting `copilot:ask` hardest (only covers that endpoint, but it's the only limiter counter you have).
- **`VirtualEconomyEvent`** — signup grants and paper-trading grants.
- **`FounderEvent`** — `COPILOT_COMMAND` events are emitted on every `ask` (line 1678 in founder-copilot.service.ts), regardless of which endpoint initiated it. Useful for cross-referencing AI calls that bypassed the rate limiter.

### Gaps in logging

- `/share/paraphrase` logs to `aiTokenUsageLog` with `source='share_paraphrase'`, `billingSource='platform_brain'` — **good**, you can see abusers there.
- `/wall/.../summarize` logs with `source='wall_summarizer'`, `billingSource='platform_promo'` — **good**.
- BYOK calls log `billingSource='byok'` — those don't cost you anything; filter them out when computing platform spend.
- `copilot/ask` (the rate-limited one) emits a `FounderEvent` of type `COPILOT_COMMAND`. The bypass endpoints (`hands-free`, `social-draft`) also end up emitting the same event because they go through `ask()` — so `FounderEvent` is actually a more complete counter than `RateLimit` for copilot activity.

### Queries to run on Neon (read-only)

Paste these into the Neon SQL editor. All are read-only `SELECT`s.

**Q1 — Top API consumers in the last 24h (any billing source):**

```sql
SELECT
  user_id,
  provider,
  billing_source,
  COUNT(*)                                       AS calls,
  SUM(prompt_tokens + completion_tokens)         AS total_tokens,
  SUM(prompt_tokens)                             AS prompt_tokens,
  SUM(completion_tokens)                         AS completion_tokens,
  MIN(created_at)                                AS first_call,
  MAX(created_at)                                AS last_call
FROM "AiTokenUsageLog"
WHERE created_at >= NOW() - INTERVAL '24 hours'
  AND billing_source IN ('platform_promo', 'platform_brain')   -- exclude byok, founder_os_local
GROUP BY user_id, provider, billing_source
HAVING COUNT(*) > 50 OR SUM(prompt_tokens + completion_tokens) > 500000
ORDER BY total_tokens DESC
LIMIT 100;
```

**Q2 — Abuse signature: users whose `copilot`-family calls far exceed the 50/day `RateLimit` cap (proof of bypass via `/copilot/hands-free` / `/copilot/social-draft`):**

```sql
SELECT
  u.id           AS user_id,
  u.email,
  u.twitter_handle,
  COUNT(t.*)     AS copilot_calls_24h,
  SUM(t.prompt_tokens + t.completion_tokens) AS total_tokens
FROM "AiTokenUsageLog" t
JOIN "User" u ON u.id = t."userId"
WHERE t.source IN ('copilot', 'copilot_forced')
  AND t.billing_source IN ('platform_promo', 'platform_brain')
  AND t.created_at >= NOW() - INTERVAL '24 hours'
GROUP BY u.id, u.email, u.twitter_handle
HAVING COUNT(t.*) > 50     -- the daily RateLimit cap is 50
ORDER BY copilot_calls_24h DESC
LIMIT 50;
```

If this returns rows, those users are bypassing `RateLimiterService` via the unprotected sibling endpoints (§3a).

**Q3 — `/share/paraphrase` hammerers (the wide-open endpoint — most likely abuser):**

```sql
SELECT
  t."userId",
  u.email,
  u.twitter_handle,
  COUNT(*)                                       AS paraphrase_calls_24h,
  SUM(t.prompt_tokens + t.completion_tokens)    AS tokens_burned
FROM "AiTokenUsageLog" t
JOIN "User" u ON u.id = t."userId"
WHERE t.source = 'share_paraphrase'
  AND t.billing_source = 'platform_brain'
  AND t.created_at >= NOW() - INTERVAL '24 hours'
GROUP BY t."userId", u.email, u.twitter_handle
HAVING COUNT(*) > 20
ORDER BY paraphrase_calls_24h DESC
LIMIT 50;
```

**Q4 — Users whose DDollar balance went negative (proof of `points.spend` race — §3b):**

```sql
SELECT id, email, twitter_handle, reputation_points, created_at
FROM "User"
WHERE reputation_points < 0
ORDER BY reputation_points ASC
LIMIT 100;
```

Also check the ledger for any single user with many `WALL_SUMMARIZER_MONTHLY` or `WALL_PIN` spends in a short window:

```sql
SELECT
  "userId",
  action_key,
  COUNT(*)       AS spend_events,
  SUM(amount)    AS net_ddollar,
  MIN("createdAt") AS first,
  MAX("createdAt") AS last
FROM "PointLedger"
WHERE amount < 0
  AND "createdAt" >= NOW() - INTERVAL '24 hours'
GROUP BY "userId", action_key
HAVING COUNT(*) > 10
ORDER BY spend_events DESC
LIMIT 100;
```

**Q5 — Promo token cap overshoot (users who exceeded 30M tokens — proof of §3c race):**

```sql
SELECT
  "userId",
  SUM(prompt_tokens + completion_tokens) AS total_promo_tokens,
  COUNT(*) AS calls,
  MIN(created_at) AS first,
  MAX(created_at) AS last
FROM "AiTokenUsageLog"
WHERE billing_source = 'platform_promo'
  AND created_at >= NOW() - INTERVAL '90 days'
GROUP BY "userId"
HAVING SUM(prompt_tokens + completion_tokens) > 30000000
ORDER BY total_promo_tokens DESC
LIMIT 100;
```

**Q6 — Referral farming signature (one referrer, many referees from same IP — you'll need to join with auth logs if you capture IP):**

```sql
SELECT
  referred_by_user_id,
  COUNT(*) AS referrals,
  COUNT(DISTINCT email) AS distinct_emails
FROM "User"
WHERE referred_by_user_id IS NOT NULL
  AND created_at >= NOW() - INTERVAL '30 days'
GROUP BY referred_by_user_id
HAVING COUNT(*) > 20
ORDER BY referrals DESC
LIMIT 50;
```

**Q7 — Wall summarizer activation bursts (one user activating many times = GLM burn):**

```sql
SELECT activated_by AS user_id, project_id, COUNT(*) AS activations, MAX(activated_at) AS last
FROM "ProjectWallSummary"
WHERE activated_at >= NOW() - INTERVAL '7 days'
GROUP BY activated_by, project_id
HAVING COUNT(*) > 5
ORDER BY activations DESC
LIMIT 50;
```

Note: `ProjectWallSummary` is upserted per project, so this only catches re-activations across different projects. For a per-call count, use `AiTokenUsageLog` with `source='wall_summarizer'`:

```sql
SELECT "userId", COUNT(*) AS summarizer_calls_24h
FROM "AiTokenUsageLog"
WHERE source = 'wall_summarizer'
  AND created_at >= NOW() - INTERVAL '24 hours'
GROUP BY "userId"
HAVING COUNT(*) > 10
ORDER BY summarizer_calls_24h DESC
LIMIT 50;
```

### What an "abuse signature" looks like

- `AiTokenUsageLog`: **>500 platform-promo/platform-brain calls in 24h** from one `userId`, or **>500k tokens in 1h**.
- `source='share_paraphrase'` with **>20 calls in 1h** from one user.
- `source IN ('copilot','copilot_forced')` with **>50 calls in 24h** from one user (proves they bypassed `RateLimiterService`).
- `User.reputation_points < 0` (proves the `points.spend` race fired).
- `PointLedger` showing **>10 `WALL_SUMMARIZER_MONTHLY` debits in 1h** from one user.
- `AiTokenUsageLog` with `prompt_tokens > 10000` on a single row (cost miscalc — see §3 in the report: every call charges a flat 1000 DDollar regardless of prompt size, so users max out prompt length).

### Vercel / Railway access logs

If you pull Vercel function logs or Railway proxy logs, filter for `POST /share/paraphrase`, `POST /copilot/hands-free`, `POST /copilot/social-draft`, `POST /wall/projects/:slug/summarize` and group by the JWT `sub` (userId) — Vercel doesn't log the body but it logs the path + status code. A user with hundreds of 200s on `/share/paraphrase` in an hour is your abuser.

---

## 5. Immediate mitigations — ship today

1. **Remove `@SkipThrottle()` from `ShareController`** and add an explicit per-user `RateLimiterService.checkLimit(userId, 'share:paraphrase')` call at the top of `ShareService.paraphraseTweet`. Set the hourly cap low (e.g. 5/hr). This single change stops the most likely abuser vector. **File:** `apps/api/src/share/share.controller.ts:8` and `apps/api/src/share/share.service.ts:53`.

2. **Wrap the bypass Copilot endpoints with the same limiter as `copilot/ask`.** In `EventsController`, add `this.rateLimiter.checkLimit(user.id, 'copilot:ask')` at the top of `handsFree`, `socialDraft`, `missionBuild`, `resume`, `runAutopilot`. Or — better, single fix — move the limiter call into `FounderCopilotService.ask()` itself so every caller is covered. **File:** `apps/api/src/events/events.controller.ts` (lines 236-266) and `apps/api/src/events/founder-copilot.service.ts:1443`.

3. **Make `PointsService.spend` atomic.** Replace the read-check-update with a single `prisma.user.updateMany({ where: { id: userId, reputationPoints: { gte: amount } }, data: { reputationPoints: { decrement: amount } } })` and throw if `result.count === 0`. Apply the same pattern to `FounderOsService.runCursorBuildRoom` (line 672) and `createBounty` (line 1079). **File:** `apps/api/src/points/points.service.ts:38`.

### Recommended next steps (this week)

- Replace in-memory `ThrottlerGuard` storage with `@nestjs/throttler-storage-redis` (or Upstash) so limits work across replicas.
- Add a custom `getTracker` to `ThrottlerGuard` that keys on `userId` when authenticated, IP only as fallback.
- Centralize all LLM calls behind one `AiGateway` service that enforces (a) per-user per-minute request cap, (b) per-user per-hour token budget, (c) atomic promo-cap reservation, before any provider call.
- Audit `FounderNodeGuard` issuance (`createPairingCode`) for self-pairing abuse that could let one user register many "nodes" — relevant only if node-reported usage ever feeds into caps or billing (currently it doesn't, but the schema allows it).

---

## 6. Audit method & limitations

- Pure static code review of `apps/api/src/`. No DB access (no `DATABASE_URL` locally). No scripts run against production.
- Every file path + line number cited above was read in this audit.
- The "Top 3 most likely abuse mechanisms" are best-guess rankings based on which bypass paths have the lowest effort-to-burn ratio. The actual abusers will be identified by running Q1-Q3 above on Neon.
- If after running Q1-Q3 you find that the top abusers are **not** concentrated on `share_paraphrase` or `copilot/hands-free`/`copilot/social-draft`, the next most likely vector is the `points.spend` race (Q4) combined with `wall_summarizer` (Q7).
