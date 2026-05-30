# Railway deploy checklist (@dcf/api)

If deployments show **FAILED** while the service stays on an old commit:

## Root cause (typical)

1. **Neon was bootstrapped with `db push`** — `prisma migrate deploy` fails on startup → API never listens → healthcheck fails.
2. **JWT_SECRET** must be 32+ chars when `NODE_ENV=production` (see `apps/api/src/main.ts`).

## Fix (repo)

- `railway.toml` sets `PRISMA_DB_PUSH=true` on start.
- `scripts/start-api-prod.mjs` uses **db push on Railway** and continues even if push warns (schema already matches).

## Required Railway variables

| Variable | Notes |
|----------|--------|
| `DATABASE_URL` | Neon PostgreSQL connection string |
| `JWT_SECRET` | 32+ chars (same as setup) |
| `NODE_ENV` | `production` (set by start command) |
| `CORS_ORIGINS` | `https://doxxedcrypto.digital` |
| `CREDENTIALS_ENCRYPTION_KEY` | 32+ char hex for API keys |

## Redeploy

```bash
railway login
railway link
railway up --detach
```

Or: Railway dashboard → @dcf/api → **Redeploy** latest `master`.

## Verify

```bash
curl https://YOUR-API.up.railway.app/api/health
```

Should return `"status":"ok"` and recent timestamp.
