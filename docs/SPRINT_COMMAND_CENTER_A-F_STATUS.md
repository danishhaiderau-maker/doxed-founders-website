# Founder OS Command Center — Sprints A–F Status

**Date:** 2026-06-05  
**Sprint commit:** `1e6f1ce` (Implement Founder OS Command Center sprints A through F)  
**Branch:** `master`

## Summary

All six sprints (A–F) implemented in one pass. Local builds: `@dcf/utils`, `@dcf/api`, `@dcf/web` succeeded.

## Per sprint

| Sprint | Status | Notes |
|--------|--------|-------|
| **A — Brain is the boss** | ✅ | Hero chat: single input + Send; hidden Ask/Build + provider/worker pickers; auto-route via `resolveHeroBrainSendMode`; route footer on messages |
| **B — CEO inbox buckets** | ✅ | `getFounderQueueBucket`, grouped UI, actionable-only badge count |
| **C — Runtime ownership** | ✅ | `agentRuns.start` on copilot Cursor dispatch; active run → queue `AGENT_REVIEW`; `sourceRunId` on items; runtime steps already in `builder-run-live.ts` |
| **D — Agent Bus loop** | ✅ | Existing chain verified; dedupe in `shouldSkipBusHandoff`; queue actions (merge/publish/sync/build) from Mission Control |
| **E — Decision memory** | ✅ | `founder-decision-log.ts`, `GET/POST /copilot/decisions`, auto-detect in `ask()`, injected in context assembly |
| **F — Desktop Bridge** | ✅ | Founder Node heartbeat sends branch + open file names + task; API `saveFromHeartbeat`; timeline + Brain context |

## Build / deploy (local audit 2026-06-05)

| Step | Result |
|------|--------|
| `npm run build:utils` | ✅ |
| `npm run build --workspace=@dcf/api` | ✅ |
| `npm run build --workspace=@dcf/web` | ✅ |
| `npm run build:api` (full + prisma generate) | ⚠️ EPERM on Prisma DLL if API process holds lock — Nest `tsc` build OK |
| `npm run sync:all` | ✅ (see sync audit) |

## Sync audit (2026-06-05, autonomous pipeline)

| Target | Result |
|--------|--------|
| **GitHub** | OK `master` @ `c2b2440` — rebased, pushed Stage C + APK v0.4.0 label; clean tree |
| **Neon** | OK `db:push:neon` — schema already in sync with Prisma |
| **Vercel** | OK Production deploy `dpl_6uUgzaPW8RwqZ8KKMA2MD84mqkqh` → alias **https://doxxedcrypto.digital** |
| **Railway** | OK Redeploy triggered: `doxed-founders-website` (API), `btc-conservative-agent` (bot) |
| **Smoke** | OK `scripts/smoke-test.mjs` — all checks passed against production |

**Notes (non-blocking):**

- Neon step: local `prisma generate` EPERM (Windows DLL lock) — push succeeded.
- Vercel: initial read reported missing `NEXT_PUBLIC_API_URL`; sync script set production env vars and deployed.
- Showcase bot credentials: skipped (none saved in Admin).

```bash
# Re-run full pipeline
npm run sync:all
```

## User testing checklist

1. Open **Founder Den → Mission Control** — confirm one chat, no mode picker, Send button only.
2. Ask **"What's the status?"** — should route to research/ask, not Builder.
3. Say **"Fix discover page"** — should auto-dispatch build (if Cursor connected).
4. Check **CEO inbox** right rail — buckets: Attention, Review, Approval, etc.
5. Complete a build — verify Agent Bus follow-up message + publish queue item.
6. Say **"We decided to ship feed first because traction is higher"** — ask Brain **"why did we choose feed?"**
7. Pair **Founder Node** — confirm Desktop bridge card in timeline panel (branch, files, task).

## Blockers requiring user action

- **Cursor / OpenHands API keys** in Settings → AI stack for live builds
- **GitHub** repo linked for PR queue + grounded status
- **Founder Node pairing** for full Desktop Bridge (optional)
- **Showcase exchange credentials** in Admin if bot showcase is needed
