# Railway deploy checklist

## GitHub red ✗ on commits (not an outage or security breach)

Public repo readers often see a **red X** next to commits on `master`. That is **Railway reporting a failed or cancelled deploy** to GitHub — not proof that the site was hacked, and not always proof that production is down.

| What you see on GitHub | What it usually means |
|------------------------|------------------------|
| Red ✗ on latest commit | A **deploy attempt** for a linked Railway service failed or was cancelled |
| Blue “master isn’t protected” | Optional branch-hardening suggestion — unrelated to deploy health |
| Green ✓ on an older commit | Production may still be running that **last successful** deploy |

### Not a security incident

- Failed statuses do **not** mean secrets leaked in git.
- `docs/` and other safe commits can still show ✗ if Railway redeploys a **secondary service** (e.g. showcase bot) and that deploy fails.
- Treat ✗ as **CI/deploy telemetry**, not an intrusion alert.

### Typical status names (Railway → GitHub)

| Context | Common message |
|---------|----------------|
| `giving-spirit - doxed-founders-website` | Deployment failed / cancelled |
| `giving-spirit - btc-conservative-agent` | Deployment failed (often missing `BYBIT_*` env or boot crash before `/health`) |

Click the ✗ on the commit → open the Railway link → read **deploy logs** for that service only.

### Is production actually down?

Railway keeps the **last successful deployment** serving traffic when a newer attempt fails.

```bash
npm run housekeeping
# or
curl https://doxxedcrypto.digital/api/health
curl https://doxxedcrypto.digital/api/health/live
```

If those return OK, founders can still use the product; GitHub is showing a **failed retry**, not a live outage.

### Why docs-only pushes can still show ✗

| Service | Auto-deploy on `master` push? |
|---------|-------------------------------|
| **doxed-founders-website** (API) | Often limited by `watchPatterns` in root `railway.toml` (API paths only) |
| **btc-conservative-agent** | Usually **every push** unless you add watch paths or disable auto-deploy |

A markdown-only commit can trigger the **bot** service, which **requires** valid `BYBIT_API_KEY` / `BYBIT_SECRET` before Flask starts — so `/health` never answers and Railway marks the commit failed.

**Mitigations (operators):**

1. Add `watchPatterns` under `services/btc-conservative-agent/railway.toml` so only bot code changes redeploy the bot.
2. Or disable auto-deploy for the bot; deploy manually when `bot.py` changes.
3. Delete duplicate Railway services (`@dcf/api`, `@dcf/web`) — see [Dashboard cleanup](#dashboard-cleanup-do-this-once) below.
4. Optionally turn off “wait for CI / deployment status” anxiety: protect `master` for process safety, not because ✗ equals breach.

### Related: scheduled GitHub Actions

Workflows like **X social daily sync** run on a **cron** schedule. Their failure is separate from your commit (usually missing `API_URL` / `ADMIN_SYNC_JWT` repo secrets). See [PRODUCTION_CHECKLIST.md](./PRODUCTION_CHECKLIST.md).

---

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
| `WEBAUTHN_ORIGIN` | `https://doxxedcrypto.digital` — passkey registration origin |
| `WEBAUTHN_RP_ID` | `doxxedcrypto.digital` — WebAuthn relying party ID |
| `PUBLIC_SITE_URL` | `https://doxxedcrypto.digital` — fallback for WebAuthn + links |
| `WEB_APP_URL` | `https://doxxedcrypto.digital` — post-GitHub OAuth redirect |
| `API_URL` | `https://doxed-founders-website-production.up.railway.app` |
| `GITHUB_CLIENT_ID` | GitHub OAuth App — Founder OS repo connect |
| `GITHUB_CLIENT_SECRET` | GitHub OAuth App secret |
| `GITHUB_OAUTH_CALLBACK_URL` | `{API_URL}/api/auth/github/callback` |
| `GITHUB_WEBHOOK_SECRET` | Optional — GitHub repo push webhook for instant sync |

### Phala CVM (optional — P1 vault backup + P2 unwrap)

Set after deploying `workers/phala-cvm-workload` on Phala Cloud. See [OPS_PHALA_CVM_RAILWAY.md](./OPS_PHALA_CVM_RAILWAY.md).

| Variable | Notes |
| --- | --- |
| `PHALA_CVM_BACKUP_URL` | Phala INGRESS base (no path) |
| `PHALA_CVM_UNWRAP_URL` | Same base as backup |
| `PHALA_API_KEY` | Redpill / Phala platform key |
| `PHALA_CVM_API_KEY` | Bearer for CVM workload (matches Phala `CVM_WORKLOAD_AUTH_TOKEN`) |
| `PHALA_INFERENCE_URL` | Default `https://api.redpill.ai/v1` |
| `PHALA_MODEL` | e.g. `phala/deepseek-chat-v3-0324` |
| `PHALA_CVM_WORKLOAD_ID` | Optional label in receipts |

```bash
npm run bootstrap:phala-cvm-env
npm run apply:railway:phala-cvm
npm run probe:phala-cvm
```

## Failed deploy badges (22 / 15 / etc.)

Railway counts **every failed attempt** in history. They do **not** mean the site is down. See [GitHub red ✗ on commits](#github-red--on-commits-not-an-outage-or-security-breach) above.

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
