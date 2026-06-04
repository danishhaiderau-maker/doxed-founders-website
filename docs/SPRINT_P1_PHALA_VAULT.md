# Sprint P1 — Phala CVM sealed vault sync/backup

## Shipped

- **`@dcf/utils` `phala-cvm-vault.ts`** — CVM status types, readiness checks, platform env probe
- **API `VaultModule`**
  - `GET /vault/cvm-capabilities` (public) — platform CVM configured flags
  - `GET /vault/cvm-status` (JWT) — relay + backup state for signed-in founder
  - `POST /vault/cvm-backup-request` (JWT) — sealed relay hash → CVM workload (or local audit log)
  - `POST /vault/cvm-verify` (JWT) — TEE attestation on latest CVM backup receipt
- **Web** — Settings → Founder Node → Step 5: **Phala CVM vault backup** panel; Mission Control trust strip badge when verified
- **Audit** — `privacyAttestationLog.kind = PHALA_CVM_VAULT_BACKUP` (no Prisma migration)
- **Smoke** — `vault-cvm-capabilities` on production

## Hybrid model

| Layer | Vault data |
| --- | --- |
| Founder Node | Plaintext `~/FounderVault/` |
| Neon relay | Metadata + AES-GCM encrypted blob (server cannot decrypt) |
| Phala CVM (optional) | Blob SHA-256 + relay metadata sealed in TEE workload |

## Railway env (full CVM path)

```env
PHALA_CVM_BACKUP_URL=https://your-cvm-workload.example/vault/backup
PHALA_CVM_WORKLOAD_ID=optional-workload-slug
PHALA_CVM_API_KEY=...          # optional; falls back to PHALA_API_KEY
PHALA_API_KEY=...              # platform inference + CVM auth
PHALA_INFERENCE_URL=https://api.redpill.ai/v1
PHALA_MODEL=phala/deepseek-chat-v3-0324
```

Without `PHALA_CVM_BACKUP_URL`, founders still get **local relay snapshots** in the attestation log; verify uses Phala TEE reports when Phala is connected.

## CVM workload contract

`POST {PHALA_CVM_BACKUP_URL}` (or `{base}/vault/backup`):

```json
{
  "blobHash": "sha256 hex of encryptedVaultBlob",
  "relayUpdatedAt": "ISO timestamp",
  "memoryMode": "FOUNDER_NODE",
  "deviceLabel": "Founder Node",
  "taskCount": 0,
  "workloadId": "optional"
}
```

Response (success):

```json
{ "ok": true, "backupId": "...", "signing_address": "0x..." }
```

## Verification

```bash
npm run build:api
npm run build --workspace=@dcf/web
npm run smoke:test
```

## Next

**Phala P2** — CVM-side credential unwrap for platform keys ([PHALA_ARCHITECTURE_ALIGNMENT.md](./PHALA_ARCHITECTURE_ALIGNMENT.md)).
