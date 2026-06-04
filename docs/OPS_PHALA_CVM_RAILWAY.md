# Ops: Phala CVM + Railway (P1 / P2)

Production today reports `platformCvmConfigured: false` until you complete this flow.

**Important:** The confidential workload runs on **Phala Cloud (TEE)**. **Railway** only stores URLs and API keys so `doxed-founders-website` can call into the CVM.

```text
Founder browser → Vercel → Railway API
                              │
                              ├── Neon (public + encrypted relay)
                              └── HTTPS → Phala CVM (unwrap + vault backup)
```

---

## One-command Step 1 (after secrets are filled)

```bash
npm run bootstrap:phala-cvm-env   # once — JWT_SECRET + PHALA_CVM_API_KEY
# Edit vault/.env.phala → add PHALA_CLOUD_API_KEY=phak_... (Phala Cloud dashboard)
npm run phala-cvm:step1           # deploy CVM → apply Railway → probe
```

---

## Step 1 — Bootstrap secrets file

```bash
npm run bootstrap:phala-cvm-env
```

Creates `../doxedcryptofounder-secrets/vault/.env.phala` with:

- `JWT_SECRET` copied from `.env.vercel.check` (must match Railway API)
- Random `PHALA_CVM_API_KEY` (workload bearer token)

Add tokens:

```env
PHALA_CLOUD_API_KEY=phak_...   # https://cloud.phala.network/dashboard/tokens — required to deploy CVM
PHALA_API_KEY=phak_...         # optional same or separate — platform Phala inference
```

---

## Step 2 — Deploy CVM workload on Phala Cloud

Automated:

```bash
npm run deploy:phala-cvm
```

Manual:

Repo: `workers/phala-cvm-workload/`

1. Build and push Docker image (or use Phala compose `build: .` if supported).
2. Create CVM with port **8080 → 8787**.
3. Encrypted Secrets on the CVM:

| Key | Value |
| --- | --- |
| `JWT_SECRET` | Same as Railway |
| `CVM_WORKLOAD_AUTH_TOKEN` | Same as `PHALA_CVM_API_KEY` in `.env.phala` |

4. When status is **running**, open **INGRESS** and copy the HTTPS URL, e.g.:

```text
https://<app-id>-8080.dstack-prod5.phala.network
```

5. Paste into `.env.phala`:

```env
PHALA_CVM_BASE_URL=https://<app-id>-8080.dstack-prod5.phala.network
```

6. Probe workload:

```bash
# PowerShell — set bearer from .env.phala PHALA_CVM_API_KEY
$env:PHALA_CVM_BASE_URL="https://..."
$env:PHALA_CVM_API_KEY="..."
npm run probe:phala-cvm
```

---

## Step 3 — Wire Railway (API host)

```bash
npm run print:railway:phala-cvm   # optional paste file
npm run apply:railway:phala-cvm   # GraphQL upsert + redeploy
```

Sets on **doxed-founders-website** only:

| Variable | Purpose |
| --- | --- |
| `PHALA_CVM_BACKUP_URL` | Base URL (API appends `/vault/backup`) |
| `PHALA_CVM_UNWRAP_URL` | Base URL (API appends `/secrets/unwrap`) |
| `PHALA_API_KEY` | Platform Phala inference |
| `PHALA_CVM_API_KEY` | Bearer to CVM workload |
| `PHALA_INFERENCE_URL` | Optional, default Redpill |
| `PHALA_MODEL` | Optional |
| `PHALA_CVM_WORKLOAD_ID` | Optional receipt label |

Requires `RAILWAY_TOKEN` in `.env.phala` or `.env.x.secrets`.

---

## Step 4 — Verify production

```bash
npm run probe:phala-cvm
npm run smoke:test
```

Expect:

```json
{ "platformCvmConfigured": true, "backupUrlSet": true }
{ "platformCvmUnwrapConfigured": true, "unwrapUrlSet": true }
```

In the app: **Settings → Founder Node → Step 5** — CVM vault backup and credential unwrap panels should show platform CVM configured.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Capabilities still `false` after apply | Wait for Railway redeploy (~2 min); re-run probe |
| CVM `/health` 503 | `JWT_SECRET` missing on Phala CVM |
| Unwrap always `platform_encrypted` | CVM unreachable, wrong bearer, or `JWT_SECRET` mismatch vs Railway |
| `401` on CVM | `PHALA_CVM_API_KEY` on Railway ≠ `CVM_WORKLOAD_AUTH_TOKEN` on Phala |

---

## After ops (P2 product polish)

See [SPRINT_P2_PHALA_SEAL.md](./SPRINT_P2_PHALA_SEAL.md) **Next**:

- Migrate GitHub PAT into sealed integration row
- Settings toggle for `secretsStorageMode = PHALA_SEALED`
