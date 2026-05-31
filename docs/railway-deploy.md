# Railway deploy checklist

## Which service to use

| Service | Status | Action |
|---------|--------|--------|
| **doxed-founders-website** | ✅ Production API | Keep — domain `doxed-founders-website-production.up.railway.app` |
| **@dcf/api** | ❌ Duplicate | **Delete** — same repo, missing env vars → healthcheck fails |
| **@dcf/web** | ❌ Wrong service | **Delete** — web runs on **Vercel**, not Railway |

### Why @dcf/api and @dcf/web fail (but production works)

Your screenshots show the pattern:

| Step | @dcf/api / @dcf/web | doxed-founders-website |
|------|---------------------|------------------------|
| Build | ✅ Success | ✅ Success |
| Deploy | ✅ Success | ✅ Success |
| Healthcheck | ❌ Failed (~5 min) | ✅ Success |
| Active deploy | Stuck on **22h-old** commit | **Latest** commit (pressing-issue) |

Healthcheck fails because the API **refuses to start** without `JWT_SECRET` (32+ chars) and `DATABASE_URL`. Duplicate services have **0 or incomplete variables**. The real service has all 16 vars — no settings change needed there.

**You do not need to change settings on `doxed-founders-website`.** Delete the duplicates instead.

## Root cause of recent FAILED deploys

### 1. `node_env=production` could not be found (bf47d62)

Railway uses **Dockerfile** builder. A start command like:

```
NODE_ENV=production PRISMA_DB_PUSH=true node scripts/start-api-prod.mjs
```

is **not** run in a shell — Docker tries to execute `NODE_ENV=production` as the binary.

**Fix:** `railway.toml` uses `node scripts/start-api-prod.mjs` only. Set env vars in Railway **Variables** (or Dockerfile `ENV`).

### 2. Healthcheck failure (older deploys)

- Neon was bootstrapped with `db push` — `prisma migrate deploy` blocked startup → API never listened → healthcheck timed out.
- **Startup script ran blocking `prisma generate` + `db push` before Nest listened** when the Railway-only fast path did not trigger → healthcheck failed at ~90s while the old deploy stayed active.
- **@dcf/web** had **0 variables** (no `DATABASE_URL`, no `JWT_SECRET`) → instant crash.
- **JWT_SECRET** must be 32+ chars when `NODE_ENV=production`.

**Fix:** `scripts/start-api-prod.mjs` uses `NODE_ENV=production` to **start Nest immediately** and runs `db push` in the background after 15s. Healthcheck uses `/api/health/live` (no DB). Prisma `$connect` is non-blocking in production.

## Required Railway variables (doxed-founders-website only)

| Variable | Notes |
|----------|--------|
| `DATABASE_URL` | Neon PostgreSQL connection string |
| `JWT_SECRET` | 32+ chars |
| `NODE_ENV` | `production` |
| `PRISMA_DB_PUSH` | `true` (safe on Neon; script also sets on Railway) |
| `CORS_ORIGINS` | `https://doxxedcrypto.digital,https://www.doxxedcrypto.digital` |
| `CREDENTIALS_ENCRYPTION_KEY` | 32+ char hex for stored API keys |

## Failed deploy badges (22 / 15 / etc.)

Railway counts **every failed attempt** in history. They do **not** mean the site is down.

- If the service shows **Online** (green) and `/api/health` returns OK, production is on the **last successful** deployment.
- Failed deploys on **`@dcf/web`** are expected — delete that service (web is on Vercel).
- After fixing `railway.toml` start command, trigger redeploy: push to `master` or run `npm run redeploy:railway` (needs `RAILWAY_TOKEN`).

## Redeploy all API services

```powershell
# Option A — GitHub webhook (automatic on push to master)
git push origin master

# Option B — Railway token (https://railway.app/account/tokens)
$env:RAILWAY_TOKEN = "your-token"
npm run redeploy:railway

# Option C — CLI linked to doxed-founders-website
railway login
railway link
npm run fix:railway
```

## Dashboard cleanup (do this once)

### Option A — Railway dashboard (no token)

1. **@dcf/web** → **Settings** → **Delete Service**
2. **@dcf/api** → **Settings** → **Delete Service**
3. Leave **doxed-founders-website** alone (already SUCCESS)

### Option B — automated (needs token)

Add `RAILWAY_TOKEN` to `doxedcryptofounder-secrets/vault/.env.x.secrets`, then:

```powershell
npm run cleanup:railway
```

## Verify

```bash
curl https://doxed-founders-website-production.up.railway.app/api/health
curl https://doxxedcrypto.digital/api/health
```

Both should return `"status":"ok"` and `"database":"ok"`.
