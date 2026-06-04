# Sprint P0 — Data-class audit & public/private split

## Shipped

- **`docs/DATA_CLASSIFICATION.md`** — canonical six-class model
- **`@dcf/utils` `data-classification.ts`** — registry, `redactForbiddenFields()`, static audit
- **`npm run audit:data-classes`** — CI-friendly compliance check
- **API `PrivacyModule`**
  - `GET /privacy/data-classes`
  - `GET /privacy/audit`
  - `GET /privacy/my-boundaries` (JWT)
- **Web** — Trust Center data-class panel; Settings privacy boundaries for founders
- **Smoke** — `privacy-data-classes` on production

## Verification

Existing code paths already avoid returning raw `integrationCredential.token` to the browser (builder settings, sealed status, exchange status). P0 documents and enforces this contract for future routes.

## Next

**Phala P2** — CVM-side credential unwrap ([PHALA_ARCHITECTURE_ALIGNMENT.md](./PHALA_ARCHITECTURE_ALIGNMENT.md)). P1 vault backup: [SPRINT_P1_PHALA_VAULT.md](./SPRINT_P1_PHALA_VAULT.md).
