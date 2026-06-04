# Sprint P2 — Phala CVM credential unwrap

## Shipped

- **`@dcf/utils` `phala-cvm-seal.ts`** — CVM unwrap platform config, readiness checks, unwrap path labels
- **`secrets-storage.ts`** — `cvm_sealed` tier, `SecretsUnwrapPath`, `secretsTierForStorageMode`
- **API**
  - `phala-cvm-unwrap.client.ts` — HTTP `POST …/secrets/unwrap` to CVM workload
  - `CvmSealService` — platform readiness + `tryUnwrapViaCvm`
  - `SealedCredentialsService.unwrap` — CVM first when configured, AES fallback + audited path suffix
  - `GET /vault/cvm-seal-capabilities` (public)
  - `GET /vault/cvm-seal-status` (JWT)
  - `GET /builder/secrets-status` — adds `cvmUnwrapReady`, `activeUnwrapPath`, `activeUnwrapPathLabel`
- **Web** — Settings → Founder Node → attestation step: **Phala CVM credential unwrap** panel; sealed secrets strip shows unwrap path
- **Smoke** — `vault-cvm-seal-capabilities` on production

## Hybrid unwrap model

| Path | When | Where keys decrypt |
| --- | --- | --- |
| `platform_encrypted` | Default or CVM unreachable | API host (AES-256-GCM from `JWT_SECRET`) |
| `cvm_sealed` | `PHALA_CVM_UNWRAP_URL` + API key set | Phala Confidential VM workload |

Raw keys never reach the browser. Every unwrap writes `privacyAttestationLog` kind `SECRET_ACCESS` with `[cvm_sealed]` or `[platform_encrypted]` suffix.

## Railway env

```env
PHALA_CVM_UNWRAP_URL=https://your-cvm-workload.example
PHALA_CVM_API_KEY=...          # optional; falls back to PHALA_API_KEY
PHALA_API_KEY=...              # platform inference + CVM auth
PHALA_CVM_WORKLOAD_ID=optional-workload-slug
JWT_SECRET=...                 # platform AES fallback + credential encryption at rest
```

Related P1 vault backup (separate URL):

```env
PHALA_CVM_BACKUP_URL=https://your-cvm-workload.example/vault/backup
```

## CVM workload contract

`POST {PHALA_CVM_UNWRAP_URL}` (or `{base}/secrets/unwrap`):

```json
{
  "encryptedToken": "AES-GCM blob from integrationCredential.token",
  "provider": "cursor",
  "purpose": "cursor_dispatch",
  "userId": "founder-user-id",
  "workloadId": "optional"
}
```

Response (success):

```json
{ "ok": true, "plaintext": "sk-..." }
```

The workload must use the same platform encryption material as the API (`JWT_SECRET` derivation) or accept re-wrapped blobs in a future migration.

## Verification

```bash
npm run build:utils
npm run build:api
npm run build --workspace=@dcf/web
npm run smoke:test
```

Production:

```bash
API_URL=https://doxxedcrypto.digital npm run smoke:test
```

## Next

- Migrate GitHub PAT into sealed integration row
- User-facing toggle for `secretsStorageMode = PHALA_SEALED` in Settings
- Agent run state and vault blob keys entirely inside CVM
