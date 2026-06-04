# Sealed secrets (Sprint 6)

## What ships

- **AES-256-GCM** encryption for `integrationCredential.token` (derived from `JWT_SECRET` via scrypt).
- **Central unwrap** through `SealedCredentialsService` with purpose checks and `SECRET_ACCESS` audit rows in `PrivacyAttestationLog`.
- **Phala inference-only tier**: new Phala connects stamp `secretsSeal.tier = phala_inference_only` so unwrap is only allowed for Phala chat, attestation, connect verify, and status probes.
- **No raw keys in the browser** — Settings and Mission Control show status only.

## API

| Endpoint | Description |
| --- | --- |
| `GET /builder/settings` | Includes `secretsStatus` summary |
| `GET /builder/secrets-status` | Full sealed credential status |
| `GET /attestation/dashboard` | Adds `secretsStorage` block for trust strip |

## Data classes

Full registry: [DATA_CLASSIFICATION.md](./DATA_CLASSIFICATION.md) · API `GET /privacy/data-classes`

| Class | Storage | Unwrap |
| --- | --- | --- |
| Public product (Neon) | Projects, feed, rankings | N/A |
| Integration API keys | Encrypted `integrationCredential` | Server-only, audited |
| Phala user key | Same + inference-only seal | Phala TEE paths only |
| GitHub PAT | `gitHubConnection.accessTokenEncrypted` | GitHub API service (encrypt at connect) |
| Founder Vault blobs | Founder Node disk + optional encrypted relay | Never decrypted on API |

## CVM unwrap (P2)

| Endpoint | Description |
| --- | --- |
| `GET /vault/cvm-seal-capabilities` | Public — `PHALA_CVM_UNWRAP_URL` configured |
| `GET /vault/cvm-seal-status` | JWT — readiness checks for signed-in founder |
| `GET /builder/secrets-status` | Adds `cvmUnwrapReady`, `activeUnwrapPath` |

When `PHALA_CVM_UNWRAP_URL` is set, `SealedCredentialsService.unwrap` tries CVM HTTP first, then falls back to local AES. See [SPRINT_P2_PHALA_SEAL.md](./SPRINT_P2_PHALA_SEAL.md).

## Next

- Migrate GitHub PAT into sealed integration row with `github_token` purpose.
- User-selectable `secretsStorageMode = PHALA_SEALED` toggle in Settings UI.
