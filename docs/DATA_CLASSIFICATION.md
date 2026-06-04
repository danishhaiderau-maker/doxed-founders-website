# Data classification (P0)

Canonical split between **public product data** (Neon) and **private founder data** (encrypted credentials, Founder Node vault, memory graph).

## Six data classes

| Class | Where it lives | Who can read it |
|-------|----------------|-----------------|
| `public_product` | Neon | Anyone via public APIs |
| `founder_private` | Neon + optional device sync | Signed-in founder only |
| `sealed_credential` | Neon (AES-256-GCM) | Server unwrap only, audited |
| `founder_node_relay` | Job queue + local vault | Paired Founder Node + owner |
| `audit_telemetry` | `privacyAttestationLog` | Owner + admins |
| `platform_identity` | `user` / auth tables | Auth flows only |

## Rules

1. **`@Public()` routes** may return only `public_product` fields.
2. **Forbidden in any browser response:** `token`, `accessTokenEncrypted`, `webhookSecret`, `secretHash`, passwords, raw API keys.
3. **Unwrap path:** `SealedCredentialsService` with purpose checks → `SECRET_ACCESS` log (Sprint 6).
4. **Memory graph / vault blobs:** never on Discover, Feed, or project pages for other users.

## Machine-readable registry

- `packages/utils/src/data-classification.ts` — `PRISMA_MODEL_CLASSIFICATION`, `API_ROUTE_CLASSIFICATION`, `redactForbiddenFields()`
- Run: `npm run audit:data-classes`

## API

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /privacy/data-classes` | Public | Catalog + static audit summary |
| `GET /privacy/audit` | Public | Static + runtime counts (no secrets) |
| `GET /privacy/my-boundaries` | JWT | What applies to the signed-in founder |

## Phala P1 (shipped)

CVM sealed vault relay backup — see [SPRINT_P1_PHALA_VAULT.md](./SPRINT_P1_PHALA_VAULT.md). Next: Phala P2 CVM credential unwrap.
