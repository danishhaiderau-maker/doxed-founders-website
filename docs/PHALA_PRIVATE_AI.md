# Phala Private AI — Step 3 of the Privacy Stack

> **Confidential inference in a hardware TEE — not shared SaaS training.**

After Step 1 (Founder Vault) and Step 2 (BYO AI), founders can route **Founder Copilot** chat through Phala's TEE-backed inference API. Prompts use the same GitHub + tasks context as other LLM providers, but run on confidential hardware when Phala is selected.

## What shipped

| Piece | Location |
|-------|----------|
| `AiProvider.PHALA` | Prisma schema + `@dcf/utils` provider list |
| Copilot routing | `BuilderService.tryCopilotChatCompletion` — Phala first when default, fallback in provider order |
| Connect API | `POST /builder/providers/phala-connect` |
| Builder UI | Settings → Builder → **Private AI — Phala TEE (Step 3)** |
| Connected stack badge | Founder Vault memory panel when Phala is ready |
| Platform credits | Optional `PHALA_API_KEY` on Railway — founders without a user key can still use Phala when enabled |

## Setup (founder BYOK)

1. Settings → Builder → **Private AI — Phala TEE**
2. Paste your Phala / Redpill API key
3. Optional: custom inference URL (default `https://api.redpill.ai/v1`) and model slug
4. Set **Default provider** → **Private AI (Phala TEE)**
5. Ask Copilot — answers show **Private AI (Phala TEE)** in the chat badge

Keys are encrypted at rest on the API. The browser never stores the key after save.

## Platform-billed inference (optional)

Set on Railway (API service only):

```env
PHALA_API_KEY=...
PHALA_INFERENCE_URL=https://api.redpill.ai/v1
PHALA_MODEL=phala/deepseek-chat-v3-0324
```

When set, founders see **Platform Phala credits enabled** in Builder settings even without pasting their own key. User keys take precedence when connected.

## Architecture

```text
Founder Copilot (web)
       │
       ▼
@dcf/api  ──► Builder settings: defaultProvider = PHALA
       │
       ├── Rule-based (pressing issue, standup) — no external call
       ├── DeepSeek / OpenAI / OpenRouter — founder BYOK (Step 2)
       ├── Ollama — Founder Node (Step 2)
       └── Phala TEE inference — user key or PHALA_API_KEY
                 │
                 ▼
           GitHub + tasks context (no raw .env)
```

## Privacy messaging

- **Cloud memory**: GitHub + tasks on Neon (encrypted DB).
- **Private AI (Phala)**: Prompts processed in TEE; not used for public model training.
- **Founder Node**: Secrets never leave the machine unless founder opts into hybrid sync.

## Roadmap (Steps 4–5)

4. **Founder Node v2** ✅ — see `docs/FOUNDER_NODE_V2.md`
5. **Attestation dashboard** ✅ — see `docs/ATTESTATION_DASHBOARD.md`

## References

- Phala Cloud: https://phala.network
- Phala docs: https://docs.phala.com
- Confidential AI API: https://docs.phala.com/phala-cloud/confidential-ai/confidential-model/confidential-ai-api
- Step 1: `docs/FOUNDER_VAULT.md`
- Step 2: `docs/BYO_AI.md`
