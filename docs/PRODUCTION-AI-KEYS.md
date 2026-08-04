# Production AI Keys — env vars Danish must set in Railway / Vercel

**Last updated:** 4 August 2026 (post Founder IDE rename + GLM-4V vision wiring)

This is the single source of truth for every AI-related env var that the
platform needs in production after the Founder IDE rename and the GLM-4V
vision preprocessor ship. Set these in **Railway** (API + workers) and
**Vercel** (web) before merging the next deploy.

> **Why both Railway and Vercel?** The API (NestJS) is on Railway and reads
> every key at runtime. The web app (Next.js) is on Vercel and only reads a
> small subset for client-side hints. When in doubt, set a key in **both**
> environments — it's a no-op if unused.

---

## 1. DeepSeek (platform brain fallback)

Default path for `founder-os-auto`/`founder-os-reasoning` routing. Used by the
AI Proxy when GLM is unavailable or the routing engine selects DeepSeek.

```bash
DEEPSEEK_API_KEY=sk-…                                    # platform DeepSeek key
DEEPSEEK_PRO_MODEL=deepseek-chat                         # coding-tier default
DEEPSEEK_CODING_MODEL=deepseek-chat                      # alias for the above
DEEPSEEK_FLASH_MODEL=deepseek-chat                       # fast-tier default
DEEPSEEK_FAST_MODEL=deepseek-chat                        # alias for the above
```

**Where it's read:**
- `apps/api/src/founder-ai-runtime/founder-brain-providers.types.ts`
- `apps/api/src/founder-ai-runtime/founder-brain-providers.service.ts` →
  `resolveApiKey('deepseek')`
- `apps/api/src/ai-proxy/ai-proxy-runtime.service.ts` → upstream invoke

**Admin UI toggle:** `/admin/control` → **AI Keys** tab →
*Platform Brain — DeepSeek fallback*. If you set the env var, the admin UI
shows `source: env` and the textarea becomes optional.

---

## 2. GLM 5.2 (ZhipuAI coding plan)

Founder Brain coding tier. Routes through the z.ai **Coding Plan** endpoint,
NOT the general z.ai API. Defaults to `glm-5.2`.

```bash
GLM_API_KEY=your_z_ai_coding_plan_key                    # ZhipuAI key
GLM_API_BASE=https://api.z.ai/api/coding/paas/v4          # Coding Plan endpoint
AI_RUNTIME_CODE_MODEL=glm-5.2                             # default coding model
GLM_CODING_MODEL=glm-5.2                                  # alias
GLM_FAST_MODEL=glm-4-flash                                # fast-tier GLM model
FOUNDER_BRAIN_TWO_MODEL_ROUTING=true                      # enable two-model routing
FOUNDER_BRAIN_FAST_PROVIDER=deepseek                      # fast lane
FOUNDER_BRAIN_CODING_PROVIDER=glm                         # coding lane
```

> **ZHIPUAI_API_KEY is NOT read by the API.** The codebase only reads
> `GLM_API_KEY`. If your provider portal shows `ZHIPUAI_API_KEY`, copy the
> same value into `GLM_API_KEY` for the deploy.

**Where it's read:**
- `apps/api/src/founder-os/glm-config.ts` → `getGlmApiBaseUrl()`,
  `getGlmDefaultModel()`
- `apps/api/src/founder-ai-runtime/founder-brain-providers.service.ts` →
  `resolveApiKey('glm')`

**Admin UI toggle:** `/admin/control` → **AI Keys** tab →
*GLM 5.2 (ZhipuAI) — promo*. Then scroll to **Founder Brain Providers** →
enable two-model routing → run **Test both providers**.

Reference: `docs/GLM-ZAI-PROVIDER-SETUP.md`.

---

## 3. GLM-4V vision preprocessing (NEW — ships with this commit)

Multimodal preprocessor. When a request includes an image attachment and the
selected coding model has no vision capability (DeepSeek, GLM 5.2 text), the
AI Proxy first routes the image through GLM-4V, then injects the resulting
text description into the coding model's prompt.

```bash
# Optional — defaults shown.
FOUNDER_VISION_MODEL=glm-4v                              # ZhipuAI vision model
FOUNDER_VISION_BASE_URL=https://api.z.ai/api/paas/v4      # general z.ai endpoint
# The vision provider reuses GLM_API_KEY if FOUNDER_VISION_API_KEY is unset.
# Set a separate key here only if you want vision billed to a different account.
FOUNDER_VISION_API_KEY=                                   # optional override
```

**Where it's read:**
- `apps/api/src/founder-os/glm-config.ts` → `getVisionApiBaseUrl()`,
  `getVisionModel()`
- `apps/api/src/ai-proxy/vision-preprocessor.service.ts` →
  `describeImage()` (new file)
- `apps/api/src/ai-proxy/ai-proxy-runtime.service.ts` →
  `maybePreprocessImages()` (new step in the invoke path)

**Cost attribution:** Vision preprocessing is logged as
`source: 'ai-proxy:glm-4v'` in `AiTokenUsageLog`. DDollar spend uses the
`fast` tier rate (same as `glm-4-flash`).

**Trigger:** Activated automatically when a `ChatCompletionRequestDto` message
`content` is an array containing an `image_url` part AND the resolved route's
model has `vision: false` in the Capability Registry. Models with
`vision: true` (e.g. future GPT-4o wiring) bypass the preprocessor.

---

## 4. Routing engine + intent classifier flags

These are not keys but behavior switches. Defaults are correct for production.

```bash
USE_ROUTING_ENGINE_V2=true          # Capability Registry + Flight Recorder (default)
USE_SMART_INTENT_CLASSIFIER=true    # hybrid heuristic + GLM-4-flash intent (default)
RATE_LIMIT_FAIL_OPEN=false          # fail closed if DB unreachable (default)
TWITTER_VERIFIED_FREE_TOKEN_GATE=true # require Twitter for free credits (default)
```

Reference: `docs/ENV-VARS.md`.

---

## 5. Cursor / OpenHands — BYOK only, NOT platform defaults

```bash
# OPTIONAL — only if you want the platform to relay to Cursor Cloud Agents.
# Most users bring their own key in /settings/builder; this env is the
# platform-side fallback for the showcase bot only.
CURSOR_API_KEY=                     # usually leave unset
```

The `CURSOR` provider entry in `packages/utils/src/ai-providers.ts` is kept as
a BYOK option for users who already have Cursor Cloud accounts. It is no
longer referenced in user-facing marketing copy.

---

## URL changes (Founder OS → Founder IDE)

| Before | After | Status |
|---|---|---|
| `/founder-os` (page) | `/founder-ide` | 301 redirect in `next.config.ts` |
| `/founder-os/decisions` | `/founder-ide/decisions` | 301 redirect in `next.config.ts` |
| `/api/founder-os/*` (API) | **unchanged** | wire-protocol contract |
| `founder-os-auto`, `founder-os-code`, `founder-os-reasoning`, `founder-os-fast` (model aliases) | **unchanged** | wire-protocol contract |
| `FOUNDER_OS_*` env vars (legacy) | **unchanged** | wire-protocol contract |
| `.github/founder-os/` directory in user repos | **unchanged** | user-data contract |
| `WorkspaceSession.conversation` Prisma field | **unchanged** | DB schema |

The 301 redirect preserves every inbound link, Google index entry, and
bookmarked URL. No SEO loss.

---

## IDE ↔ website chat sync (NEW — ships with this commit)

Two new surfaces close the loop between the Founder IDE desktop client and the
website's per-user `WorkspaceSession.conversation`:

1. **API:** `PATCH /api/founder-node/workspace-session/conversation`
   - Auth: `FounderNode {nodeId}:{nodeToken}` (node credentials, not JWT)
   - Body: `{ conversation: WorkspaceConversationMessage[] }`
   - Effect: upserts the calling node's owner's `WorkspaceSession.conversation`
     row.
2. **IDE side:** `founder-next/server/founder-node-remote.mjs` gained a
   `pushConversation(conversation)` method on `FounderNodeRemoteClient`. It
   POSTs the conversation to the endpoint above on each turn. Fire-and-forget
   — never blocks the local build.

The JWT-authenticated `PUT /api/workspace-session` route is unchanged (still
used by the web UI for the in-page chat panel).

---

## Verification checklist (run after deploy)

1. `GET /v1/models` returns the `founder-os-*` aliases (unchanged).
2. `/founder-os` returns 308 with `Location: /founder-ide`.
3. `/founder-ide` renders the workspace shell.
4. `/admin/control` → AI Keys shows both DeepSeek and GLM as `configured: true,
   source: env`.
5. Send a chat turn with an image attachment to `/v1/chat/completions` with
   `model: founder-os-auto` and watch the API logs for
   `vision_preprocessor.describeImage ok` before the upstream DeepSeek call.
6. From the Founder IDE desktop, send a chat message and confirm the website's
   `/founder-ide` chat panel reflects the new turn within ~5s.

If any of the above fail, see `docs/ENV-VARS.md` for the full env reference
and `docs/GLM-ZAI-PROVIDER-SETUP.md` for GLM-specific debugging.
