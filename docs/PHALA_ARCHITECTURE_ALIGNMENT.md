# Phala architecture alignment (ChatGPT review vs DoxxedCrypto)

This doc maps the external “Founder OS + Phala TEE + Founder Vault” proposal to what we ship today and what to build next.

## Stack reality (not Supabase)

| ChatGPT assumption | Actual |
| --- | --- |
| Supabase for public data | **Neon Postgres** + Prisma |
| All secrets in DB plaintext | **AES-encrypted** `integrationCredential` rows |
| No privacy layer | **Phala TEE inference**, attestation UI, Founder Node vault relay |
| Fragmented memory | **Sprint 1:** `memoryGraph` on builder settings + GitHub `.github/founder-os/` + vault files |

## Hybrid model (aligned)

**Public layer (Neon)** — projects, feed, DDollar, rankings, scout voting, trust center. No API keys, no raw vault blobs.

**Private layer** — today split across:

1. **Founder Node** — `~/FounderVault` on disk; pairing + heartbeat to API.
2. **Platform** — encrypted credentials, `memoryGraph`, copilot memory, builder agent run metadata.
3. **Phala** — optional `PHALA` provider for confidential chat inference + attestation receipts.

ChatGPT’s “move only vault, agent memory, secrets into Phala CVM” is the right **P2+** direction without migrating the whole app.

## What we already match

- **“Continue where I left off”** — `copilotResume`, project memory, memory graph prefix on all LLM paths.
- **Founder Vault branding** — Founder Node hub, memory storage modes, attestation dashboard.
- **AI router (partial)** — default provider + fallback chain in `BuilderService.tryCopilotChatCompletion`; workforce/orchestrator intents in copilot.
- **Sprint 2 UX** — Mission Control shows deploy health strip; full **Hybrid Control Plane** lives under **Settings → Autopilot**; hero chat shows **Founder Brain** / **Builder Agent** instead of per-vendor dropdowns.

## Gaps vs ChatGPT P0–P4

| Priority | Proposal | Status | Next step |
| --- | --- | --- | --- |
| P1 | Mission State (continue where I left off) | **Shipped (Sprint 3)** | Mission Control panel, resume + continue flows, after-build sync |
| P0 | Split public/private with Phala TEE | **Shipped** | [DATA_CLASSIFICATION.md](./DATA_CLASSIFICATION.md), `/privacy/*`, `audit:data-classes` |
| P1 vault | Founder Vault flagship in TEE | Partial (local vault + graph) | Phala Cloud CVM for sealed vault sync backup |
| P2 | Seal API keys in Phala, not DB | **Shipped (Sprint 6)** — audited unwrap + Phala inference-only tier | Phala CVM-side decrypt for platform keys |
| P3 | “Founder Brain” task router | **Shipped (Sprint 4)** | Task classify + provider order in API; code asks dispatch Builder |
| P4 | Attestation button on vault | **Shipped (Sprint 5)** | Mission Control trust strip + Settings `#founder-attestation` |

## Recommended phases

1. **Now** — Neon public data + encrypted credentials + Founder Node vault + memory graph (shipped).
2. **Next** — Task-based router in API (`research` → DeepSeek, `code` → Cursor, etc.) without exposing vendor names in UI.
3. **Then** — Phala CVM workload for: credential unwrap, agent run state, vault blob encryption keys.
4. **Later** — Portable vault export; cross-device agent resume entirely inside TEE.

## Verdict

The ChatGPT architecture is **directionally better** for moat and marketing truth (“private by default”) but **not a greenfield rewrite**. Integrate by **deepening Phala + vault on sensitive paths** while keeping Neon/Vercel/Railway for the public product — exactly the hybrid cost model they describe.
