# Jatevo BYOK — Multi-model gateway

Founder OS connects to [Jatevo](https://jatevo.ai) as an OpenAI-compatible provider. One API key routes to multiple hosted models (GPT, Qwen, Kimi, GLM, Cerebras, etc.) with optional **$JTVO**-backed daily quota.

## Setup

1. Sign in at [jatevo.ai](https://jatevo.ai) and create an application key (`sk-clb-…`).
2. Settings → Builder → Founder Node **Step 3 — AI on your stack**.
3. Paste the key in the **Jatevo** card → **Connect & activate**.
4. Set **Default brain** → **Jatevo (multi-model gateway)**.
5. Optional **Preferred model**: `auto` (default), or a model id from `GET /v1/models` (e.g. `qwen3.5-plus`).

Keys are encrypted at rest on the API — never returned to the browser after save.

## API surface (reference)

| Item | Value |
|------|--------|
| Base URL | `https://2.lb.jatevo.ai/v1` |
| List models | `GET /v1/models` (no quota cost) |
| Chat | `POST /v1/chat/completions` |
| Docs | [jatevo.ai/docs](https://jatevo.ai/docs) |

Platform operators may override the base URL:

```env
JATEVO_BASE_URL=https://2.lb.jatevo.ai/v1
```

## Copilot routing

When Jatevo is connected, Founder Brain may prefer it for **code** and **strategy** tasks (after DeepSeek / alongside OpenRouter). Your **default brain** is always tried first for general questions.

## Common errors

| HTTP | Meaning |
|------|---------|
| 401 | Invalid or missing API key |
| 403 | Key disabled — refresh wallet verification on Jatevo |
| 429 | Daily quota exhausted — UTC reset or increase $JTVO holdings |
| 502 / 504 | Upstream route busy — retry |

## Related

- [BYO_AI.md](./BYO_AI.md) — full Bring Your Own AI stack
- [PHALA_PRIVATE_AI.md](./PHALA_PRIVATE_AI.md) — TEE inference alternative
