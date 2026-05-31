# Founder Vault — Step 1 of the Privacy Stack

> **Own your memory. Own your agents. Own your company intelligence.**

Founder Vault is the memory layer of Founder OS. Founders choose where goals, tasks, decisions, and private notes live — from cloud (build in public) to fully self-custodied on their machine.

## Memory tiers

| Mode | Where memory lives | Cloud sees |
|------|-------------------|------------|
| **Cloud (Founder OS)** | Neon database + GitHub optional | Full build context |
| **GitHub repo files** | `.github/founder-os/` in your repo | Whatever you commit |
| **This browser only** | `localStorage` | Nothing |
| **Local + encrypted relay** | Browser + metadata snapshot | Goal, task count, optional encrypted blob |
| **Founder Vault (Founder Node)** | `~/FounderVault/` on desktop | Metadata + AES-256-GCM blob only |

## Vault files (Founder Node)

On disk at `~/FounderVault/`:

| File | Purpose | Cloud relay |
|------|---------|-------------|
| `project-context.md` | Current goal, project narrative | Encrypted in blob |
| `roadmap.md` | Product roadmap | Encrypted in blob |
| `tasks.json` | Open tasks | Encrypted in blob |
| `decisions.md` | Decision log | Encrypted in blob |
| `private-notes.md` | Investor notes, confidential plans | Encrypted in blob |
| `build-history.jsonl` | Local audit trail | Local only |
| `node-config.json` | Pairing credentials | Local only |

## Zero-knowledge relay

Founder Node encrypts sensitive vault JSON with a key derived from the node token (`deriveVaultKey`). The API stores:

- **Metadata:** current goal, task count, device label, timestamp
- **Encrypted blob:** server cannot decrypt

Copilot uses metadata for answers and explicitly states that private contents stay on the founder's machine.

## Architecture

```text
Founder OS Web
      │
      ▼
@dcf/api (control plane)
      │
      ├── PLATFORM → Neon + GitHub
      ├── FOUNDER_NODE → metadata relay only
      └── Copilot → respects memoryStorageMode

Founder Node (desktop)
      │
      └── ~/FounderVault/ plaintext files
              │
              └── encrypt → POST /founder-node/sync
```

## Roadmap (Steps 2–5)

1. **Founder Vault** ✅
2. **Bring Your Own AI** ✅ — OpenRouter, Ollama via Founder Node, BYOK routing
3. **Private AI inference (Phala TEE)** ✅ — TEE Copilot routing, Builder connect, platform credits optional
4. **Founder Node v2** ✅ — see `docs/FOUNDER_NODE_V2.md`
5. **Attestation dashboard** ✅ — see `docs/ATTESTATION_DASHBOARD.md`

See `docs/BYO_AI.md` for Step 2 setup.

## Setup

1. Settings → Builder → **Founder Vault (Founder Node)**
2. Install Founder Node from `/founder-node`
3. Generate pairing code → enter in tray app
4. Edit files in `~/FounderVault/` — sync every ~60s

See also: `docs/PHALA_PRIVATE_AI.md` for Step 3. Full stack index: `docs/PRIVACY_STACK.md`.
