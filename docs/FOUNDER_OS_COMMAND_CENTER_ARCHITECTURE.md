# Founder OS — Command Center Architecture

**Audience:** product, Cursor agents, external review (safe to share; no secrets).

**North star:** [FOUNDER_OS_NORTH_STAR.md](./FOUNDER_OS_NORTH_STAR.md)

This doc merges:

1. The **four-layer stack** (Context → Intelligence → Runtime → Actions)
2. The **six OS differentiators** (Agent Bus, Founder Queue, Attention Center, single Brain, Timeline, Deploy Intelligence)
3. What **CodeGrid** teaches us (and what we do *not* copy)

---

## Verdict

| If we ship only… | You get… |
|------------------|----------|
| Context + Mission + Runtime + Actions | A **much smarter dashboard** with in-chat builds |
| **+ the six additions below** | A **founder operating system** |

The bottleneck is correct: **not** Railway/Vercel/GitHub — **Context → Intelligence → Runtime → Actions**, then **orchestration between agents**.

---

## Target architecture

```text
                    ┌─────────────────────┐
                    │   Founder Brain     │  ← only AI the user talks to
                    │   (chat / voice)    │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │    Intent Router     │  packages/utils founder-brain-router
                    └──────────┬──────────┘
           ┌───────────────────┼───────────────────┐
           ▼                   ▼                   ▼
    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
    │  Research   │    │   Builder   │    │   Content   │   workers (invisible labels)
    │   worker    │    │   worker    │    │   worker    │
    └──────┬──────┘    └──────┬──────┘    └──────┬──────┘
           │                   │                   │
           └───────────────────┼───────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │      Agent Bus        │  NEW — typed events, handoffs
                    └──────────┬──────────┘
                               │
         ┌─────────────────────┼─────────────────────┐
         ▼                     ▼                     ▼
  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
  │ Mission State│    │ Founder Queue│    │  Attention   │
  │   Engine     │    │  (CEO inbox) │    │   Center     │
  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘
         │                     │                     │
         └─────────────────────┼─────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Project Timeline   │  commits + ships + deploys + decisions
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ Deploy Intelligence │  outcome, impact, risk — not “deployed”
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ Control Actions     │  merge PR, redeploy, publish X
                    └─────────────────────┘
```

---

## Layer 1 — Context Assembly (P0 ✅ v1)

**Problem:** Brain reads `goal` / `task` / `launch_readiness` and ignores real work.

**Solution:** Assemble before every LLM call:

- Project description, roadmap, open tasks  
- Last 40 commits, PRs, deploy events  
- Mission graph, vault, workspace activity  
- Initiative themes (Feed, Discover, Founder OS, Vault, Builder, …)

**Code today:**

- `packages/utils/src/founder-brain-context.ts`
- `packages/utils/src/commit-intelligence.ts`
- `apps/api/src/events/founder-copilot.service.ts` → `ask()`

**v2:** Decision journal, last N chat turns, PR diff summaries, timeline slice.

---

## Layer 2 — Mission State Engine (P0 ✅ v1)

**Problem:** Static PM fields feel like Jira.

**Solution:** Derived cockpit fields:

| CEO field | Sources |
|-----------|---------|
| Current initiative | Commit themes + sprint + roadmap |
| Progress | Ships + PRs + readiness |
| Blocker | Open PRs, failed runs, `blocked_by` |
| Impact | Outcome lines from commits |
| Next step | Queue + graph + deploy state |
| Confidence | Signal count |

**Code today:**

- `deriveMissionIntelligence()` in `founder-brain-context.ts`
- `GET /copilot/mission-intelligence`
- Mission Control right rail

**v2:** Auto-write graph patches after sync/build; never overwrite founder-edited fields without merge.

---

## Layer 3 — Agent Runtime (P1 — critical)

**Problem:** `Open full session in Cursor` = OS does not own execution.

**Solution:** Founder OS owns an **AgentRun**:

```text
runId, worker (CURSOR|OPENHANDS), steps[], status, artifacts{branch, prUrl, deployUrl}
```

**User-visible steps (trust):**

```text
1. Reading repository
2. Creating plan
3. Editing files
4. Generating diff / commit
5. Opening PR
6. Deploying (optional)
7. Finished
```

Map Cursor Cloud statuses → steps; stream into chat (existing poll → formalize).

**Code today:** `pollCursorRunInChat`, `formatBuilderRunInChat` in `apps/web/src/lib/builder-run-live.ts`

**Do not copy from CodeGrid:** 2D canvas of terminal sessions — we need **one command center**, not dozens of panes.

---

## Layer 4 — Control Actions (P1)

**Problem:** User leaves OS to merge, deploy, publish.

**Solution:** Action buttons on queue items and run completion:

- Approve / merge PR (GitHub API)  
- Trigger Vercel/Railway redeploy (existing autopilot paths)  
- Publish founder update / X (Social Hub)

**Code today:** `FounderAutopilotService`, `publishSuggestedUpdate`, builder dispatch

---

## The six OS differentiators

### 1. Agent Bus (from CodeGrid — the real lesson)

**CodeGrid insight:** Value is **agent ↔ agent** handoffs, not grid UI.

**Today:** Founder talks to Research, then separately to Builder, then Content — no chain.

**Target:**

```text
RESEARCH_FINDING  →  enqueue BUILD_PROPOSAL  →  Builder worker
BUILD_COMPLETE    →  enqueue DRAFT_UPDATE    →  Content worker
CONTENT_READY     →  enqueue ATTENTION_ITEM →  Founder Queue
```

**Implementation sketch:**

| Piece | Approach |
|-------|----------|
| Transport | Extend `FounderEvent` + new `AgentBusMessage` table OR reuse `events.emit` with typed `payload.kind` |
| Router | `AgentBusService` subscribes on emit; idempotent `dedupeKey` |
| Rules | Declarative map in `packages/utils/src/agent-bus.ts` (v1: 3 rules) |

**Example rule:**

```typescript
// research.completed + tag "competitor" → create build queue item + notify Attention
```

**Not in v1 yet** — design now, ship after Agent Runtime has stable `runId`.

---

### 2. Founder Queue (CEO inbox — not task manager)

**Today:** `BuildQueueItem` (IDEA/TASK) — backlog, not **action inbox**.

**Target:** `FounderQueueItem` with:

| Field | Example |
|-------|---------|
| `kind` | `REVIEW_PR` \| `APPROVE_DEPLOY` \| `PUBLISH_UPDATE` \| `RESOLVE_MARKET` \| `AGENT_WAITING` |
| `title` | Review Feed redesign PR |
| `action` | `merge_pr` \| `open_url` \| `publish` \| `dispatch_build` |
| `priority` | 1–5 |
| `expiresAt` | scout vote deadline |
| `sourceRunId` | link to Agent Runtime |

**UI:** Right rail above Mission State — “Needs you (4)”.

**Derivation (v1, no new table):** Compute queue from:

- Open PRs (GitHub)  
- Pending `SuggestedBuildUpdate`  
- Builder run awaiting approval (`CREATING` / terminal ERROR)  
- Scout markets expiring (prediction API)  
- Unread `Notification` filtered to actionable types only  

Endpoint: `GET /copilot/founder-queue` (computed view first; persist later).

---

### 3. Attention Center (actionable notifications)

**Today:** `Notification` model — generic “4 notifications”.

**Target:** **Needs Attention** — every row requires a verb:

```text
Builder waiting for approval     [Review run]
PR #42 ready for review          [Open PR]
Deployment failed on Railway     [Redeploy]
Scout market dispute #7          [Vote]
```

**Implementation:**

- New `AttentionItem` type in utils  
- `AttentionService.aggregate(userId)` from PRs, runs, deploys, markets, queue  
- Replace badge count on nav with attention count  
- Hide non-actionable types from founder OS shell (points earned → ledger only)

---

### 4. Single Founder Brain (UI)

**Rule:** User never picks DeepSeek vs Builder vs Research in Mission Control.

| Surface | Shows |
|---------|--------|
| Chat header | Founder Brain |
| Settings only | API keys, Cursor connect |
| Message footer | “Routed: build” (optional, subtle) |

**Code today:** `classifyFounderBrainTask`, `getFounderBrainRouteLabel`, Command Center copy.

**Remove:** Agent picker on hero chat; keep Agents tab as **status**, not **mode switch**.

---

### 5. Project Timeline

**Problem:** Memory fragmented across GitHub, events, posts, graph.

**Target:** Unified chronological narrative:

```text
Jun 3 — Feed redesign started (12 commits)
May 29 — Builder Rewards launched (deploy)
```

**Sources:**

- `FounderEvent` (filtered: ships, merges, major deploys — not every commit)  
- Commit intelligence themes by week  
- `FounderBuildPost`, `FounderUpdate`  
- Mission graph snapshots (optional)

**Endpoint:** `GET /copilot/project-timeline?days=30`

**Brain:** Inject timeline excerpt into Context Assembly for “what happened this month?”

---

### 6. Deployment Intelligence

**Problem:** “Deployment shipped” is noise.

**Target:**

```text
Discover redesign → production (Vercel)
Impact: navigation refactor live
Affected: /discover, bubble engine
Risk: low (no schema migration)
Next: smoke /discover, publish founder update
```

**v1 heuristic (no LLM required):**

- Parse deploy event + recent commits on default branch  
- Map files/routes from commit messages (discover, feed, founder)  
- Risk: `migration` / `breaking` keywords → medium/high  

**v2:** LLM summary on deploy webhook.

---

## Surface separation (non-negotiable)

| Surface | Answers |
|---------|---------|
| **Feed** (`/feed`) | Where is money flowing? |
| **Founder OS** | What should I build/ship/approve next? |
| **Social Hub** | What do we publish? |
| **Discover** | What opportunities exist? |

**Enforced in:** `packages/utils/src/money-feed.ts`, `docs/SPRINT_7B_FEED.md`

---

## Desktop Bridge (P2)

**Do not sync:** file contents (privacy).

**Do sync:**

- Current branch  
- Open file paths (names only)  
- Current task label from IDE  
- Recent edit summary (hash + file count)  
- Agent status  

**Transport:** Founder Node heartbeat extension (already has vault sync).

---

## Build phases (recommended)

### P0 — Keep & harden (in progress)

- [x] Context Assembly v1  
- [x] Mission Intelligence v1  
- [x] Money Feed separation  
- [x] Command Center layout 70/30  
- [x] LLM + GitHub required in onboarding copy  
- [x] Feed leak audit on project pages  

### P1 — Feel like command center

1. **Agent Runtime** — `AgentRun` model + step UI  
2. **Founder Queue** — computed inbox endpoint + right rail UI  
3. **Attention Center** — aggregate actionable items  
4. **Hide multi-agent picker** — Brain-only chat  
5. **Agent Bus v1** — 3 handoff rules (research→build, build→content)  

### P2 — Feel like operating system

1. **Project Timeline** — API + Brain context ✅ v1  
2. **Deployment Intelligence** — outcome cards on deploy ✅ v1 heuristic  
3. **Desktop Bridge** — metadata-only IDE sync ✅ v1 (heartbeat `desktopBridge`)  
4. **Agent Bus v2** — persisted messages + custom rules (partial: dedupe shipped in P1.5)  

---

## What we steal from CodeGrid vs ignore

| CodeGrid idea | Founder OS |
|---------------|------------|
| Agent ↔ agent communication | **Agent Bus** ✅ |
| Many parallel sessions on canvas | ❌ — one command center, one primary run |
| Terminal-first UX | ❌ — browser-first; Cursor worker optional |
| Session grid | ❌ — Founder Queue + Attention instead |

Repo reference: [CodeGrid-Claude-Code-Terminal](https://github.com/ZipLyne-Agency/CodeGrid-Claude-Code-Terminal)

---

## How you know it worked

**Dashboard test:** Ask “what am I working on?” → get task title only.

**Command center test:** Same question → initiative name, 3 shipped outcomes, blocker, next approval in Founder Queue.

**OS test:** Research finds gap → Builder PR appears → Content draft queued → you approve merge and publish without leaving the tab.

---

## Related code map

| Concept | Location |
|---------|----------|
| Context / mission intel | `packages/utils/src/founder-brain-context.ts` |
| Brain routing | `packages/utils/src/founder-brain-router.ts` |
| Workforce agents | `packages/utils/src/founder-agents.ts` |
| Mission graph | `packages/utils/src/founder-memory-graph.ts` |
| Copilot API | `apps/api/src/events/founder-copilot.service.ts` |
| Mission Control UI | `apps/web/src/components/founder-os-dashboard.tsx` |
| Builder stream | `apps/web/src/lib/builder-run-live.ts` |
| Money feed | `packages/utils/src/money-feed.ts` |
| Notifications (to refactor) | `apps/api/src/notifications/`, `Notification` model |
