# Bring Your Own AI — Step 2 of the Privacy Stack

> **You choose the model. You pay the bill. Prompts can stay on your machine.**

After Step 1 (Founder Vault), founders pick **who runs inference** for Copilot and Quick Build — without being locked to a single cloud LLM.

## Supported providers

| Provider | How it connects | Best for |
|----------|-----------------|----------|
| **OpenRouter** | API key in Builder settings | One key, many models (Claude, GPT, Llama, DeepSeek…) |
| **Jatevo** | API key in Builder settings (Step 3) | One gateway — multi-model routing; $JTVO quota on your account |
| **Ollama (Founder Node)** | Founder Node tray app + local Ollama | Zero cloud inference — prompts stay on desktop |
| **Ollama (direct URL)** | Self-hosted Ollama URL | VPS / homelab with Ollama exposed to API |
| **OpenAI, Anthropic, Gemini, DeepSeek** | Existing BYOK keys | Direct billing to each vendor |

## OpenRouter setup

1. Settings → Builder → **Bring your own AI**
2. Paste your key from [openrouter.ai/keys](https://openrouter.ai/keys)
3. Set **Default provider** → **OpenRouter (BYO models)**
4. Optional: set **Preferred model** (e.g. `anthropic/claude-3.5-haiku`, `openrouter/auto`)

Copilot and Quick Build route through OpenRouter when selected. Keys are encrypted at rest on the API — never sent to the browser after save.

## Jatevo setup

1. Settings → Builder → **Step 3 — AI on your stack**
2. Paste your key from [jatevo.ai](https://jatevo.ai) (`sk-clb-…`)
3. Set **Default provider** → **Jatevo (multi-model gateway)**
4. Optional: **Preferred model** — `auto` or a model from Jatevo’s catalog

See [JATEVO_BYOK.md](./JATEVO_BYOK.md) for endpoints, errors, and env overrides.

## What you connect (no guesswork)

| Provider | API key? | What you do in Settings → Step 3 |
|----------|----------|----------------------------------|
| Jatevo / OpenRouter / OpenAI / DeepSeek / etc. | **Yes** | Paste key in that provider’s card → **Connect & activate** |
| **Ollama (local)** | **No** | Install [Ollama](https://ollama.com) on your PC, pair Founder Node (Step 2), wait for **Ollama ready**, set **Default brain** → Ollama — **do not** use `http://127.0.0.1:11434` in the browser (cloud cannot reach your laptop) |
| Phala TEE | Yes (+ optional inference URL) | Phala card → Connect |

## Ollama via Founder Node (recommended local path)

```text
Founder Copilot (web)
       │
       ▼
@dcf/api  ── queues inference job
       │
       ▼ (poll every ~3s)
Founder Node tray app
       │
       ▼
Ollama http://127.0.0.1:11434
```

1. Install [Ollama](https://ollama.com) and pull a model: `ollama pull llama3.2`
2. Install **Founder Node** from `/founder-node` and pair with Founder OS
3. Founder Node auto-detects Ollama on heartbeat
4. Set **Default provider** → **Ollama (local via Founder Node)**

Copilot waits up to ~45s for the node to complete the job. If the node is offline, Copilot falls back to rule-based answers.

## Ollama direct URL (self-hosted)

For founders running Ollama on a server the API can reach:

1. Builder → **Connect direct URL** with base URL (e.g. `https://ollama.yourdomain.com`)
2. Set default provider to **Ollama (local via Founder Node)** — direct URL satisfies the connection check

## Copilot routing order

When you ask Copilot a question:

1. **Priority prompts** (pressing issue, standup) → rule-based, no LLM
2. **Default provider** (if connected):
   - `OLLAMA_LOCAL` → Founder Node job queue, then direct Ollama URL fallback
   - `OPENROUTER` / `JATEVO` / BYOK keys → cloud completion
3. **Fallback chain**: Jatevo → OpenRouter → DeepSeek → OpenAI → Anthropic → Gemini (any connected key)
4. **Last resort**: rule-based answer from GitHub / vault metadata

## Privacy messaging

| Mode | Prompts leave your machine? |
|------|----------------------------|
| Rule-based | No external LLM |
| Ollama + Founder Node | No — processed locally |
| BYOK (OpenRouter, OpenAI, …) | Yes — to vendor you chose |
| Step 3 Phala TEE | Encrypted TEE inference (next stage) |

## Env vars (optional platform defaults)

Founders bring their own keys. Platform operators do not need these for BYO AI:

```env
# Optional future: platform-billed OpenRouter for free tier
# OPENROUTER_PLATFORM_KEY=
```

## Next: Step 3 — Phala private inference

See `docs/PHALA_PRIVATE_AI.md` for TEE-attested Copilot when founders want cloud scale **without** shared SaaS training risk.
