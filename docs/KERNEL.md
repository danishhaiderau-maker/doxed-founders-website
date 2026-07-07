# Founder OS — Kernel Architecture Definition

| Field | Value |
|-------|-------|
| **Status** | DRAFT |
| **Authored** | 2026-07-07 |
| **Owners** | Founder · Head of Eng |

> This is the engineering reference. It defines the kernel boundary, the services inside it, and the discipline that keeps it tiny. Read alongside [`PRODUCT.md`](./PRODUCT.md) (the constitution) and [`BILLING.md`](./BILLING.md) (the revenue thesis).

---

## 1. What Founder OS is

Founder OS is an Operating System for Building Companies. It sits above AI infrastructure and below applications, exposing capabilities through a kernel. Applications — Raise Room, Marketplace, Founder Brain, the Workspace shell — call kernel services. Kernel services never call applications.

The boundary matters. AI models come and go (GLM today, DeepSeek today, Claude today, GPT today, whatever exists in 2028 tomorrow). Editors come and go (Cursor today, Claude Code today, OpenHands today, something else next year). What persists — and what the kernel owns — is the operational intelligence layer: memory of what the founder is building, routing decisions based on capability not vendor, flight records of every choice the system made, and learning loops that make tomorrow's routing smarter than today's. The kernel is the part of Founder OS that gets more valuable the longer a founder uses it.

---

## 2. The four layers

```text
                    Founder OS

                    Applications
────────────────────────────────────────────────────
Founder Brain
Raise Room
Workspace
Observatory
Marketplace
DDollar UI
Founder Den
Agent Hub

────────────────────────────────────────────────────
                  Founder OS Kernel
────────────────────────────────────────────────────
AI Gateway
Routing Engine
Memory Engine
Execution Engine
Flight Recorder
Learning Engine
DDollar Engine
Auth + Vault
Capability Registry
Founder Intent Engine

────────────────────────────────────────────────────
                   Execution Targets
────────────────────────────────────────────────────
Cursor
Claude Code
OpenHands
VS Code
Terminal
Git
Docker
Browser
Filesystem

────────────────────────────────────────────────────
                    AI Infrastructure
────────────────────────────────────────────────────
GLM
DeepSeek
Claude
GPT
Gemini
Kimi
Qwen
Whatever exists in 2028
```

---

## 3. Kernel services

The kernel contains exactly ten services. If something is not in this list, it is an application and belongs above the kernel boundary.

1. **AI Gateway** — OpenAI-compatible entry point (`/v1/chat/completions`, `/v1/models`, `/v1/usage`). Authenticates via Founder Node tokens. Already shipped (`apps/api/src/ai-proxy/`).
2. **Routing Engine** — 3-layer routing: cache lookup → capability gate → intent + cost-latency scoring. Replaces the existing `ModelRouterService` (`apps/api/src/founder-ai-runtime/model-router.service.ts`).
3. **Memory Engine** — four memory stores: Conversation, Project, Founder, Workspace. Memory is company knowledge, not chat history.
4. **Execution Engine** — orchestrates across execution targets (Cursor, Claude Code, OpenHands, VS Code, Terminal, Git, Docker, Browser, Filesystem) via adapters.
5. **Flight Recorder** — logs every routing decision with full metadata for debugging and future Learning Engine training. Stored in the `RoutingDecision` Prisma model.
6. **Learning Engine** — consumes Flight Recorder outcomes, adapts routing weights, builds per-founder and per-project model reputation. Updates `Capability.successRate` / `Capability.retryRate`.
7. **DDollar Engine** — already shipped (`apps/api/src/ddollar/`). Spending, Reward, AntiAbuse, BuilderScore. See [`DDOLLAR_ECONOMY.md`](./DDOLLAR_ECONOMY.md).
8. **Auth + Vault** — Twitter auth, Founder Node tokens, encrypted credential vault for integration keys.
9. **Capability Registry** — Prisma `Capability` table. Stores model attributes (Tool Use, JSON, Large Context, Vision, cost, latency, intent scores) instead of hardcoding model names. Broadened to include non-LLM capabilities (OCR, Browser, SQL, Embedding).
10. **Founder Intent Engine** — Phase 5+. Goal → Task → Execution Graph decomposition.

---

## 4. Kernel boundary rules

The discipline that keeps the kernel tiny. Future engineers must read these.

1. **Kernel never depends on applications.** No kernel code imports from `apps/api/src/raise-room/`, `apps/api/src/marketplace/`, etc. Applications import kernel services; never the reverse.
2. **No kernel code references a provider key or model name by string.** All model attributes flow through the `Capability` Prisma table. Hardcoding `"glm-5.2"` or `"deepseek-coder"` in kernel code is a code-review reject.
3. **Every kernel service follows Input → Decision → Output.** See [`PRODUCT.md`](./PRODUCT.md) §5.
4. **The kernel stays tiny.** If a piece of functionality does not belong in the kernel list above, it does not go in the kernel. Raise Room, Marketplace, Founder Brain, Observatory — those are applications.
5. **Kernel services are independently testable.** Each exposes a clear interface and can be unit-tested without booting the full platform.

---

## 5. The Input → Decision → Output contract

Restated from [`PRODUCT.md`](./PRODUCT.md) §5 with a worked example for Routing:

```
Input:  CapabilityRequest { intent: "code", tokens: 4500, profile: "balanced" }
Decision: score([glm-5.2, deepseek-coder, kimi-coder])
          → glm-5.2 (0.92), deepseek-coder (0.81), kimi-coder (0.74)
Output: ExecutionPlan { provider: "glm", model: "glm-5.2", cacheKey: "abc123" }
```

Every kernel service has this shape. The Decision step is pure (no side effects). The Output is observable (logged to Flight Recorder if relevant).

---

## 6. Routing Engine v2 detail

The 3-layer pipeline. This is the immediate Phase 1 build.

```
Request arrives at AI Gateway
        ↓
Layer 1: Cache Lookup
  - Hash the normalized prompt prefix
  - If cache hit → return cached response (bypass LLM call entirely)
  - If miss → continue
        ↓
Layer 2: Capability Gate
  - Look up required capabilities (Tool Use? JSON mode? Large Context? Vision?)
  - Filter providers/models that DON'T meet requirements
        ↓
Layer 3: Intent + Cost-Latency Score
  - Classify intent (code / reasoning / simple_qa / agent / vision)
  - Apply Execution Profile weights (Turbo / Balanced / Architect / Autonomous)
  - Score remaining candidates on cost × latency × historical success
  - Pick highest score
        ↓
Log to Flight Recorder
        ↓
Invoke provider
```

---

## 7. Execution Profiles

Workspace-scoped router settings. Stored per workspace, override the global routing weights.

| Profile | Optimizes for | When to use |
|---------|---------------|-------------|
| **Turbo** | Speed + low DDollar cost | Quick edits, simple Q&A |
| **Balanced** | Default routing | Day-to-day coding |
| **Architect** | Deep reasoning quality | Planning, refactors, hard bugs |
| **Autonomous** | Allow expensive multi-step agent execution | Overnight runs, agent tasks |

---

## 8. Capability Registry shape (Phase 1)

The Prisma `Capability` model is the data backbone of routing. Don't write the migration yet, but spec the shape:

```
model Capability {
  id              String   @id @default(cuid())
  provider        String   // "glm", "deepseek", "claude", etc.
  model           String   // "glm-5.2", "deepseek-coder-v2", etc.
  displayName     String
  isActive        Boolean  @default(true)

  // Capabilities (boolean flags)
  toolUse         Boolean  @default(false)
  jsonMode        Boolean  @default(false)
  largeContext    Boolean  @default(false) // >128K
  vision          Boolean  @default(false)
  streaming       Boolean  @default(true)

  // Costs (per 1M tokens, USD)
  inputCostPer1M  Float
  outputCostPer1M Float

  // Latency (ms, p50)
  latencyP50Ms    Int

  // Intent scores (0..1, how good for this intent)
  codeScore       Float    @default(0.5)
  reasoningScore  Float    @default(0.5)
  simpleQaScore   Float    @default(0.5)
  agentScore      Float    @default(0.5)
  visionScore     Float    @default(0.0)

  // Reputation (updated by Learning Engine)
  successRate     Float    @default(1.0)
  retryRate       Float    @default(0.0)

  createdAt       DateTime @default(now())
  updatedAt       DateTime @updatedAt
}
```

The reputation columns (`successRate`, `retryRate`) are owned by the Learning Engine — see §10 and Phase 4 in [`PRODUCT.md`](./PRODUCT.md) §6.

---

## 9. Flight Recorder shape (Phase 1)

The `RoutingDecision` Prisma model. Every routing decision logs one row.

```
model RoutingDecision {
  id              String   @id @default(cuid())
  requestId       String   // correlation ID
  userId          String
  workspaceId     String?

  intent          String   // "code" / "reasoning" / etc.
  profile         String   // "turbo" / "balanced" / etc.

  candidates      Json     // array of { provider, model, score } pre-selection
  chosenProvider  String
  chosenModel     String

  cacheLevel      String   // "hit" / "partial" / "miss"
  cacheKey        String?

  promptHash      String   // SHA-256 of normalized prompt prefix
  tokenCountPrompt     Int
  tokenCountCompletion Int

  latencyMs       Int
  costUsd         Float    // computed

  // Outcome signals (filled in async after response)
  accepted        Boolean? // user accepted the response
  retried         Boolean? // user retried within 60s
  edited          Boolean? // user edited the response heavily
  rating          Int?     // 1-5 if collected

  createdAt       DateTime @default(now())
}
```

The outcome-signal columns are the training data the Learning Engine consumes in Phase 4.

---

## 10. Component state classification

| Component | Layer | State | Notes |
|-----------|-------|-------|-------|
| AI Gateway | Kernel | ✅ Shipped | `apps/api/src/ai-proxy/` |
| Founder Node token auth | Kernel | ✅ Shipped | `apps/api/src/founder-node/` |
| DDollar Engine | Kernel | ✅ Shipped | `apps/api/src/ddollar/` |
| ModelRouterService v1 | Kernel | ⚠ Replaced | Becomes Routing Engine v2 in Phase 1 |
| Routing Engine v2 | Kernel | 🚧 Phase 1 | 3-layer pipeline |
| Capability Registry | Kernel | 🚧 Phase 1 | Prisma `Capability` model |
| Flight Recorder | Kernel | 🚧 Phase 1 | Prisma `RoutingDecision` model |
| Execution Profiles | Kernel | 🚧 Phase 1 | Per-workspace router overrides |
| Memory Engine | Kernel | 🚧 Phase 1+ | Skeleton in Phase 1, fills in Phases 2-4 |
| Execution Engine | Kernel | 🚧 Phase 3 | Cursor adapter first |
| Learning Engine | Kernel | 🚧 Phase 4 | Updates Capability successRate |
| Founder Intent Engine | Kernel | 🚧 Phase 5 | Goal → Task → Execution Graph |
| Auth + Vault | Kernel | ⚠ Partial | Twitter + Founder Node done; integration vault exists |
| Raise Room | App | ⚠ Partial | Existing, matures in Phase 7+ |
| Marketplace | App | 🚧 Spec | Phase 6+ |
| Founder Brain | App | ⚠ Partial | Reasoning UI |
| Founder Den | App | ⚠ Partial | Personal dashboard |
| Observatory | App | 🚧 Phase 2+ | System visibility |
| Agent Hub | App | 🚧 Phase 3+ | |
| Workspace / Founder OS Shell | App | 🚧 Phase 2 | The thin shell with two CTAs |
| Token Launch + DEX | App | 🚧 Phase 7+ | See [`RAISE_ROOM_LAUNCH_FLOW.md`](./RAISE_ROOM_LAUNCH_FLOW.md) |

---

## Change log

| Date | Change |
|------|--------|
| 2026-07-07 | Initial draft. |
