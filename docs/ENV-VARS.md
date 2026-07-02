# Environment variables — runtime behavior switches

This doc covers env vars that change runtime behavior (not secrets). For secret
storage see `SECRETS_STORAGE.md`. For deploy infra see `railway-deploy.md` /
`vercel-deploy.md`.

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

## See also

- `docs/API-ABUSE-AUDIT.md` — the full audit that motivated these vars
- `docs/SECRETS_STORAGE.md` — where to put API keys / DB URLs
- `.env.example` (root) — connection string placeholders
- `.env.production.example`, `.env.neon.example` — production / Neon
  placeholders
