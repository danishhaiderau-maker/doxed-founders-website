# AI Token Optimization — Quick Wins

**Date:** 6 July 2026  
**Goal:** Ship token savings **this week** without quality regression  
**Primary metric:** Cost per completed task (see [FOUNDER-BRAIN-AI-OS-SPEC.md](./FOUNDER-BRAIN-AI-OS-SPEC.md))

---

## Priority matrix

| Priority | Win | Est. savings | Risk | Status |
|----------|-----|--------------|------|--------|
| P0 | Prompt hash cache hits (L2) | 15–40% on repeat asks | Low | **Shipped** (Phase 0) |
| P0 | Context pruning (system + user) | 10–25% on long copilot threads | Low | **Shipped** |
| P0 | Max output tokens per intent | 5–20% completion tokens | Low | **Shipped** |
| P1 | Wire runtime to share + wall | 10–30% on paraphrase/summary repeats | Low | **Shipped** |
| P1 | Wall transcript cap (40 msgs) | 20–50% on busy walls | Low | **Shipped** |
| P2 | Expand runtime to founder_draft, quick_build | 10–25% | Medium | Planned |
| P2 | Skip LLM when L0 tool succeeds | 30–80% on repo-status asks | Medium | Planned |
| P2 | Local-first: git diff via Founder Node | 40–70% on “what changed” asks | Medium | Planned |
| P3 | Streaming cache (partial response hash) | 5–15% | High | Backlog |
| P3 | Semantic cache (L5) | 20–35% | Medium | Phase 2 |

**Combined estimate (flag on, 3 sections wired):** ~25–45% token reduction on typical demo/production mix; **~60–90%** on exact repeat prompts (cache hit).

---

## Bypass paths still calling LLM directly

From [FOUNDER-AI-RUNTIME-SPEC.md](./FOUNDER-AI-RUNTIME-SPEC.md) §5 audit — **wire order for this week:**

| # | Feature | File | Runtime wired? |
|---|---------|------|----------------|
| 1 | Copilot ask (sync) | `builder.service.ts` | ✓ cache + prune |
| 2 | Copilot ask (stream) | `builder.service.ts` | ✗ skipCache |
| 3 | Copilot social draft | `founder-copilot.service.ts` | ✗ via copilot |
| 4 | Quick Build | `builder.service.ts` | ✗ |
| 5 | Founder draft / updates | `builder.service.ts` | ✗ |
| 6 | Founder Den ask brain | `founder-den.service.ts` | ✗ |
| 7 | Share paraphrase | `share.service.ts` | **✓ this PR** |
| 8 | Wall summarizer | `wall.service.ts` | **✓ this PR** |
| 9 | AI Routing admin test | `ai-invoker.service.ts` | N/A |
| 10 | Builder BYOK verify | `builder.service.ts` | N/A (ping) |
| 11 | Phala / Jatevo / Surplus clients | `builder/*.client.ts` | ✗ |
| 12 | Founder Node Ollama | `founder-node-inference.service.ts` | Local (not cloud) |
| 13 | Showcase BTC bot | external service | Out of scope |

**After this PR:** ~3 of 14 features through runtime when `AI_RUNTIME_ENABLED=true` (~79% still bypass).

---

## Shipped quick wins (code)

### 1. Context pruning (`ContextBuilderService`)

- Trims system prompt to `AI_RUNTIME_MAX_SYSTEM_CHARS` (default 12k) — keeps head (instructions) + tail (memory)
- Trims user prompt to `AI_RUNTIME_MAX_USER_PROMPT_CHARS` (default 16k)
- Disable: `AI_RUNTIME_CONTEXT_PRUNING=false`

### 2. Output token caps per intent

| Intent | Default max output |
|--------|-------------------|
| `simple_qa` | 512 |
| `social_draft` | 400 |
| `summarize` | 800 |
| `code` | 4096 |
| `reasoning` | 2048 |
| `unknown` | 1024 |

Override globally: `AI_RUNTIME_MAX_OUTPUT_TOKENS`  
Override per intent: `AI_RUNTIME_MAX_OUTPUT_{INTENT}` (e.g. `AI_RUNTIME_MAX_OUTPUT_CODE=8192`)

### 3. Runtime gateway on share + wall

Both use `FounderAiRuntimeService.complete()` — cache when flag on, invoke with capped tokens when miss.

### 4. Wall transcript window

`AI_RUNTIME_WALL_MAX_MESSAGES` (default 40) — deterministic trim before LLM; full 500-msg DB window unchanged for display.

### 5. Telemetry schema

`AiTokenUsageLog`: `cacheLevel`, `localToolUsed`, `confidenceScore` — enables cost-per-task dashboards.

---

## Planned this week (next agents)

1. **founder_draft + quick_build** — same `complete()` pattern as share/wall
2. **L0 tool short-circuit** — `isFounderRepoStatusPrompt()` → Founder Node vault/git job before LLM
3. **Copilot stream** — optional non-stream cache check before opening SSE (read-only hit)

---

## Local-first routing (Phase 2 preview)

Before any cloud LLM for code/repo questions:

```
Ask prompt
  → classifyFounderBrainTask()
  → if repo-status / git-history → FounderNodeSyncService.enqueue(VAULT_SEARCH | git diff job)
  → if tool result confidence ≥ 0.9 → return (L0 cache, localToolUsed=true)
  → else → runtime.complete() with diff summary in context (not full repo)
```

Founder Node today: sync jobs via `founder-node-sync.service.ts`; no `/local/analyze` HTTP yet — use job queue.

---

## Environment variables (quick wins)

| Variable | Default | Purpose |
|----------|---------|---------|
| `AI_RUNTIME_ENABLED` | `false` | Master switch |
| `AI_RUNTIME_CONTEXT_PRUNING` | `true` | Prompt trimming |
| `AI_RUNTIME_MAX_SYSTEM_CHARS` | `12000` | System cap |
| `AI_RUNTIME_MAX_USER_PROMPT_CHARS` | `16000` | User cap |
| `AI_RUNTIME_MAX_OUTPUT_*` | per intent | Completion cap |
| `AI_RUNTIME_WALL_MAX_MESSAGES` | `40` | Summarizer transcript |
| `AI_PROMPT_CACHE_TTL_SEC` | `3600` | Cache TTL |
| `AI_PROMPT_CACHE_MAX_ENTRIES` | `500` | LRU size |

---

## Quality guardrails

- Pruning preserves **head + tail** — never drops system instructions entirely
- Output caps are **per-intent conservative** — code tier keeps 4096
- Cache only when `skipCache` false — BYOK, forced provider, streaming excluded
- Feature flag off = **identical** to pre-Phase-0 behavior (except share/wall always use `complete()` with no-op cache when disabled)

---

*See [FOUNDER-BRAIN-AI-OS-SPEC.md](./FOUNDER-BRAIN-AI-OS-SPEC.md) for full phased vision.*
