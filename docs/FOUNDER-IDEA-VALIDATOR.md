# Founder Idea Validator

**Status:** Design / Research (no code changes yet)
**Owner:** Founder OS
**Last updated:** 2026-07-10
**Effort estimate:** ~1.5–2.5 weeks end-to-end (MVP), broken down in §10

> When a founder describes their idea, Founder OS automatically checks
> GitHub and the web for similar projects, then returns a competitive
> landscape report with differentiation analysis and reusable open-source
> code suggestions.
>
> **The novelty:** No AI coding/build tool today (Cursor, Bolt, Lovable,
> v0, Replit) closes the loop from *idea text* → *GitHub + web evidence*
> → *differentiation + reuse plan*. Generic idea validators exist
> (DimeADozen, Preuve, ValidateMySaaS) but none are wired into a
> founder OS, none surface reusable OSS components, and none feed an
> admin review queue. See §11 for the competitive scan.

---

## Table of contents

1. [User experience](#1-user-experience)
2. [Technical architecture](#2-technical-architecture)
3. [Prisma schema](#3-prisma-schema)
4. [API endpoints](#4-api-endpoints)
5. [Prompt design](#5-prompt-design)
6. [Admin review integration](#6-admin-review-integration)
7. [GitHub search strategy](#7-github-search-strategy)
8. [Open-source reuse detection](#8-open-source-reuse-detection)
9. [Competitive moat](#9-competitive-moat)
10. [Build plan & effort](#10-build-plan--effort)
11. [Competitive landscape — does this exist?](#11-competitive-landscape--does-this-exist)
12. [Open questions](#12-open-questions)

---

## 1. User experience

### 1.1 When does the check trigger?

Three trigger points, in priority order:

| # | Trigger | Where | Default | Why |
|---|---------|-------|---------|-----|
| 1 | **On-demand** — founder clicks "Validate my idea" | `/founder-brain`, `/founder-den`, project workspace | On | Lowest friction, most wow-factor. This is the demo path. |
| 2 | **Doxxing application submitted** — auto-run in the background after `POST /founder-applications` | Doxxing flow | On (async) | Gives the admin a competitive snapshot before review. Non-blocking to the founder. |
| 3 | **New project / new goal created** — opt-in toggle | Project setup wizard | Off | Catches ideas before a founder invests weeks. Avoids surprising repeat founders with noise. |

**Key rule:** the check is **never blocking**. It always returns immediately with a "check in progress" state, then streams/polls until results land. Trigger 2 specifically must not delay application submission — it's a fire-and-forget job (BullMQ-style queue or `setImmediate`) that writes the `IdeaCheck` row when done.

### 1.2 What does the founder see?

**Loading state (0–45s typical, 90s worst case):**

```
┌──────────────────────────────────────────────────────────┐
│  🔍 Checking the landscape for your idea…                │
│                                                          │
│  ✓ Extracting keywords from your description             │
│  ✓ Searching 14 GitHub repos                            │
│  ⏳ Searching the web for similar products               │
│  ⏳ Synthesizing competitive analysis                    │
│                                                          │
│  Found so far: 6 repos · 4 products                      │
└──────────────────────────────────────────────────────────┘
```

This is server-sent events (SSE) or polling every 3s against `GET /api/founder-brain/idea-checks/:id`. Phase 1 ships polling (simpler, matches the existing AI proxy pattern); SSE is a Phase-2 polish.

**Results format:**

```
┌──────────────────────────────────────────────────────────┐
│  VERDICT: 🟡 Moderately crowded                          │
│  6 similar projects found. Your wedge: "first to focus   │
│  on Solana mempool frontrunning alerts for retail."      │
│                                                          │
│  ── Competitors (6) ──────────────────────────────────   │
│  ⭐ mev-inspect-rs        2.1k★  Rust · MIT               │
│     MEV inspector. Overlaps on data layer; not retail.   │
│  🚀 Flashbots Protect     Product · funded Series A      │
│     RPC protection. Different segment (power users).     │
│  ... (4 more)                                            │
│                                                          │
│  ── Reusable open source ─────────────────────────────   │
│  📦 mev-inspect-rs /classify → ~2 weeks saved            │
│     MIT · last push 3d ago · fork the classifier module  │
│  📦 jito-solana /simulator → ~4 days saved               │
│     Apache-2.0 · fork the txn simulator                  │
│                                                          │
│  ── What's different about yours ──                      │
│  No existing tool targets retail traders on Solana.      │
│  The wedge is UX + alert routing, not infra.             │
│                                                          │
│  [💾 Save to project]  [↻ Re-run]  [💬 Ask about #2]    │
└──────────────────────────────────────────────────────────┘
```

The **verdict** is a four-level signal:

| Verdict | Meaning | Heuristic the synthesis prompt targets |
|---------|---------|----------------------------------------|
| `novel` | Truly empty space — no direct competitors found | 0 close competitors, <3 adjacent |
| `empty` | Under-explored — opportunity but verify demand | <3 competitors, weak traction |
| `moderate` | Active space with room for a wedge | 3–8 competitors, no dominant player |
| `crowded` | Saturated — need a sharp differentiator | >8 competitors OR a well-funded incumbent |

### 1.3 How do they interact with results?

- **Save to project** — writes the `IdeaCheck` to `Project.memoryGraph` via the Memory Engine so it surfaces in future Founder Brain turns. (Memory Engine is currently a stub per `memory-engine.service.ts`; this is one of its first real consumers.)
- **Dismiss** — marks the check `dismissed=true` (soft hide). Stays in history.
- **Re-run** — creates a *new* `IdeaCheck` row (history is append-only) so you can see how the landscape shifts over time. The moat (§9) depends on this.
- **"Tell me more about competitor X"** — opens a follow-up turn in Founder Brain with the IdeaCheck row loaded as context. The model can do a second web search focused on one competitor.
- **"Fork this repo"** — stretch goal; deep-links to GitHub's fork UI when the founder has a connected GitHub account (`GitHubConnection` already exists at schema line 1810).

---

## 2. Technical architecture

### 2.1 Component diagram

```
founder describes idea
        │
        ▼
┌──────────────────────────────────────────────────────────┐
│  FounderIdeaValidatorService  (new, apps/api/src/         │
│     founder-brain/)                                       │
│                                                          │
│  1. KeywordExtractor  ──► GLM 5.2 fast call (or regex)   │
│  2. GitHubSearchClient ──► /search/repositories           │
│  3. WebSearchClient   ──► GLM 5.2 tool_web_search=true    │
│  4. ReuseDetector     ──► /repos/{owner}/{repo} + license │
│  5. SynthesisPrompt   ──► GLM 5.2 via Routing Engine      │
│                                                          │
│  writes: IdeaCheck row  reads/writes: Memory Engine       │
└──────────────────────────────────────────────────────────┘
        │                          │
        ▼                          ▼
   Prisma (IdeaCheck)      Flight Recorder (reuse existing)
```

### 2.2 GitHub Search API integration

**Auth strategy — two tiers:**

1. **Founder's connected account (preferred).** We already have `GitHubConnection.accessTokenEncrypted`. Decrypt, use it for the search call, attribute the rate-limit consumption to the founder. Falls back to the bot account if their token lacks `public_repo` scope.
2. **Platform bot account (fallback / doxxing flow).** A single `FOUNDER_OS_GITHUB_TOKEN` service account. Authenticated search gives **30 requests/min** vs 10/min unauthenticated. Since one idea check = 1 search + ~6 README/license fetches = ~7 calls, the bot account can serve ~4 concurrent checks. Fine for MVP; add queueing at scale.

**Rate-limit handling (per GitHub's 2026 docs):**

- Read `X-RateLimit-Remaining` + `X-RateLimit-Reset` on every response.
- On 403/429: if `Retry-After` present, sleep that many seconds; else sleep until `X-RateLimit-Reset`; else exponential backoff with jitter, max 3 retries.
- **Serial requests, not concurrent** — GitHub's secondary limits trigger on concurrent bursts. One queue per token.
- Use `ETag` / `If-None-Match` for the README fetches so repeat checks on the same idea cost ~0 quota.

**Endpoint:** `GET /search/repositories`

- Hard cap: GitHub returns at most **1,000 results per query**, and the search scope covers at most **4,000 repos**. We page only the top ~30 by stars. Pagination beyond page 5 is usually noise.
- `incomplete_results: true` in the response means the query timed out — log it, flag the IdeaCheck as `partial`.

### 2.3 Web search

Two viable paths; we ship **Path A** for MVP and keep Path B as an upgrade:

**Path A — GLM 5.2 native web search (preferred).**
GLM 5.2 has `tool_web_search: true` — the model decides when to search, cites sources, and there's a small per-call fee only when a search actually runs. This is the lowest-friction integration because we already call GLM 5.2 through the AI Proxy and Routing Engine. We use it as a *tool* during synthesis: feed the idea, let GLM search for live competitors, and have it fold the findings into the structured output.

**Path B — Dedicated search API (upgrade).**
For deeper data (funding, traffic) we can add later:
- **SerpApi** — Google results as JSON; best for "similar products" discovery.
- **SimilarWeb V5** — `/website-analysis/websites/keywords-competitors` gives competitor domain overlap + traffic. Enterprise pricing.
- **Crunchbase** — firmographics/funding. **No free tier since 2025**; cheapest is $49/mo. Skip for MVP.
- **Intelica** — agent-native, $0.05–$1.00/call via x402, structured competitive JSON. Good fit if we want a pure-API "competitive intelligence" upgrade without building the synthesis prompt ourselves.

For MVP: Path A only. The synthesis model already has the context to judge which web hits matter.

### 2.4 AI synthesis — Routing Engine integration

The synthesis call goes through the **existing AI Proxy → Routing Engine** path (`ai-proxy-runtime.service.ts`), not a bespoke fetch. This means:

- **Intent = `reasoning`** — idea validation is multi-step analysis, not code or simple QA. The legacy `ModelRouterService` already routes `reasoning` to GLM or DeepSeek depending on `twoModelRoutingEnabled`.
- **DDollar spend is automatic** — the AI Proxy's `afterRequest` hook charges the founder via the Spending Engine using `AI_PROXY_DDOLLAR_COST['reasoning']`. We do *not* bypass it.
- **Flight Recorder captures the decision** — so the admin Decision Log shows "idea-check synthesis" as a traceable request.
- **Force `glm` + `glm-5.2`** — we want the 1M context window (GitHub READMEs add up fast) and native web search. Add a `founderBrainTask: 'idea_validation'` hint to the runtime request so the router can specialize later.

**Token budget:** system prompt (~1.5k) + idea text (~1k) + 6 GitHub READMEs truncated to 1.5k each (~9k) + 4 web result snippets (~2k) ≈ 14k prompt tokens. Comfortably inside GLM 5.2's 1M window; set `max_tokens: 2048` for the structured response.

### 2.5 Storage

New `IdeaCheck` model (full schema in §3). Key decisions:

- **`competitors` and `openSourceReuse` are JSON columns**, not related models. The shape is fluid (we'll iterate on the synthesis output) and we never query *into* them relationally. Avoids a schema migration every time we add a field.
- **Append-only history** — re-runs create new rows. Old rows stay so the moat (§9) can track landscape drift.
- **Link to `FounderApplication`** (doxxing) via nullable `applicationId` so the admin review surface can join.
- **Link to `Project`** via nullable `projectId` for the on-demand flow.

### 2.6 Memory Engine integration

After a successful check, `FounderIdeaValidatorService` calls:

```ts
memoryEngine.set('project', projectId, 'ideaCheck:latest', {
  verdict, competitors, openSourceReuse, summary, checkedAt
});
```

This makes the landscape available to future Founder Brain turns ("given my competitors, how should I position?") without re-running the check. The Memory Engine is currently a stub (`memory-engine.service.ts` line 19) — this feature is a forcing function to implement the `project` store for real. Phase it: ship the IdeaCheck table first, wire memory second.

---

## 3. Prisma schema

Add to `prisma/schema.prisma`. Mirrors the existing style (cuid ids, `@db.Text` for long fields, indexes on query paths, `onDelete: Cascade` from the owning user).

```prisma
/// Competitive landscape check for a founder's idea. Created on-demand
/// (founder clicks "validate"), on doxxing-application submit (async), or
/// on project creation (opt-in). Append-only — re-runs make new rows so
/// we can track how the landscape shifts over time.
model IdeaCheck {
  id              String   @id @default(cuid())
  userId          String
  /// Set when the check is tied to a project (on-demand / new-project flow).
  projectId       String?
  /// Set when the check was auto-triggered by a doxxing application.
  applicationId   String?
  /// Raw idea text the founder submitted for validation.
  ideaText        String   @db.Text
  /// Keywords extracted from ideaText and used for GitHub/web search.
  searchQueries   Json?
  /// Final verdict: "crowded" | "moderate" | "empty" | "novel"
  verdict         String
  /// AI-generated 1–2 paragraph summary of the landscape + differentiation.
  summary         String   @db.Text
  /// Array of competitor objects:
  ///   { name, type: "oss"|"product"|"startup",
  ///     url, description, stars?, traction?, funding?,
  ///     differentiation: "what's different about the founder's idea vs this" }
  competitors     Json
  /// Array of reusable-OSS objects:
  ///   { repo, license, whatToReuse, modulePath?, savedTimeEstimate,
  ///     lastPushedAt, stars }
  openSourceReuse Json
  /// Raw evidence bundle kept for debugging / re-synthesis without re-searching.
  /// Capped at ~64KB per row.
  rawEvidence     Json?
  /// "complete" | "partial" (GitHub incomplete_results) | "failed"
  status          String   @default("complete")
  /// Soft-dismissed by the founder — stays in history but hidden from the UI.
  dismissed       Boolean  @default(false)
  /// GitHub rate-limit budget consumed by this check (search + README fetches).
  ghApiCallsUsed  Int      @default(0)
  createdAt       DateTime @default(now())
  updatedAt       DateTime @updatedAt

  user User @relation(fields: [userId], references: [id], onDelete: Cascade)

  @@index([userId])
  @@index([projectId])
  @@index([applicationId])
  @@index([createdAt])
}
```

And the back-relation on `User` (around schema line 462, where the other relation arrays live):

```prisma
  ideaChecks           IdeaCheck[]
```

**Why no FK to `Project` or `FounderApplication`?** Both are nullable and we don't need referential integrity (a check can outlive a draft project that gets deleted). If we later want cascade-on-delete, add `Project? @relation(...)` and `FounderApplication? @relation(...)` and run a migration. For MVP the indexed `projectId` / `applicationId` strings are enough and avoid a coupled migration with two other models.

---

## 4. API endpoints

All under the existing `apps/api/src/` structure. Create a new `founder-brain/` module (there is no `founder-brain/` dir today — closest siblings are `founder-ai-runtime/`, `founder-den/`, `founder-os/`).

### 4.1 `POST /api/founder-brain/check-idea`

Runs (or queues) a check. Auth: JWT required.

```ts
// dto/check-idea.dto.ts
export class CheckIdeaDto {
  @IsString() @MinLength(20) @MaxLength(5000)
  ideaText!: string;

  @IsOptional() @IsString()
  projectId?: string;

  @IsOptional() @IsString()
  applicationId?: string;

  /// Force a fresh search even if a recent check exists for the same idea hash.
  @IsOptional() @IsBoolean()
  force?: boolean;
}
```

**Response (202 Accepted):**

```json
{
  "id": "ck...",
  "status": "in_progress",
  "pollUrl": "/api/founder-brain/idea-checks/ck...",
  "estimatedSeconds": 45
}
```

The controller calls `FounderIdeaValidatorService.run(...)` which does the work synchronously if it can finish in ~5s, otherwise enqueues and returns immediately. For MVP, run inline but stream progress via the row's `status` field; client polls.

**Idempotency:** if a check with the same `userId` + SHA-256(`ideaText`) exists and was created within the last 24h, return it instead of re-running — unless `force: true`.

### 4.2 `GET /api/founder-brain/idea-checks/:id`

Returns a single check by id. Auth: JWT, must own the check (or be admin).

### 4.3 `GET /api/founder-brain/idea-checks?userId=...&projectId=...&limit=20`

List checks. Auth: founder sees their own; admin sees any.

### 4.4 `PATCH /api/founder-brain/idea-checks/:id`

Body: `{ dismissed?: boolean }`. Founder can dismiss.

### 4.5 Integration into `POST /api/founder-applications`

In `founder-applications.controller.ts` `create()` (line 49), after the application is created, fire-and-forget a check:

```ts
const application = await this.prisma.founderApplication.create({ ... });

// Fire-and-forget — never blocks the application submission.
void this.ideaValidator.run({
  userId: user.id,
  applicationId: application.id,
  ideaText: dto.ideaDescription,
  projectName: dto.projectName,
}).catch((err) => this.logger.warn(`IdeaCheck failed for app ${application.id}: ${err}`));

return application;
```

The check writes its own `IdeaCheck` row linked by `applicationId`. The admin review surface (§6) joins on that.

---

## 5. Prompt design

The synthesis prompt is the heart of the feature. It's sent to GLM 5.2 via the Routing Engine with `tool_web_search: true` so the model can pull live product/startup data. Structured JSON output via `response_format: { type: 'json_schema', json_schema: { ... } }` (GLM 5.2 supports this — OpenAI-compatible).

### 5.1 System prompt

```
You are the Founder OS Idea Validator. Given a founder's idea description
and a bundle of evidence (GitHub repos + your own web searches), produce
a rigorous competitive landscape analysis.

Your job:
1. Identify the closest competitors — both open-source projects and
   commercial products/startups.
2. For each, state precisely what overlaps with the founder's idea and
   what is genuinely different about the founder's angle.
3. For open-source competitors with permissive licenses (MIT, Apache-2.0,
   BSD, MPL), name the specific modules/files the founder could fork and
   estimate the time saved.
4. Return a verdict on how crowded the space is.
5. Write a sharp 1–2 paragraph summary that names the founder's wedge.

Be honest. If the space is crowded, say so. If the idea is novel, say
why. Do not flatter the founder. Cite URLs.

Use the web_search tool when you need live data on funding, traction,
or recent product launches. Do not fabricate companies, funding rounds,
star counts, or licenses — if you don't know, search or omit.

Return STRICT JSON matching this schema (and nothing else):
{schema}
```

### 5.2 JSON schema (passed as `response_format`)

```json
{
  "name": "IdeaValidationReport",
  "strict": true,
  "schema": {
    "type": "object",
    "required": ["verdict", "summary", "competitors", "openSourceReuse", "differentiation"],
    "additionalProperties": false,
    "properties": {
      "verdict": {
        "type": "string",
        "enum": ["crowded", "moderate", "empty", "novel"]
      },
      "summary": { "type": "string", "maxLength": 1200 },
      "differentiation": {
        "type": "string",
        "description": "2–4 sentences naming the founder's specific wedge vs the field."
      },
      "competitors": {
        "type": "array",
        "maxItems": 12,
        "items": {
          "type": "object",
          "required": ["name", "type", "url", "description", "differentiation"],
          "additionalProperties": false,
          "properties": {
            "name": { "type": "string" },
            "type": { "type": "string", "enum": ["oss", "product", "startup"] },
            "url": { "type": "string" },
            "description": { "type": "string", "maxLength": 400 },
            "stars": { "type": "integer" },
            "traction": { "type": "string", "description": "free-text traction signal" },
            "funding": { "type": "string" },
            "differentiation": {
              "type": "string",
              "description": "What's different about the founder's idea vs this competitor."
            }
          }
        }
      },
      "openSourceReuse": {
        "type": "array",
        "maxItems": 8,
        "items": {
          "type": "object",
          "required": ["repo", "license", "whatToReuse", "savedTimeEstimate"],
          "additionalProperties": false,
          "properties": {
            "repo": { "type": "string" },
            "license": { "type": "string" },
            "whatToReuse": { "type": "string", "maxLength": 300 },
            "modulePath": { "type": "string" },
            "savedTimeEstimate": { "type": "string", "description": "e.g. '~1 week'" },
            "lastPushedAt": { "type": "string" },
            "stars": { "type": "integer" }
          }
        }
      }
    }
  }
}
```

### 5.3 User message assembly

```
FOUNDER'S IDEA:
{ideaText}

GITHUB EVIDENCE (top {n} repos by stars, pre-fetched):
[
  { "repo": "owner/name", "stars": 2100, "language": "Rust",
    "license": "MIT", "pushedAt": "2026-07-01",
    "description": "...", "readmeExcerpt": "first 1500 chars..." },
  ...
]

Use the web_search tool to find commercial competitors, funding rounds,
and recent launches. Then produce the report.
```

### 5.4 Why this works with GLM 5.2 specifically

- **1M context window** — we can paste several full READMEs, not just descriptions.
- **Native `tool_web_search`** — no separate search API wiring for MVP; the model decides when to search and cites what it used.
- **Strict JSON mode** — the `response_format` schema above is enforced by the model, so the service code can `JSON.parse` without defensive fallbacks.
- **Tunable `reasoning_effort`** — set to `high` for this synthesis (it's the value-driving call); `none` for the keyword-extraction warmup call.

---

## 6. Admin review integration

When the admin reviews a doxxing application at `/admin/founder-applications`, the application detail panel should show the auto-generated competitive check inline, below the application fields and above the approve/reject controls.

### 6.1 Data join

The admin `GET /founder-applications/pending` endpoint (controller line 83) already returns the application row. Extend it to include the latest IdeaCheck:

```ts
include: {
  user: { select: { ... } },
  ideaChecks: {
    where: { applicationId: applicationId },
    orderBy: { createdAt: 'desc' },
    take: 1,
  },
}
```

(Or, simpler for MVP: a separate `GET /api/founder-brain/idea-checks?applicationId=...` that the admin frontend calls after loading the application.)

### 6.2 What the admin sees

```
┌─ Doxxing application: mev-retail-alerts ─────────────────┐
│  Founder: jane.crypto   Project: mev-retail-alerts       │
│  GitHub: github.com/jane/mev-alerts   Video: ...         │
│  Idea: "Alerts for Solana MEV for retail traders..."     │
│                                                          │
│  ── Auto competitive check (ran 2 min ago) ──────────    │
│  🟡 MODERATE — 6 competitors found                       │
│  Wedge: first to target retail on Solana.                │
│  Reusable OSS: 2 MIT/Apache modules (~2.5 weeks saved)   │
│  [View full report ▾]                                   │
│                                                          │
│  [Approve]  [Reject]  [Needs more info]                 │
└──────────────────────────────────────────────────────────┘
```

This directly answers the admin's gut question — *"is this idea worth approving?"* — without making them open a tab and Google it. The full report expands inline (collapsed by default) showing the competitor list and reuse suggestions.

### 6.3 Why this matters for the doxxing flow

Doxxing is the gate to `VERIFIED_BUILDER` (controller line 138). Today the admin approves based on GitHub URL + video + idea text. The IdeaCheck adds a **third signal**: market reality. A founder proposing "yet another Uniswap fork" shows up as 🔴 crowded; a founder in a genuinely empty niche shows up as 🟢 novel. The admin still decides — the check informs, doesn't replace.

---

## 7. GitHub search strategy

The quality of the whole feature hinges on search query construction. Two-stage approach:

### 7.1 Stage A — keyword extraction

From the idea description, extract the search terms. Two options:

- **Cheap path (regex + stopword list):** strip stopwords, keep nouns and tech terms. Works surprisingly well for idea descriptions which are usually noun-heavy.
- **Smart path (GLM 5.2 fast call):** one cheap completion with `reasoning_effort: none`:

  ```
  Extract 3–6 GitHub search keywords from this idea description.
  Return a JSON array of strings. Only the keywords, nothing else.
  Idea: "{ideaText}"
  ```

  Costs ~200 tokens. Use this for MVP — it's far better at spotting that "frontrunning alerts for retail" should become `["mev", "frontrunning", "solana", "alerts"]` not `["retail", "traders"]`.

Generate **2–3 query variants** per idea (broad → narrow) to cast a wider net.

### 7.2 Stage B — search construction

```
GET /search/repositories?q={keywords}+in:description,topics,readme
                          &sort=stars
                          &order=desc
                          &per_page=30
Headers: Authorization: token {token}
         Accept: application/vnd.github+json
```

**Filters applied in the `q` string:**

| Filter | Rationale |
|--------|-----------|
| `in:description,topics,readme` | Match anywhere a founder would describe what the project does |
| `stars:>10` | Filter out abandoned 0-star forks |
| `pushed:>2024-07-10` | Exclude repos not touched in 2 years (avoids necromancy) |
| `archived:false` | Exclude GitHub-archived repos |
| `fork:false` | Exclude pure forks (the original is what matters) |
| `license:mit,apache-2.0,bsd-2-clause,bsd-3-clause,mpl-2.0` (optional, reuse pass) | Restrict to permissively-licensed repos when looking for reusable code |

**Per-result enrichment (serial, ETag-aware):**

For each of the top ~8 results:

1. `GET /repos/{owner}/{repo}` — confirm license, stars, last push, language.
2. `GET /repos/{owner}/{repo}/readme` (Accept: `application/vnd.github.raw`) — truncate to first 1,500 chars for the synthesis prompt.
3. (Reuse pass only) `GET /repos/{owner}/{repo}/contents/package.json` or `requirements.txt` or `Cargo.toml` — detect tech stack to match against the founder's stated stack.

Cap at 8 repos to stay inside rate limits and token budget. ~8 calls/repo + 1 search = ~9 calls per check; with the 30/min authenticated limit, one bot token serves ~3 concurrent checks.

### 7.3 Failure modes to handle

- **`incomplete_results: true`** — set `status: 'partial'` on the IdeaCheck; the synthesis prompt is told results may be incomplete.
- **Zero results** — that's signal, not a bug. The synthesis returns `verdict: 'novel'` or `'empty'`. Don't retry with looser queries silently — surface it.
- **Rate limited** — queue + backoff; never fail the application submission (the integration in §4.5 is fire-and-forget).

---

## 8. Open-source reuse detection

For each GitHub repo that lands in the top results, determine whether the founder could *fork and reuse* parts of it. This is the genuinely novel piece — no idea-validation competitor does this.

### 8.1 License gate

Reusable licenses: **MIT, Apache-2.0, BSD-2/3-Clause, MPL-2.0, ISC**. These permit fork-and-modify with attribution.

Not reusable without legal review: **GPL, AGPL, SSPL, proprietary, no license** (no license = "all rights reserved" by default — flag this honestly to the founder).

The license comes from the `/repos/{owner}/{repo}` response (`license.spdx_id`). If null, treat as non-reusable and say so.

### 8.2 Overlap detection

Ask the synthesis model (it already has the README + the founder's idea):

> *"If a founder building {idea} wanted to reuse parts of {repo}, what specific module or directory would save them the most time? Name the path. Estimate the time saved. Be specific — don't say 'the code'; say 'the `/src/classifier` module'."*

The model returns `{ repo, license, whatToReuse, modulePath, savedTimeEstimate }` per the schema in §5.2.

### 8.3 Time-saved estimate calibration

The model will produce rough estimates ("~1 week", "~3 days"). To keep these honest:

- Cap at "~3 months" — anything bigger is "just fork the whole project."
- Floor at "~2 hours" — anything smaller isn't worth the integration tax.
- Surface them as **estimates**, not promises. The UI shows "~1 week" not "1 week."

### 8.4 Freshness check

`lastPushedAt` from the repo metadata is included so the founder sees whether the reusable module is actively maintained. A 4-year-old MIT repo is still reusable, but the UI flags it: *"last commit 4 years ago — budget time to modernize."*

---

## 9. The competitive moat

The IdeaCheck is useful on its own. But the *aggregate* data across thousands of checks is a durable moat no single-shot validator can match.

### 9.1 What compounds

| Signal | Source | Why it's valuable |
|--------|--------|-------------------|
| **Trending idea categories** | Aggregate `verdict` + `competitors[].type` over time | "In Q3, 40% of doxxing applications were AI agents — the field is crowding." Sell this back to founders as market intelligence. |
| **Which OSS repos founders actually reuse** | Track clicks on "Fork this repo" + `openSourceReuse[].repo` frequency | A proprietary ranking of "most-forked-by-founders" repos — GitHub's star count can't see intent. |
| **Market map per vertical** | Cluster checks by idea similarity (embedding the `ideaText`) | A live market map: "here's the DeFi tooling cluster, here's the empty whitespace next to it." Updates as founders submit. |
| **Founder outcome correlation** | Months later: did approved founders with `novel` verdicts ship more? Raise more? | The strongest signal: which verdicts predict founder success. Becomes a data product. |

### 9.2 Implementation phasing

- **Phase 1 (this build):** store the data, surface per-check. No aggregation.
- **Phase 2:** nightly job that clusters recent checks and emits a "trending ideas" digest to the admin dashboard.
- **Phase 3:** track reuse-action events (`fork_clicked`, `saved_to_project`) to build the proprietary reuse ranking.
- **Phase 4:** outcome correlation — join `IdeaCheck.verdict` against `Founder.builderScore`, raise history, launch outcomes. This is the data moat.

### 9.3 Why incumbents can't easily copy

DimeADozen, Preuve, ValidateMySaaS run a check once and hand back a PDF. They don't see the founder again. Founder OS sees the founder *build* — the IdeaCheck is the first frame of a longitudinal record. That's the moat: not the check, the *after*.

---

## 10. Build plan & effort

### 10.1 Breakdown

| Phase | Scope | Est. |
|-------|-------|------|
| **P0 — Schema + skeleton** | Prisma migration, `IdeaCheck` model, `founder-brain` module scaffold, empty service + controller, DTOs | 0.5 day |
| **P1 — GitHub client** | `GitHubSearchClient` with auth fallback, rate-limit headers, serial queue, ETag support, query construction from keyword extraction | 2 days |
| **P2 — Synthesis** | Synthesis prompt + JSON schema, wire through AI Proxy / Routing Engine, parse + validate structured response, store to `IdeaCheck` | 1.5 days |
| **P3 — Web search** | Enable `tool_web_search` on GLM 5.2 synthesis call, test citation quality, fallback path if web search quota exhausted | 0.5 day |
| **P4 — Endpoints** | `POST /check-idea`, `GET /idea-checks/:id`, list, dismiss; idempotency by idea hash; integration into `POST /founder-applications` | 1 day |
| **P5 — Frontend** | Founder-facing results card (loading state + results), admin inline panel in application review | 2 days |
| **P6 — Memory Engine** | Implement real `project` store backend (currently stub); wire `ideaCheck:latest` write | 1 day |
| **P7 — Polish + tests** | Partial-result handling, rate-limit backoff tests, prompt eval against 20 sample ideas, copy review | 1.5 days |

**Total: ~10 working days (~2 weeks) for a ship-quality MVP.** A rough-but-demoable version (P0–P4, skip frontend polish) is ~5 days.

### 10.2 Risk-weighted

- **+2 days** if GLM 5.2's strict JSON mode is flaky on the competitor schema (mitigation: defensive parse + retry with simpler schema).
- **+1 day** if the founder's connected GitHub token usually lacks search scope (likely — most OAuth scopes are repo-only). We'll lean on the bot token anyway.
- **+1–2 days** if we add SerpApi or Intelica as a web-search upgrade mid-build. Defer to Phase 2.

**Realistic range: 1.5–2.5 weeks.**

---

## 11. Competitive landscape — does this exist?

Researched via WebSearch (July 2026). Short answer: **idea validators exist; none do what this spec describes.**

### 11.1 Idea-validation tools (closest category)

| Tool | What it does | Gap vs Founder OS Idea Validator |
|------|--------------|----------------------------------|
| **DimeADozen** | $129 one-time, 200+ page citation-backed report. Market size, competition, unit economics. | No GitHub. No OSS reuse detection. Not wired into a founder OS or admin review. Static PDF. |
| **Preuve AI** | $29, scans 50+ live sources (Crunchbase, Reddit, G2, Product Hunt). Source-linked. | Closest in spirit on the *web* side. But no GitHub repo analysis, no reuse suggestions, not integrated into a build flow. |
| **IdeaRoast** | $5, 60s, 4 AI agents stress-test failure modes. | Fast and cheap but no external evidence beyond Exa search; no OSS angle. |
| **WorthBuild** | $5, 2h+, G2/Trustpilot + SEO + competitor pricing. | Strong on SaaS competitor data. No GitHub. No reuse. |
| **ValidatorAI** | $25/mo, chatbot mentor "Val." | Conversational, not evidence-first. No GitHub. |
| **ValidateMySaaS** | Competitor landscape reports for SaaS. | Feature-by-feature breakdowns are good; GitHub-blind; not integrated. |

**Conclusion:** the *idea-validation* category is crowded, but every player is either (a) PDF-report-only, (b) GitHub-blind, or (c) not integrated into a founder's build/admin flow. None do the three-step "GitHub + web + reuse synthesis" loop.

### 11.2 GitHub competitive / reuse tools (adjacent)

| Tool | What it does | Gap |
|------|--------------|-----|
| **GithubPill** (Claude Code plugin) | "Does my idea already exist on GitHub?" 60s verdict (🟢🟡🔴). | GitHub-only. No web. No reuse detection. No founder OS integration. Closest direct analog and still misses half the feature. |
| **Scavenge** | Repo intelligence extractor — scans GitHub for patterns filtered by your stack. | Reverse direction (explores repos, doesn't validate ideas). Powerful reuse detection but you bring the repos. |
| **Scout (@4meta5)** | Fingerprints your local project, finds similar OSS. | Requires an existing codebase. Idea Validator works from idea *text*, before any code. |
| **RepoLens** | `mgithub.com` — analyze/compare any repo. | General-purpose repo browser; not idea-first. |

**Conclusion:** GithubPill is the only tool that validates an *idea* against GitHub, and it's a 60-second Claude Code plugin with a traffic-light verdict — no web, no reuse plan, no integration. The reuse-detection tools (Scavenge, Scout) work post-code, not pre-code.

### 11.3 The gap we fill

No tool combines:
1. **Idea-text-in** (not code-in) — works before the founder has written a line.
2. **GitHub + web** in one pass — OSS *and* commercial competitors.
3. **Reuse detection** — names the forkable module + time saved.
4. **Founder OS integration** — feeds the admin doxxing review and the founder's project memory.

That four-part combination is the novelty. It's defensible because the integration (doxxing flow, Memory Engine, Routing Engine) is the part competitors can't copy without being Founder OS.

---

## 12. Open questions

1. **Cost attribution.** Each check = 1 keyword-extraction call + 1 synthesis call (with web search fees) + GitHub quota. Charge DDollars? Free for doxxing applicants? Proposal: free for trigger 2 (doxxing — it's admin-facing), costs ~50 DDollars for trigger 1 (on-demand). Aligns with the existing `AI_PROXY_DDOLLAR_COST` spend hook.

2. **GitHub token scope.** Do founders' connected tokens typically have search scope, or do we always lean on the bot token? Need to audit `GitHubConnection` tokens. Likely: always use bot token for search, use founder token only for the "fork this repo" deep-link.

3. **Prompt eval set.** We need ~20 sample idea descriptions spanning crowded (Uniswap fork), moderate (Solana MEV alerts), and novel (genuinely weird) to tune the synthesis prompt before ship. Build this into P7.

4. **Web search quota / cost.** GLM 5.2's per-call web search fee is small but non-zero. Cap at ~5 searches per check. Surface a "web search skipped (quota)" state rather than failing.

5. **Stale-check re-run cadence.** Should we auto-rerun a check if it's >90 days old and the founder reopens the project? Proposal: prompt the founder ("the landscape may have shifted — re-run?") rather than auto-running.

6. **Admin override.** Should the admin be able to mark a verdict "inaccurate" and have that feed the Learning Engine? Probably yes in Phase 2 — it's the same feedback loop the Routing Engine already uses (retry detection → reputation).

---

## Appendix A — Key technical challenge

**The single hardest technical problem is GitHub search quality, not rate limits.**

Rate limits are manageable: 30 req/min authenticated is plenty for a per-founder, per-idea check, and the serial-queue + ETag pattern is well-trodden (GitHub's 2026 docs explicitly recommend it).

The real challenge is **query construction**. A founder writes "I want to build alerts for retail traders so they don't get frontrun on Solana." The naive keyword extraction (`alerts`, `retail`, `traders`) misses every relevant repo because the OSS world uses `MEV`, `frontrunning`, `sandwich`, `jito`. Without the GLM-assisted keyword-extraction step (§7.1), the search returns noise and the whole report loses credibility.

This is why Stage A (keyword extraction via a cheap GLM call) is non-optional — it's the difference between a report that says "no competitors found" (wrong) and one that surfaces `mev-inspect-rs` and `jito-solana` (right). Budget extra prompt-tuning time here.

---

## Thin-slice notes (Phase 6 build, 2026-07-10)

The Phase 6 build shipped a **working thin slice** of the design above. The
spec is implemented end-to-end (Prisma model → controller → service →
Browser Use LAM adapter → synthesis → frontend panel + daily pop-up), with
two intentional simplifications documented here so future workers know
exactly what to harden.

### Shipped (MUST-have, all done)

- **Prisma `IdeaCheck` model + `IdeaCheckStatus` enum + migration**
  (`prisma/migrations/20260710230000_add_idea_check/migration.sql`). Fields:
  `id`, `userId`, `ideaText`, `status`, `resultJson`, `differentiationScore`,
  `similarProjectsJson`, `suggestedOssJson`, `createdAt`, `completedAt`,
  plus `projectId`/`applicationId` for the doxxing/project flows, and
  `viewed`/`dismissed` to gate the daily pop-up.
- **NestJS module** `apps/api/src/idea-validator/`:
  - `idea-validator.controller.ts` — `POST /check`, `GET /checks`,
    `GET /check/:id`, `GET /latest-for-user`, `PATCH /check/:id`, plus a
    manual-trigger `POST /cron/daily-pop-up`. All JWT-authed.
  - `idea-validator.service.ts` — orchestrates keyword extraction → browser
    research → synthesis → persist. Idempotent within 24h on identical idea
    text. Defensive parsing so a malformed model report still completes the
    check rather than failing.
- **Browser Use research hand (the LAM capability)**
  `apps/api/src/idea-validator/browser-research.adapter.ts`:
  - Headless Chromium (Playwright) driven by the decision model.
  - Every model call routes through `AiProxyRuntimeService` (AI Gateway) —
    no direct DeepSeek/GLM API calls anywhere.
  - Every browser action logged to the Flight Recorder as a
    `RoutingDecision`-style row (`intent: 'research'`,
    `chosenProvider: 'local-playwright'`) — this is the action trace that
    makes it a LAM, not a chatbot.
  - Hard timeouts (30s/query, 120s/check) + step cap (8 steps/query).
  - **Graceful degradation:** if Playwright/chromium isn't available at
    runtime, falls back to a fetch + regex extractor so the feature still
    works in serverless/thin-CI environments.
- **Capability Registry:** `local-playwright` / `chromium-headless` seeded
  in `capability-registry.seeds.ts` so Flight Recorder rows join cleanly.
- **Daily proactive pop-up cron** (`idea-validator.cron.ts`) — 09:00 daily,
  surfaces unviewed completed checks, gated behind the kill switch.
- **Kill switch:** `IDEA_VALIDATOR_ENABLED` env (default ON in dev, set
  `false` to disable in prod without redeploy).
- **Frontend:** `idea-validator-panel.tsx` (input, loading state,
  competitive landscape table, differentiation score gauge, similar
  projects, suggested OSS, summary) + `idea-pop-up.tsx` (daily proactive
  pop-up). Integrated into the Founder OS shell at `/founder-os`.

### Stubbed (CAN-stub per scope-management rules)

- **Product Hunt scraping.** The adapter has a `producthunt` research target
  with a search URL + link parser, but PH's JS-rendered SPA often blocks
  headless fetches. The default query set is `['github', 'web']` only; PH
  is wired but not in the default rotation until its extraction is hardened
  against their anti-bot.
- **GitHub authenticated search + README/license enrichment.** The thin
  slice uses GitHub's public search (no auth token). The spec's
  founder-token / bot-token fallback, README fetch, and license detection
  (§2.2, §7.2) are not yet wired — the browser adapter extracts repo names
  + descriptions from the search results page, and the synthesis model
  infers licenses from what it knows. Authenticated search + ETag-aware
  README fetch is the next hardening pass.
- **Memory Engine integration (§2.6).** The `ideaCheck:latest` write to the
  Memory Engine is deferred — the Memory Engine's `project` store is still
  a stub. The IdeaCheck row is the source of truth for now.
- **Doxxing-application auto-trigger (§4.5).** The fire-and-forget hook on
  `POST /founder-applications` is not yet wired; the admin can run a check
  manually via the Idea Validator panel. Wiring it is a one-liner once the
  Memory Engine store lands.
- **Admin inline panel (§6).** The founder-facing panel is shipped; the
  admin review inline panel is deferred.

### Cost estimate

Per-check cost target was ~$0.005. Actual cost is dominated by the two
model calls (keyword extraction ~200 output tokens; synthesis ~2048 output
tokens) routed through the AI Gateway. At GLM 5.2 seed pricing
($0.5/1M input, $1.5/1M output) a typical check is ≈ $0.003–0.005,
comfortably on target. The Flight Recorder rows (one per browser action +
one per model call) give the exact per-check breakdown once traffic flows;
the Learning Engine will refine from there. Browser actions themselves are
logged at costUsd=0 (local chromium, no per-call fee).

