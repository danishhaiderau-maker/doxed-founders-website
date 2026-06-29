# Cursor Remote-Resume Feasibility

**Question:** Can Founder OS act as a true remote control for Cursor — listing the agent sessions currently open on a founder's home machine, reading their recent messages, and sending a follow-up prompt to a *specific existing* session (instead of starting a new run)?

**Bottom line:** **Yes for SDK-managed agents, with a hard requirement that the Cursor SDK runs on the founder's machine inside Founder Node. The Cursor IDE GUI is not a server — it exposes no remote API of its own.** The cloud Cursor API only covers cloud agents (`bc-`-prefixed). Local agents (the "6 Cursor agents open at home" scenario) require a local SDK process on the founder's machine that Founder OS can talk to. This maps cleanly onto the existing Founder Node desktop bridge.

---

## What the Cursor SDK exposes

Source: `@cursor/sdk` (TypeScript) / `cursor-sdk` (Python), public beta. Same `Agent` → `Run` model in both. The skill at `C:\Users\user\.cursor\skills-cursor\sdk\SKILL.md` is the source of truth.

### (a) LIST active Cursor agent sessions on a user's machine — **YES (with a caveat)**

```typescript
const localList = await Agent.list({ runtime: 'local', cwd: process.cwd() });
```

- Lists **local** agents known to the SDK, filtered by `cwd`.
- Caveat: this enumerates agents **created via the SDK** (and persisted in the SDK's local run store for that `cwd`). The skill does **not** document that agents opened manually in the Cursor IDE GUI are surfaced through `Agent.list`. Treat GUI-only sessions as **not guaranteed enumerable** until proven otherwise on the target machine.
- Cloud agents are listable via the REST API (`/v1/agents/*`) — but those are Cursor-hosted VMs, not the user's home desktop.

### (b) FETCH the last N messages from a specific Cursor agent session — **YES**

```typescript
const info = await Agent.get(agentId, { apiKey });
const run = await Agent.getRun(runId, { runtime: 'local', agentId, apiKey });
for await (const event of run.stream()) { /* assistant/user blocks */ }
// or, after the live stream has closed:
if (run.supports('conversation')) {
  const convo = await run.conversation(); // full message history
}
```

- `run.messages()` (Python) / `run.stream()` (TypeScript) yield typed SDK messages.
- `run.conversation()` returns the full conversation — guard with `run.supports('conversation')` because detached/rehydrated runs may not support every op.
- `run.unsupportedReason(op)` tells you why an op is unavailable.

### (c) SEND a follow-up prompt to an existing Cursor agent session — **YES**

```typescript
await using agent = await Agent.resume(previousAgentId, { apiKey });
const run = await agent.send('Also update the changelog');
await run.wait();
```

- `Agent.resume(previousAgentId)` rehydrates an agent across process boundaries. Runtime is auto-detected from the ID prefix (`bc-` = cloud, anything else = local).
- This is **the** primitive that makes "remote control your agent from anywhere" real: a follow-up keeps full conversation context, vs. `Agent.create(...)` which starts fresh.
- Inline MCP servers are **not** persisted across resume — pass them again on the resume call if the agent still needs them.
- `Agent.prompt(...)` is a one-shot that disposes for you — do **not** use it for the resume flow; it cannot be resumed.

---

## The hard constraint: where the SDK must run

The Cursor IDE GUI is a desktop application. It is **not** a server. It exposes no HTTP/WebSocket API that Founder OS (running in the cloud) can dial into directly. The Cursor SDK is the only programmatic surface, and:

- **Local agents** (the home-desktop scenario the product vision is built around) require the SDK to run **on that same home machine**, with access to the `cwd` where the agents were created and a `CURSOR_API_KEY`.
- **Cloud agents** (`bc-` prefix) are reachable from anywhere via the cloud API — but they run on Cursor-hosted VMs against cloned repos, not against the founder's live working tree.

So the bridge architecture is forced: **Founder Node (the desktop app) must embed the Cursor SDK and be the thing that lists/resumes agents locally.** Founder OS cloud talks to Founder Node, which talks to the Cursor SDK, which talks to the local agent store. There is no cloud-only shortcut for local agents.

---

## Bridge design (what we build)

```
Founder OS (cloud, Nest API)
        │  HTTPS  (existing desktop-bridge heartbeat channel)
        ▼
Founder Node (desktop, on the founder's home machine)
        │  @cursor/sdk  (local runtime, CURSOR_API_KEY, cwd = repo)
        ▼
Cursor local agent store  (Agent.list / Agent.resume / run.messages)
```

1. **Discovery (heartbeat):** Founder Node periodically calls `Agent.list({ runtime: 'local', cwd })` and includes the resulting agent list (id, label, last activity, status) in its existing heartbeat to `/desktop-bridge`. The cloud stores this alongside the existing `DesktopBridgeSnapshot`.
2. **Recent messages:** Founder Node calls `run.messages()` / `run.conversation()` for each listed agent and ships the last N (user prompt + assistant reply snippet) in the same heartbeat. Cloud keeps the last 5 agents × last 5 messages.
3. **Resume (send follow-up):** Founder OS cloud sends a **command** to Founder Node (existing command channel) carrying `{ agentId, prompt }`. Founder Node runs `Agent.resume(agentId)` + `agent.send(prompt)` + `run.wait()`, streams events back up. Cloud relays the stream to the web client.

This reuses the existing Founder Node ↔ cloud heartbeat/command plumbing — no new transport.

---

## Fallback (what we shipped today, while the SDK bridge is being wired)

Until Founder Node embeds `@cursor/sdk` and starts reporting real Cursor agent IDs, the home screen cannot truthfully say "here are your 6 open Cursor agents." We ship an honest fallback that still makes the home screen feel alive:

- **`GET /cursor-bridge/recent-agents`** aggregates:
  1. The active `FounderAgentRun` (the most recent agent dispatched *from* Founder OS — Cursor or OpenHands), with its `agentId`, `runId`, `status`, `task`, `branch`, `repository`.
  2. The latest `DesktopBridgeSnapshot` per node (branch, open files, agent status, task label, last-updated) — this is the live desktop signal.
  3. The last 5 `CURSOR_BUILD_SESSION` founder events (label + timestamp) — a history of recent Cursor dispatches.
- The web `RecentAgentsPanel` renders this honestly labeled as **"Recently dispatched from Founder OS"** when no live SDK agent list is present, and **"Live desktop agents"** when Founder Node reports real `Agent.list` output.
- The "Continue this agent" composer sends a follow-up via the **existing Cursor dispatch path** (`executeBuildTask`) when a real Cursor agent ID is not yet available. The moment Founder Node starts reporting SDK agent IDs, the same composer switches to `Agent.resume(agentId)` + `agent.send(prompt)` with no UI change.

This means the UI shell is correct today and the backend swap (dispatch → resume) is a one-function change once the SDK bridge lands.

---

## What is NOT possible (be honest)

- **Enumerating Cursor IDE GUI sessions that were never created via the SDK.** If the founder opened 6 agents by clicking around the Cursor GUI and never dispatched them through Founder OS or the SDK, `Agent.list` is not documented to surface them. We will verify on a real machine; until then, the "6 agents open at home" vision assumes Founder Node either (a) creates/owns those agents via the SDK, or (b) Cursor later exposes GUI sessions to `Agent.list`.
- **Cloud-only resume of a local agent.** Impossible without Founder Node. The cloud API only handles `bc-` cloud agents.
- **Reading a local agent's messages from the cloud without a local relay.** Same reason — the local run store is on the home machine.

---

## Open questions for the user

1. Are the "6 Cursor agents open at home" expected to be agents the founder launched **through Founder OS / Founder Node** (in which case we own the agent IDs and resume is trivial), or agents they launched **manually in the Cursor GUI** (in which case we depend on `Agent.list` surfacing GUI sessions, which needs a real-machine test)?
2. Do we want Founder Node to **create** the agents on the founder's behalf (so we always own the agent ID and can resume), even when the founder started the conversation in the GUI? This would mean "tap to resume" always opens a Founder-Node-owned continuation, not the literal GUI session.
3. Is `CURSOR_API_KEY` per-founder (BYOK, stored in `IntegrationCredential`) or a platform-shared key? The existing Cursor dispatch path already supports BYOK Cursor keys — reuse that.

---

## Recommendation

Ship the fallback now (done in this change). In parallel, add `@cursor/sdk` to `apps/founder-node`, implement `Agent.list` + `Agent.resume` + `run.messages` in the desktop process, and extend the existing heartbeat payload with a `cursorAgents[]` field. The cloud side already has the storage pattern (`DesktopBridgeSnapshot` in `memoryGraph` JSON) and the command channel — the SDK bridge is an additive change, not a redesign.
