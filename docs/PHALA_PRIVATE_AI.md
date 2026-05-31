# Phala private AI — integration options for Founder OS

Founders on doxxedcrypto.digital need AI that respects repo secrets, strategy, and token plans. Phala Cloud runs workloads in TEEs (Intel TDX / AMD SEV), which fits “private inference” better than sending prompts to a shared SaaS API.

## Integration tiers (recommended order)

### Tier 1 — Private chat LLM (fastest win)

Route **Founder Copilot** chat completions through Phala when a founder enables “Private AI” in Builder settings.

| Piece | Approach |
|-------|----------|
| API | Add `PHALA_API_KEY` + model endpoint in vault; new `PhalaProvider` in `BuilderService.tryCopilotChatCompletion` |
| UI | Builder → “Private AI (Phala)” toggle + model picker |
| Data | Same context block as today (GitHub memory, tasks) — sent to Phala endpoint, not OpenAI/DeepSeek |
| Safety | No founder API keys in browser; platform or founder-billed Phala key server-side only |

Phala docs: https://docs.phala.com — use their hosted inference or deploy a model in a TEE.

### Tier 2 — Founder Node + local vault (already started)

Founder Node tray app keeps `.env` and repo memory local. Phala complements this:

- **Local-only mode**: Node holds secrets; Copilot uses rule-based + GitHub sync (current).
- **Hybrid mode**: Node encrypts a bundle; API forwards to Phala with attestation check before decrypting in TEE.

### Tier 3 — TEE-attested agents

Long-running “Chief of Staff” agents (watch GitHub, draft updates, raise readiness) run as Phala workers:

1. Cron / webhook triggers worker with `founderId` + signed JWT.
2. Worker reads repo memory from Neon (encrypted at rest) or Founder Node push.
3. Outputs: inbox items, suggested build updates, X drafts — same event bus as today.

### Tier 4 — Community “Ask this project”

Public project page Q&A uses a **redacted** context (no secrets). Optional Phala deployment per project for founders who pay for isolated inference.

## Architecture sketch

```text
Founder Copilot (web)
       │
       ▼
  @dcf/api  ──► Builder settings: defaultProvider = PHALA
       │
       ├── Rule-based (pressing issue, standup) — no external call
       ├── DeepSeek / OpenAI — founder BYOK
       └── Phala TEE inference — platform or founder key
                 │
                 ▼
           GitHub + tasks context (no raw .env)
```

## Env vars (production)

```env
PHALA_API_KEY=...
PHALA_INFERENCE_URL=https://...  # Phala Cloud endpoint
PHALA_MODEL=deepseek-v3  # or model slug from Phala
```

## Privacy messaging for founders

- **Cloud memory**: GitHub + tasks on Neon (encrypted DB).
- **Private AI (Phala)**: Prompts processed in TEE; not used for public model training.
- **Founder Node**: Secrets never leave the machine unless founder opts into hybrid sync.

## Next implementation steps

1. Add `AiProvider.PHALA` + credential provider in Prisma/schema.
2. Implement `completionWithProvider('PHALA', ...)` in `builder.service.ts`.
3. Builder settings UI: Phala API key + “Use for Copilot chat” toggle.
4. Document in Founder OS Connected stack: “Private AI (Phala)” badge when connected.

## References

- Phala Cloud: https://phala.network
- Phala docs: https://docs.phala.com
- Founder Node vault: `/founder-node` in the web app
