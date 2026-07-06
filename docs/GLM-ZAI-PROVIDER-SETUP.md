# GLM z.ai Provider Setup (Founder Brain coding tier)

**Last updated:** 7 July 2026

Founder Brain routes **coding / implementation** prompts to GLM via the z.ai **Coding Plan** endpoint. This is separate from the general z.ai API base URL.

## Railway API env (recommended)

```bash
GLM_API_KEY=your_z_ai_coding_plan_key
GLM_API_BASE=https://api.z.ai/api/coding/paas/v4
AI_RUNTIME_CODE_MODEL=glm-5.2
FOUNDER_BRAIN_TWO_MODEL_ROUTING=true
FOUNDER_BRAIN_FAST_PROVIDER=deepseek
FOUNDER_BRAIN_CODING_PROVIDER=glm
DEEPSEEK_API_KEY=sk-…   # optional; falls back to Platform Brain key
AI_RUNTIME_FAST_MODEL=deepseek-chat
```

## Admin UI

1. Open `/admin/control` → **AI Keys**
2. Set Platform Brain (DeepSeek) and/or promo GLM key if not using env
3. Scroll to **Founder Brain Providers**
4. Enable two-model routing, pick fast/coding providers, run **Test both providers**

Policy is stored in `PlatformSettings.founderBrainProvidersJson`. Keys are never returned to the browser.

## Endpoint rules

| Endpoint | Use |
|----------|-----|
| `https://api.z.ai/api/coding/paas/v4` | **Coding Plan** — use this for Founder Brain code tier |
| `https://api.z.ai/api/paas/v4` | General API — **not** interchangeable with coding plan |

See also: `docs/ENV-VARS.md`, `apps/api/src/founder-os/glm-config.ts`.

## Verification

With admin JWT:

```http
GET  /api/admin-control/founder-brain-providers
POST /api/admin-control/founder-brain-providers/test
PATCH /api/admin-control/founder-brain-providers
```

Founder Den chat should show **Founder Brain** (not vendor names) when two-model routing is on.
