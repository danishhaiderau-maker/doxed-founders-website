# Railway deploy checklist

## Which service to use

| Service | Use? | Notes |
|---------|------|--------|
| **doxed-founders-website** | ✅ Yes | Production API — `doxed-founders-website-production.up.railway.app` |
| **@dcf/api** | ⚠️ Duplicate | Same repo; merge into above or delete |
| **@dcf/web** | ❌ Delete | Web runs on **Vercel**. This service has no env vars and will always fail healthcheck |

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
- **@dcf/web** had **0 variables** (no `DATABASE_URL`, no `JWT_SECRET`) → instant crash.
- **JWT_SECRET** must be 32+ chars when `NODE_ENV=production`.

**Fix:** `scripts/start-api-prod.mjs` auto-detects Railway (`RAILWAY_ENVIRONMENT`) and uses **db push**, continuing on schema-already-sync warnings.

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

## Dashboard cleanup (recommended)

1. **@dcf/web** → Settings → disconnect GitHub or delete service (web is on Vercel).
2. **@dcf/api** → delete if duplicate of doxed-founders-website, or point only one service at the repo.
3. Clear failed deploy history is optional; yellow warning counts are historical failures.

## Verify

```bash
curl https://doxed-founders-website-production.up.railway.app/api/health
curl https://doxxedcrypto.digital/api/health
```

Both should return `"status":"ok"` and `"database":"ok"`.
