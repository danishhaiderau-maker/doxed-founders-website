# Documentation Index

Everything in `docs/` is **safe to commit** — no production secrets.

---

## Start here

| Doc | Audience | Description |
|-----|----------|-------------|
| [MISSION.md](./MISSION.md) | Everyone | Why DoxxedCrypto.digital exists, culture, Founder OS & Node, BYOK |
| [REPOSITORY_LAYOUT.md](./REPOSITORY_LAYOUT.md) | Developers & auditors | Public git vs private vault vs audit export |
| [AUDIT_FOR_CHATGPT.md](./AUDIT_FOR_CHATGPT.md) | ChatGPT / security reviewers | How to audit without secrets |

---

## Privacy stack (Founder OS)

| Step | Doc |
|------|-----|
| Overview | [PRIVACY_STACK.md](./PRIVACY_STACK.md) |
| 1 — Founder Vault | [FOUNDER_VAULT.md](./FOUNDER_VAULT.md) |
| 2 — Bring Your Own AI | [BYO_AI.md](./BYO_AI.md) |
| 3 — Phala Private AI | [PHALA_PRIVATE_AI.md](./PHALA_PRIVATE_AI.md) |
| 4 — Founder Node v2 | [FOUNDER_NODE_V2.md](./FOUNDER_NODE_V2.md) |
| 5 — Attestation | [ATTESTATION_DASHBOARD.md](./ATTESTATION_DASHBOARD.md) |

---

## Architecture & security

| Doc | Description |
|-----|-------------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | **System architecture** — diagrams, data flow, how privacy is preserved (public-safe) |
| [FOUNDER_OS_AUDIT.md](./FOUNDER_OS_AUDIT.md) | Architecture & security narrative (May 2026) |
| [DATA_CLASSIFICATION.md](./DATA_CLASSIFICATION.md) | Public vs private data classes (P0) |
| [PHALA_ARCHITECTURE_ALIGNMENT.md](./PHALA_ARCHITECTURE_ALIGNMENT.md) | Hybrid Phala / Neon roadmap |
| [SECRETS_STORAGE.md](./SECRETS_STORAGE.md) | Sealed credentials & CVM unwrap |
| [HYBRID_CONTROL_PLANE.md](./HYBRID_CONTROL_PLANE.md) | Control plane / Autopilot |
| [PROJECT_AGENT_ARCHITECTURE.md](./PROJECT_AGENT_ARCHITECTURE.md) | Project agents, vault, BYOK brain |
| [FOUNDER_COPILOT_SETUP.md](./FOUNDER_COPILOT_SETUP.md) | Copilot setup |

---

## Operations & deploy

| Doc | Description |
|-----|-------------|
| [PRODUCTION_CHECKLIST.md](./PRODUCTION_CHECKLIST.md) | Production checklist |
| [railway-deploy.md](./railway-deploy.md) | Railway API |
| [vercel-deploy.md](./vercel-deploy.md) | Vercel web |

---

## Desktop app

| Doc | Description |
|-----|-------------|
| [../apps/founder-node/README.md](../apps/founder-node/README.md) | Founder Node install & pairing |

---

## External audit workflow

```bash
npm run audit:export
# Zip ../doxedcryptofounder-audit/ and share with AUDIT_SCOPE.txt + docs/AUDIT_FOR_CHATGPT.md
```
