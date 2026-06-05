# Founder OS Command Center — Proof Pack

**Safe to share.** No secrets, API keys, database URLs, or production tokens.

**Date:** 2026-06-05  
**Repo:** `danishhaiderau-maker/doxed-founders-website`  
**Sprint commit:** `1e6f1ce` — Implement Founder OS Command Center sprints A through F  
**Sync audit:** `d52bd89` — Founder OS A–F sync audit (2026-06-05)  
**Latest HEAD (at verification):** `80427d6` — chore(founder-os): sync tasks

**Related docs:** [North Star](./FOUNDER_OS_NORTH_STAR.md) · [Architecture](./FOUNDER_OS_COMMAND_CENTER_ARCHITECTURE.md) · [Sprint A–F status](./SPRINT_COMMAND_CENTER_A-F_STATUS.md) · [Audit guide](./AUDIT_FOR_CHATGPT.md)

---

## 1. Executive verdict

Founder OS is no longer a status dashboard or integration launcher. It is a **founder operating system**: one browser tab where you think, research, plan, build, deploy, and publish. The six sprints (A–F) shipped in commit `1e6f1ce` — single Founder Brain chat (no agent picker), CEO inbox buckets, Agent Runtime ownership, Agent Bus handoffs, decision memory, and Desktop Bridge metadata. Production sync was recorded in `d52bd89`; latest HEAD `80427d6` is synced with `origin/master`. Automated checks on 2026-06-05 confirm production web (200), API health (200 + JSON), full production smoke test (18/18), and local `@dcf/utils` + `@dcf/web` builds passing.

---

## 2. The three acceptance tests

These are the architecture doc’s pass/fail criteria. Use them to judge whether Founder OS feels like a dashboard or an OS.

### Dashboard test (fail case — what we moved past)

**Old behavior:** Ask “What am I working on?” and get only a static task title from `tasks.json` or a PM field — no initiative, no shipped outcomes, no blocker, no approval queue.

**What shipped:** Context Assembly + Mission State Engine inject commits, PRs, deploys, themes, and mission graph into every Brain call. Static task titles alone are never the answer when GitHub context exists.

**Manual verify (production, login required):**

1. Open [Founder Den](https://doxxedcrypto.digital/founder-den) → **Mission Control**.
2. Ask Founder Brain: **“What am I working on?”**
3. **Fail (dashboard):** response is only a task name with no initiative, outcomes, or queue.
4. **Pass (command center):** see current initiative, recent shipped outcomes, blocker, and next step — proceed to Command center test below.

---

### Command center test

**Target:** Same question → initiative name, ~3 shipped outcomes, blocker, next approval in Founder Queue.

**What shipped:**

- `deriveMissionIntelligence()` — dynamic initiative, progress, blocker, impact, next step, confidence
- `GET /copilot/mission-intelligence` + right-rail Mission State in Mission Control
- `GET /copilot/founder-queue` — computed CEO inbox with bucketed items (Attention, Review, Approval, Publishing, Deployment, Decision)

**Manual verify (production, login required):**

1. Mission Control → right rail: confirm **Mission intelligence** shows initiative, blocker, confidence.
2. Ask: **“What’s the status?”** — should route to research/ask (not auto-dispatch Builder).
3. Check **CEO inbox** right rail — buckets labeled *Needs Attention*, *Needs Review*, *Needs Approval*, etc., with actionable counts.
4. Response should reference real commit/PR/deploy themes, not boilerplate from `tasks.json`.

---

### OS test

**Target:** Research finds a gap → Builder PR appears → Content draft queued → you approve merge and publish **without leaving the tab**.

**What shipped:**

- Intent router (`founder-brain-router.ts`) — single Brain, invisible workers
- Agent Runtime — `agentRuns.start` on Cursor dispatch; steps stream in chat (`builder-run-live.ts`)
- Agent Bus v1 — research→build, build→content handoffs with dedupe (`agent-bus.ts`, `founder-command-center.service.ts`)
- Control actions on queue items — merge PR, publish, sync, dispatch build from Mission Control panels

**Manual verify (production, login + Cursor/GitHub connected):**

1. Ask: **“Fix discover page”** (or similar build intent) — should auto-route to Builder if Cursor is connected.
2. Watch in-chat steps: plan → branch → PR (no need to open Cursor as primary UX).
3. On build complete — Agent Bus follow-up + publish item in CEO inbox.
4. Use queue action buttons (merge / publish / sync) from the right rail without navigating away.

---

## 3. Sprint A–F proof matrix

| Sprint | Capability | Code location | How to verify in UI |
|--------|------------|---------------|---------------------|
| **A — Brain is the boss** | Single hero chat; no Ask/Build or provider picker; auto-route + route footer | `apps/web/src/components/founder-copilot-chat.tsx`, `packages/utils/src/founder-brain-router.ts`, `apps/web/src/lib/copilot-ai-stack.ts` | Mission Control: one input + Send only; message footer shows “Routed: …” |
| **B — CEO inbox buckets** | Grouped queue buckets, actionable badge count | `packages/utils/src/founder-queue.ts`, `apps/web/src/components/founder-command-center-panels.tsx` | Right rail **CEO inbox** — sections: Needs Attention, Review, Approval, Publishing, Deployment, Decision |
| **C — Runtime ownership** | `agentRuns.start` on dispatch; active run → `AGENT_REVIEW` queue item with `sourceRunId` | `apps/api/src/events/founder-copilot.service.ts`, `apps/web/src/lib/builder-run-live.ts` | Dispatch a build; see step stream in chat + “Agent review” item in inbox |
| **D — Agent Bus loop** | Handoff rules + dedupe; queue control actions | `packages/utils/src/agent-bus.ts`, `apps/api/src/events/founder-command-center.service.ts` | Complete research or build; follow-up queue item / chat note from bus; use merge/publish/sync buttons |
| **E — Decision memory** | Decision journal + Brain context injection | `packages/utils/src/founder-decision-log.ts`, `apps/api/src/events/founder-copilot.service.ts` | Say a decision aloud in chat; later ask “why did we choose …?” — Brain recalls it |
| **F — Desktop Bridge** | Founder Node heartbeat → branch, open files, task label | `apps/founder-node/src/sync-client.ts`, `apps/api/src/events/events.controller.ts` | Pair Founder Node; timeline / bridge card shows branch + file names + task (metadata only) |

---

## 4. Automated verification results (2026-06-05)

All commands run from repo root on Windows (PowerShell). Timestamps in local time (UTC+10) unless noted.

| Check | Command | Result | Summary |
|-------|---------|--------|---------|
| **Git HEAD** | `git log -1 --oneline` | ✅ | `80427d6 chore(founder-os): sync tasks` |
| **Branch sync** | `git fetch` + `git status -sb` | ✅ | `master...origin/master` — synced after fast-forward pull |
| **build:utils** | `npm run build:utils` | ✅ | `@dcf/utils` + `@dcf/founder-vault` — `tsc` OK (~19s) |
| **build:web** | `npm run build --workspace=@dcf/web` | ✅ | Next.js 15.5.18 — compiled successfully; 40 routes; `/founder-den` 30 kB |
| **Smoke (local)** | `npm run smoke:test` | ⚠️ | 18/18 FAIL — `http://localhost:4000` unreachable (API not running locally); expected when dev stack is off |
| **Smoke (production)** | `$env:API_URL="https://doxed-founders-website-production.up.railway.app"; npm run smoke:test` | ✅ | 18/18 OK — health, projects, feed, discover, vault, auth-gated routes, prediction markets |
| **Web production** | `curl.exe -w "HTTP %{http_code}" https://doxxedcrypto.digital` | ✅ | HTTP **200** — 0.66s |
| **Founder Den** | `curl.exe -I https://doxxedcrypto.digital/founder-den` | ✅ | HTTP **200** — HTML shell served (Vercel); full Mission Control requires browser login |
| **API health** | `curl.exe https://doxed-founders-website-production.up.railway.app/api/health` | ✅ | HTTP **200** — `{"status":"ok","services":{"api":"ok","database":"ok"},...}` |

**Production smoke output (abbreviated):**

```text
=== Smoke test: https://doxed-founders-website-production.up.railway.app ===
OK   health
OK   projects
OK   featured
OK   project-detail
OK   feed
OK   unified-feed
OK   feed-hub
OK   privacy-data-classes
OK   vault-cvm-capabilities
OK   vault-cvm-seal-capabilities
OK   discover-universe
OK   platform-pulse
OK   adoption-metrics
OK   reputation-leaderboard
OK   account-api (auth required)
OK   cursor-api (auth required)
OK   prediction-markets
OK   reset-info
All smoke checks passed.
```

**API health JSON (2026-06-05T04:54:03Z):**

```json
{"status":"ok","timestamp":"2026-06-05T04:54:03.364Z","services":{"api":"ok","database":"ok"},"pendingListings":0}
```

---

## 5. Production URLs

| Surface | URL | Role |
|---------|-----|------|
| **Web** | https://doxxedcrypto.digital | Next.js (Vercel) — public site + Founder Den |
| **Founder Den / Mission Control** | https://doxxedcrypto.digital/founder-den | Founder OS command center (login for full copilot) |
| **API health** | https://doxed-founders-website-production.up.railway.app/api/health | NestJS (Railway) — liveness + DB check |

---

## 6. 10-minute manual walkthrough script

Do this **right now** in production. Steps marked 🔐 require founder login.

| Step | Action | Expected |
|------|--------|----------|
| 1 | Open https://doxxedcrypto.digital — confirm landing loads | Public home, no errors |
| 2 | 🔐 Go to https://doxxedcrypto.digital/founder-den | Mission Control layout: chat left (~70%), intelligence + inbox right (~30%) |
| 3 | 🔐 Confirm **one** chat input — no Ask/Build toggle, no agent/provider picker on hero | Sprint A |
| 4 | 🔐 Type in Founder Brain: **“What’s the status?”** | Routes to research/ask; answer includes initiative + outcomes + blocker (not task title only) |
| 5 | 🔐 Scan right rail **CEO inbox** buckets | Grouped: Attention, Review, Approval, Publishing, Deployment, Decision |
| 6 | 🔐 Type: **“Fix discover page”** (skip if Cursor not connected) | Auto-dispatch build; in-chat step stream; optional `AGENT_REVIEW` inbox item |
| 7 | 🔐 Decision memory test — say: **“We decided to ship feed first because traction is higher”** | Brain acknowledges / logs decision |
| 8 | 🔐 Ask: **“Why did we choose feed?”** | Brain recalls decision from journal (Sprint E) |
| 9 | Optional: pair **Founder Node** | Desktop bridge metadata in timeline (branch, file names, task) |
| 10 | Open https://doxxedcrypto.digital/feed in new tab | Money/capital flow only — build/commit noise stays in Founder OS |

---

## 7. Before vs After

| Dimension | Before (dashboard) | After (command center / OS) |
|-----------|-------------------|----------------------------|
| **Primary UX** | Status panels + “open Cursor” links | Single Founder Brain chat owns the loop |
| **“What am I working on?”** | Static task / PM field | Initiative + shipped outcomes + blocker + queue |
| **Notifications** | Generic badge count | CEO inbox buckets — every row has a verb |
| **Agents** | User picks Research vs Builder vs Content | System routes; user sees one Brain |
| **Build flow** | Leave tab for Cursor / GitHub | Steps stream in chat; PR/merge/publish from queue |
| **Memory** | Fragmented across GitHub, events, posts | Decision journal + timeline + context assembly |
| **Desktop** | No IDE awareness | Founder Node bridge (metadata only, privacy-safe) |
| **Feed** | Mixed build + money signal | `/feed` = capital flow only; builds in Founder OS |

---

## 8. What still requires founder config (optional, not blockers)

These do **not** block proving the command-center architecture; they unlock live builds and full queue population.

| Config | Where | Unlocks |
|--------|-------|---------|
| **Cursor / OpenHands API keys** | Settings → AI stack | Live “Fix discover page” build dispatch + Agent Runtime steps |
| **GitHub repo linked** | Settings / onboarding | PR review queue, grounded status, merge actions |
| **Founder Node pairing** | Founder Node desktop app | Desktop Bridge card (branch, open files, task) |
| **Showcase exchange credentials** | Admin (optional) | BTC showcase bot demo |

---

## Re-run verification

```powershell
# Local builds
npm run build:utils
npm run build --workspace=@dcf/web

# Production smoke (PowerShell)
$env:API_URL="https://doxed-founders-website-production.up.railway.app"
npm run smoke:test

# Production health
curl.exe -s https://doxed-founders-website-production.up.railway.app/api/health
```

Full deploy pipeline: `npm run sync:all` (requires operator vault access; not included in audit export).

---

*Generated 2026-06-05 as part of the Founder OS command center proof pack.*
