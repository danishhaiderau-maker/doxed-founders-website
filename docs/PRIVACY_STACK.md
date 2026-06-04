# Privacy Stack — Founder OS

Complete five-step privacy architecture for doxxed founders.

| Step | Name | Doc | Builder UI |
|------|------|-----|------------|
| 1 | Founder Vault | [FOUNDER_VAULT.md](./FOUNDER_VAULT.md) | Settings → Builder → Memory storage |
| 2 | Bring Your Own AI | [BYO_AI.md](./BYO_AI.md) | Bring your own AI (Step 2) |
| 3 | Phala Private AI (TEE) | [PHALA_PRIVATE_AI.md](./PHALA_PRIVATE_AI.md) | Private AI — Phala TEE (Step 3) |
| 4 | Founder Node v2 | [FOUNDER_NODE_V2.md](./FOUNDER_NODE_V2.md) | Founder Node v2 (Step 4) |
| 5 | Attestation dashboard | [ATTESTATION_DASHBOARD.md](./ATTESTATION_DASHBOARD.md) | Attestation dashboard (Step 5) |

## Quick setup order

1. Pair **Founder Node** (`/founder-node`) and set memory mode to **Founder Vault**
2. Connect **OpenRouter** or **Ollama** (Step 2)
3. Connect **Phala** and optionally set as default Copilot provider (Step 3)
4. Rebuild **vector index** on Founder Node v0.4.0+ (Step 4)
5. **Verify TEE** and **scan vault integrity** in attestation dashboard (Step 5)

## Secrets vault

Local secrets live in `../doxedcryptofounder-secrets/vault/` (never commit). Relink after clone:

```bash
npm run secrets:link
```

## Audit export (ChatGPT / external review)

Safe, code-only snapshot — **no `.env`, no tokens**:

```bash
npm run audit:export
```

Output: `../doxedcryptofounder-audit/` + `AUDIT_SCOPE.txt`.  
Guide: [docs/AUDIT_FOR_CHATGPT.md](./AUDIT_FOR_CHATGPT.md) · Entry: [AUDIT.md](../AUDIT.md)

## Production ops

| Task | Command |
|------|---------|
| Health + reminders | `npm run housekeeping` |
| Fix admin TOTP (JWT sync) | `npm run fix:admin-2fa` |
| X automation bootstrap | `npm run finish:x-production` |
| First X sync | `npm run run:first-x-sync` |
| Diagnose X bearer token | `npm run diagnose:x-sync` |
| Railway redeploy | `npm run redeploy:railway` |
| Vercel web deploy | `npm run deploy:web` |
| Smoke test | `npm run smoke:test` |

## Secrets vault

Local secrets live in `../doxedcryptofounder-secrets/vault/` (never commit). Relink after clone:

```bash
npm run secrets:link
```
