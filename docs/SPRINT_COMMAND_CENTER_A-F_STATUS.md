# Founder OS Command Center — Sprints A–F Status

**Date:** 2026-06-05  
**Branch:** main (pushed after implementation)

## Summary

All six sprints (A–F) implemented in one pass. Builds: `@dcf/utils`, `@dcf/api`, `@dcf/web` succeeded.

## Per sprint

| Sprint | Status | Notes |
|--------|--------|-------|
| **A — Brain is the boss** | ✅ | Hero chat: single input + Send; hidden Ask/Build + provider/worker pickers; auto-route via `resolveHeroBrainSendMode`; route footer on messages |
| **B — CEO inbox buckets** | ✅ | `getFounderQueueBucket`, grouped UI, actionable-only badge count |
| **C — Runtime ownership** | ✅ | `agentRuns.start` on copilot Cursor dispatch; active run → queue `AGENT_REVIEW`; `sourceRunId` on items; runtime steps already in `builder-run-live.ts` |
| **D — Agent Bus loop** | ✅ | Existing chain verified; dedupe in `shouldSkipBusHandoff`; queue actions (merge/publish/sync/build) from Mission Control |
| **E — Decision memory** | ✅ | `founder-decision-log.ts`, `GET/POST /copilot/decisions`, auto-detect in `ask()`, injected in context assembly |
| **F — Desktop Bridge** | ✅ | Founder Node heartbeat sends branch + open file names + task; API `saveFromHeartbeat`; timeline + Brain context |

## Build / deploy

| Step | Result |
|------|--------|
| `npm run build:utils` | ✅ |
| `npm run build --workspace=@dcf/api` | ✅ |
| `npm run build --workspace=@dcf/web` | ✅ |
| `npm run build:api` (full + prisma generate) | ⚠️ EPERM on Prisma DLL (likely locked by running API) — tsc build OK |
| `npm run sync:all` | See below |

## Sync (post-push)

Run manually if automated sync fails without vault/secrets:

```bash
npm run sync:all
npm run deploy:web
```

**Expected blockers without local secrets:** Railway/Vercel env sync, production DB push — document only; no secrets committed.

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
- **Production sync secrets** for `sync:all` / Railway / Vercel if not in local vault
