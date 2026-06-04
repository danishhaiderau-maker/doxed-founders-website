# Phala CVM workload (P1 backup + P2 unwrap)

Minimal HTTP service that runs **inside Phala Confidential VM**, not on Railway.

| Route | Purpose |
| --- | --- |
| `GET /health` | Liveness; requires `JWT_SECRET` for 200 |
| `POST /vault/backup` | Sealed vault backup receipt (P1) |
| `POST /secrets/unwrap` | Decrypt `integrationCredential` blobs (P2) |

Auth: `Authorization: Bearer` must match `CVM_WORKLOAD_AUTH_TOKEN`, `PHALA_CVM_API_KEY`, or `PHALA_API_KEY` on the workload.

Decrypt uses the same `JWT_SECRET` + scrypt salt as `apps/api` (`dcf-security-v1`).

## Local test

```bash
cd workers/phala-cvm-workload
set JWT_SECRET=your-32-char-production-secret
set CVM_WORKLOAD_AUTH_TOKEN=test-token
npm start
```

```bash
curl -s http://127.0.0.1:8787/health
curl -s -X POST http://127.0.0.1:8787/vault/backup -H "Authorization: Bearer test-token" -H "Content-Type: application/json" -d "{\"blobHash\":\"abc\"}"
```

## Phala Cloud deploy

1. Build image: `docker build -t your-registry/dcf-phala-cvm-workload:latest .`
2. Push to a registry Phala can pull.
3. In [Phala Cloud](https://cloud.phala.com), **Create CVM** → docker-compose:

```yaml
services:
  cvm:
    image: your-registry/dcf-phala-cvm-workload:latest
    ports:
      - "8080:8787"
```

4. **Encrypted Secrets** (must match Railway):

| Key | Value |
| --- | --- |
| `JWT_SECRET` | Same as Railway `doxed-founders-website` |
| `CVM_WORKLOAD_AUTH_TOKEN` | Same as `PHALA_CVM_API_KEY` in vault `.env.phala` |

5. Copy **INGRESS** URL → `PHALA_CVM_BASE_URL` in `vault/.env.phala`.

Full ops: [docs/OPS_PHALA_CVM_RAILWAY.md](../../docs/OPS_PHALA_CVM_RAILWAY.md).
