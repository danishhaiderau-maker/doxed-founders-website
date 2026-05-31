# Production checklist

Run after shipping features or when something breaks in prod.

## 1. Housekeeping (automated)

```bash
npm run housekeeping
```

Checks API health, X automation, and key web routes. Exit code 1 lists what failed.

## 2. Deploy latest code

```bash
git pull origin master
npm run build:api
npm run redeploy:railway
npm run deploy:web
npm run housekeeping
```

Neon schema (if Prisma changed):

```bash
npm run db:push:neon
```

## 3. Admin login / 2FA

| Symptom | Fix |
|---------|-----|
| TOTP → "Authenticator unavailable" or 500 | `npm run fix:admin-2fa` then use new TOTP from vault |
| Locked out | Recovery code from `vault/.env.admin-security` (one-time each) |
| Passkey fails | `npm run apply:railway-webauthn` — WEBAUTHN_ORIGIN must match `doxxedcrypto.digital` |

## 4. X automation (Step 3 ops)

| Symptom | Fix |
|---------|-----|
| Bearer 401 on sync | Regenerate token in X Developer Portal → update `vault/.env.x.secrets` |
| No pinned feed items | `npm run finish:x-production` then `npm run run:first-x-sync` |
| Cron not running | GitHub secrets `API_URL` + `ADMIN_SYNC_JWT`; workflow `x-social-daily.yml` |

## 5. Privacy stack verification

1. Settings → Builder → each Step 1–5 panel shows green badges where applicable
2. Ask Copilot with Phala default → Attestation dashboard → **Verify latest TEE response**
3. Founder Node online → **Scan vault integrity**

## URLs

- Web: https://doxxedcrypto.digital
- API: https://doxed-founders-website-production.up.railway.app
- Health: `/api/health/live`
