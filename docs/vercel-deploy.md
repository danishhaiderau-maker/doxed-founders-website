# Vercel deploy checklist (web — doxxedcrypto.digital)

## Why production can look stale

GitHub `master` may be ahead of what Vercel serves. Symptoms:

- Homepage still shows old hero (`sticky top-0` header, long feature sections)
- `/trust-center`, `/town-hall`, `/privacy` return 404
- Build ID in page source does not match latest push

**Railway API** deploys from the same repo and may be current while **Vercel web** is not.

## Fix: redeploy Vercel

1. Open [Vercel Dashboard](https://vercel.com) → project for `doxxedcrypto.digital`
2. Confirm **Git connection** points to `danishhaiderau-maker/doxed-founders-website` branch `master`
3. **Root directory:** `apps/web` (or monorepo setting that builds `@dcf/web`)
4. Click **Redeploy** → use latest commit (`master`)
5. Wait for build success

Or with Vercel CLI (after `vercel login`):

```powershell
cd apps/web
vercel --prod
```

## Required Vercel env vars

| Variable | Example |
|----------|---------|
| `NEXT_PUBLIC_API_URL` | `https://doxed-founders-website-production.up.railway.app` |
| `API_URL` | same as above |
| `NEXTAUTH_URL` | `https://doxxedcrypto.digital` |
| `NEXTAUTH_SECRET` | 32+ char secret |
| `GOOGLE_CLIENT_ID` | Google OAuth |
| `GOOGLE_CLIENT_SECRET` | Google OAuth |
| `TWITTER_CLIENT_ID` | X OAuth |
| `TWITTER_CLIENT_SECRET` | X OAuth |

## Google / Microsoft OAuth — privacy policy URL

When registering OAuth apps, use:

**Privacy policy:** `https://doxxedcrypto.digital/privacy`

We only use Google/X login for authentication — no data selling, paper trading only.

## Verify after deploy

```powershell
# Homepage should include new hero copy
(Invoke-WebRequest -Uri "https://doxxedcrypto.digital" -UseBasicParsing).Content -match "Private by default"

# New routes
(Invoke-WebRequest -Uri "https://doxxedcrypto.digital/trust-center" -UseBasicParsing).StatusCode
(Invoke-WebRequest -Uri "https://doxxedcrypto.digital/town-hall" -UseBasicParsing).StatusCode
(Invoke-WebRequest -Uri "https://doxxedcrypto.digital/privacy" -UseBasicParsing).StatusCode
```

All should return `True` / `200`.

```powershell
npm run smoke:test
```
