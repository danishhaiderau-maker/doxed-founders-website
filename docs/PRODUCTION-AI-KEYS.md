# Production AI Keys — env vars Danish must set in Railway / Vercel

**Last updated:** 9 August 2026 (Admin AI simplification + Second Brain cheap cascade)

This is the single source of truth for every AI-related env var that the
platform needs in production. Set these in **Railway** (API + workers) and
**Vercel** (web) before merging the next deploy.

> **Why both Railway and Vercel?** The API (NestJS) is on Railway and reads
> every key at runtime. The web app (Next.js) is on Vercel and only reads a
> small subset for client-side hints. When in doubt, set a key in **both**
> environments — it's a no-op if unused.

---

## Roles (Admin → AI Keys)

| Role | Purpose | Provider |
|------|---------|----------|
| **Showcase AI** | Fly showcase bot only | Admin-selectable |
| **Platform Brain** | Community / in-app messaging (walls, paraphrase, platform fallbacks) | DeepSeek |
| **Founder IDE** | Builder chat | DeepSeek V4 Flash + V4 Pro |
| **Second Brain** | Expert IDE consult | **Gemini Flash → OpenAI mini / Luna-class → optional GLM** (never DeepSeek) |

Promo pool UI is removed from Admin. Keys previously under "promo" for Gemini/GLM
are still stored in the same encrypted columns — they now power Second Brain.

---

## 1. DeepSeek (Platform Brain + Founder IDE)

```bash
DEEPSEEK_API_KEY=sk-…                                    # platform DeepSeek key
DEEPSEEK_PRO_MODEL=deepseek-v4-pro                       # coding-tier default
DEEPSEEK_CODING_MODEL=deepseek-v4-pro                    # alias for the above
DEEPSEEK_FLASH_MODEL=deepseek-v4-flash                   # fast-tier default
DEEPSEEK_FAST_MODEL=deepseek-v4-flash                    # alias for the above
```

**Admin UI:** `/admin/control` → **AI Keys** → Platform Brain + Founder IDE panels.

---

## 2. Second Brain cascade (NOT GLM-default)

```bash
GEMINI_API_KEY=AIza…                                     # primary Second Brain (gemini-2.0-flash)
# Optional overrides:
SECOND_BRAIN_PRIMARY_MODEL=gemini-2.0-flash
SECOND_BRAIN_FALLBACK_MODEL=gpt-4o-mini                  # Luna-class cheap consult (requires OPENAI_API_KEY)

# Fallback #2 — if set, used after Gemini and before GLM:
OPENAI_API_KEY=sk-…                                      # gpt-4o-mini (Luna-class cheap consult)

# Optional last resort only (allowGlmSpend=true):
GLM_API_KEY=your_z_ai_coding_plan_key
GLM_API_BASE=https://api.z.ai/api/coding/paas/v4
```

> **Never DeepSeek for Second Brain.** DeepSeek stays Builder / Platform Brain only.
> There is no separate "Luna" provider slug in this repo. Wire Luna-class traffic
> via `OPENAI_API_KEY` + `gpt-4o-mini` (Gemini Flash remains the default primary).

**Admin UI:** `/admin/control` → **AI Keys** → Second Brain cards (Gemini primary, GLM optional).

### Admin → desktop (safe pattern)

1. Save Gemini (and optional Railway `OPENAI_API_KEY`) in Admin / Railway only.
2. Desktop Team users pair a Founder Node or sign in — they never receive the raw key.
3. Founder Next gateway calls `POST /api/second-brain/critique` with Node/JWT auth.
4. Platform cascade runs server-side; desktop shows the answer (or an honest “no Gemini configured” error).
5. Do **not** paste production keys into desktop `.env`, git, or installer configs.

---

## 3. Vision preprocessing (Gemini)

```bash
FOUNDER_VISION_MODEL=gemini-2.0-flash
FOUNDER_VISION_API_KEY=                              # or reuse GEMINI_API_KEY
```

---

## 4. Routing engine flags

```bash
USE_ROUTING_ENGINE_V2=true
USE_SMART_INTENT_CLASSIFIER=true
FOUNDER_BRAIN_TWO_MODEL_ROUTING=true
```
