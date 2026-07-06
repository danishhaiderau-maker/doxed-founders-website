# Founder Brain — AI Operating System Specification

**Status:** Active roadmap · July 2026  
**Audience:** Engineering, product, agents  
**Supersedes framing:** Generic “AI Runtime” → **Founder Brain / AI OS**  
**Related:** [FOUNDER-AI-RUNTIME-SPEC.md](./FOUNDER-AI-RUNTIME-SPEC.md) (Phase 0 implementation), [AI-TOKEN-OPTIMIZATION-QUICK-WINS.md](./AI-TOKEN-OPTIMIZATION-QUICK-WINS.md), [PLATFORM-READINESS-PLAN.md](./PLATFORM-READINESS-PLAN.md) Slice 5

---

## 1. North star

Founder OS is not chat-with-an-LLM. It is a **local-first AI operating system** where:

- Every AI call flows through **Founder Brain → AI Runtime → Router → Memory → Prompt Compiler → Cache → Providers**
- **Cost is measured per completed task**, not raw tokens
- **Founder Node** handles deterministic work (grep, git diff, lint, vault search) before any cloud LLM
- Context is **decisions and workspace state**, not unbounded chat history

```
User intent
    → Confidence Router (tool-first? cache? tier?)
    → Local Founder Node (L0 tools)
    → Multi-level cache (L2–L7)
    → Capability tier (Fast / Coding / Reasoning)
    → Provider (cloud or Ollama via Node)
    → Task completion + cost attribution
```

---

## 2. Core metric: cost per completed task

| Metric | Definition |
|--------|------------|
| **Task** | A user-visible outcome: copilot answer, wall summary, share paraphrase, code draft, deploy check |
| **Completed** | Response validated + delivered (or cache hit with same outcome) |
| **Cost** | `estimatedUsd` or DDollar debit for that task, including retries |
| **Efficiency** | `costPerCompletedTask = sum(cost) / count(completed)` by section, tier, user tier |

Token counts remain useful for debugging; **dashboards and budgets use task economics**.

Telemetry fields (Phase 1+): `cacheLevel`, `localToolUsed`, `confidenceScore`, `intent`, `tier`, `taskId`.

---

## 3. Capability tiers (not hardcoded models)

| Tier | Use when | Typical signals | Env override |
|------|----------|-----------------|--------------|
| **Fast** | Short Q&A, social drafts, summaries | `<120 chars`, hello/status, `share_paraphrase`, `wall_summarizer` | `AI_RUNTIME_FAST_MODEL` |
| **Coding** | Implementation, refactor, schema | `code` task, implement/refactor/debug | `AI_RUNTIME_CODE_MODEL` |
| **Reasoning** | Strategy, regulatory, architecture | why/explain/compare, compliance | `AI_RUNTIME_REASONING_MODEL` |

Models are **configuration**, not product identity. UI always shows **Founder Brain · {task}**, never vendor names.

---

## 4. Ten missing pieces → phased delivery

| # | Capability | Phase | Status (Jul 2026) |
|---|------------|-------|-------------------|
| 1 | **Workspace Intelligence** | 2 | Partial — `FounderMemoryGraphService`, build-graph utils |
| 2 | **Local First** | 2–3 | Partial — Founder Node Ollama + sync jobs; tool router stub |
| 3 | **Multi-Level Cache** | 1–2 | L2 prompt hash ✓; L5 semantic + L6 tool planned |
| 4 | **Confidence Router** | 2 | Intent router ✓; confidence scores planned |
| 5 | **Learning Router** | 3 | Not started — feedback loop on task outcomes |
| 6 | **Cost Optimizer dashboard** | 3 | Adoption chart exists; task-cost view planned |
| 7 | **AI Budget** | 2–3 | Rate limits + DDollar; per-founder budget caps planned |
| 8 | **Dynamic Context** | 2 | Context pruning ✓; decision graph not chat |
| 9 | **Planning Engine** | 4 | Builder dispatch partial; multi-step planner planned |
| 10 | **Founder Graph** | 2–4 | Memory graph partial; platform-wide graph planned |

---

## 5. Multi-level cache

| Level | Name | Scope | Phase |
|-------|------|-------|-------|
| L0 | **Tool / deterministic** | Git status, deploy health, vault search | 2 |
| L1 | **CDN / edge** | Public help, marketing | 2 |
| L2 | **Prompt hash (API)** | SHA-256 section + user + system + prompt | **0 ✓** |
| L3 | **Provider prefix** | Stable system blocks to provider native cache | 2 |
| L4 | **Redis shared** | Cross-instance L2 | 1 |
| L5 | **Semantic** | Embedding similarity ≥ 0.95 | 2 |
| L6 | **Tool result** | Railway/Railway health without LLM | 2 |
| L7 | **Memory layers** | Global → Workspace → Project → Conversation | 2 |

---

## 6. Phase roadmap

### Phase 1 — Gateway + economics (done / partial)

- [x] Prompt hash cache (`PromptCacheService`)
- [x] Model / intent router (`ModelRouterService`)
- [x] Pilot: Copilot non-stream (`AI_RUNTIME_ENABLED`)
- [x] Context pruning + output token caps (`ContextBuilderService`)
- [x] Runtime on `share_paraphrase`, `wall_summarizer`
- [x] `AiTokenUsageLog` telemetry: `cacheLevel`, `localToolUsed`, `confidenceScore`
- [ ] Mandatory gateway — zero direct `fetch` in app code
- [ ] Redis backend (`REDIS_URL`)
- [ ] Prompt compiler (`config/founder-ai/`)
- [ ] Cost per task aggregation job

### Phase 2 — Workspace graph + local-first

- [ ] Founder Graph: project + repo + decision nodes
- [ ] Context builder: git diff summary only (not full repo)
- [ ] Semantic cache (pgvector, section-scoped)
- [ ] Confidence router: route to L0 tool when score > threshold
- [ ] Tool-first orchestrator (Founder Node grep/git/lint before LLM)
- [ ] Dynamic memory: decisions + milestones, not raw chat
- [ ] AI budget caps per founder tier

### Phase 3 — Learning + optimization

- [ ] Learning router: promote/demote tiers from task feedback
- [ ] Auto optimization: A/B prompt templates, cache TTL tuning
- [ ] **Cost Optimizer dashboard** — cost per completed task by section/tier
- [ ] Admin alerts: spend spike, cache miss rate, abuse

### Phase 4 — Multi-agent OS

- [ ] Planning engine: decompose goals → subtasks → workers
- [ ] Multi-agent coordination (researcher, builder, summarizer)
- [ ] Batch + background workers (domain event bus)
- [ ] Unified telemetry: cloud + Founder Node + showcase bot

---

## 7. Local-first (Founder Node)

Founder Node (`apps/founder-node/`) already provides:

- Ollama inference queue
- Vault sync, Cursor/Claude discovery
- Sync jobs: `VAULT_SEARCH`, `RUN_AGENT`, `PUSH_GOAL`

**Phase 2 target:** API `ToolRouter` sends symbol search / git diff / lint to Node **before** LLM. Endpoint pattern: extend sync jobs or lightweight `/local/analyze` on Node heartbeat channel.

Billing: `billingSource=founder_os_local` — zero cloud tokens when L0 succeeds.

---

## 8. Module map

```
apps/api/src/founder-ai-runtime/
  founder-ai-runtime.service.ts   # Gateway: complete(), cache, route
  prompt-cache.service.ts         # L2 prompt hash
  model-router.service.ts         # Intent → tier
  context-builder.service.ts      # Pruning + max output tokens
  founder-ai-runtime.types.ts

apps/founder-node/                # Local inference + sync jobs
packages/utils/
  founder-brain-router.ts         # Task classification
  founder-brain-context.ts        # Mission intelligence
```

---

## 9. Feature flags

| Flag | Default | Effect |
|------|---------|--------|
| `AI_RUNTIME_ENABLED` | `false` | Prompt cache + pruning + output caps on wired sections |
| `AI_RUNTIME_CONTEXT_PRUNING` | `true` (when runtime on) | Trim oversized system/user prompts |
| `REDIS_URL` | unset | Phase 1 shared cache |

See [ENV-VARS.md](./ENV-VARS.md) for full list.

---

## 10. UI Principles: Provider-agnostic Founder Brain

Founder OS surfaces **Founder Brain** as the single product identity for AI — not Claude, DeepSeek, GLM, or other vendor names in default UI.

| Principle | Implementation |
|-----------|------------------|
| **No provider pickers in workspace** | Founder Den shows mode (`Automatic` / `Fast` / `Balanced` / `Deep Thinking`), not model dropdowns |
| **Automatic routing** | `brainMode=automatic` (default) → existing AI runtime router + cascade; no `forceProvider` |
| **Explicit modes** | `FOUNDER_BRAIN_MODES=automatic\|fast\|balanced\|deep` — maps to tier hints server-side (`fast`→GLM, `deep`→reasoning) |
| **Status panel** | Online dot, project/repo/workspace/context, AI Power %, Cost Saved Today (telemetry or estimate) |
| **Vendor config elsewhere** | Settings → AI Stack for BYOK and infra; never primary workspace chrome |
| **Chat attribution** | Messages show `Founder Brain`, not `via DEEPSEEK` |

Tooltip on mode selector: *Routes automatically by task complexity* (Automatic mode).

---

*Product engineering context only. No API keys in this document.*
