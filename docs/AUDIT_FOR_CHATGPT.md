# Audit Guide for External Review (ChatGPT / Security / Architecture)

**Safe to publish.** This file contains **no secrets**, no API keys, no database URLs, and no production tokens.

Use it with the code-only export produced by `npm run audit:export` (sibling folder `../doxedcryptofounder-audit/`).

---

## What you are reviewing

**DoxxedCrypto.digital** — a curated crypto intelligence platform:

- Public website + NestJS API + PostgreSQL (Neon)
- Founder OS (founder operating system: Copilot, build queue, integrations)
- Founder Node (desktop encrypted vault + optional local AI)
- Paper trading, scout markets, listing review, reputation graph
- Optional showcase trading bot service (`services/btc-conservative-agent`)

**Mission context:** [MISSION.md](./MISSION.md)  
**North star (command center, not dashboard):** [FOUNDER_OS_NORTH_STAR.md](./FOUNDER_OS_NORTH_STAR.md)  
**Command center architecture (Agent Bus, Queue, Attention, Timeline):** [FOUNDER_OS_COMMAND_CENTER_ARCHITECTURE.md](./FOUNDER_OS_COMMAND_CENTER_ARCHITECTURE.md)  
**Architecture narrative:** [FOUNDER_OS_AUDIT.md](./FOUNDER_OS_AUDIT.md)

---

## Repository boundaries (public vs private)

| Location | In git? | Share with ChatGPT? |
|----------|---------|---------------------|
| This monorepo (`doxedcryptofounder/`) | Yes (application source) | Yes — via `audit:export` only |
| `../doxedcryptofounder-secrets/vault/` | **Never** | **Never** |
| `../doxedcryptofounder-audit/` | **Never** (generated) | Zip + scope doc only |
| Production Neon / Railway / Vercel | Cloud | Never paste credentials |

Full layout: [REPOSITORY_LAYOUT.md](./REPOSITORY_LAYOUT.md)

---

## How to run an audit export

```bash
npm run audit:export
```

This writes a scrubbed tree to `../doxedcryptofounder-audit/` including:

- `apps/` (api, web, founder-node source — no `node_modules`)
- `packages/`, `prisma/schema.prisma`, `prisma/seed.ts`
- `docs/` (including this file)
- `services/` (bot source — no runtime `data/`)
- `AUDIT_SCOPE.txt` (generated manifest)

**Excluded by design:** all `.env*`, `scripts/` (ops + sync), `docker/`, root README, lockfiles, cron workflows that require GitHub secrets on the deploy repo.

Zip the audit folder and attach **`AUDIT_SCOPE.txt`** when asking ChatGPT or a human reviewer to audit.

---

## Review checklist (suggested)

### 1. Authentication & authorization

- `apps/api/src/auth/` — JWT guards, public routes, optional JWT
- `apps/web/src/lib/auth-options.ts` — NextAuth OAuth sync
- Paper trading session tokens: `apps/api/src/paper-trading/paper-session.util.ts`
- Admin routes: `apps/api/src/admin-control/`

### 2. Secrets & encryption

- Integration credentials: `apps/api/src/credentials/`, `security-crypto.util.ts`
- Platform showcase keys: encrypted with AES-256-GCM (key derived from `JWT_SECRET` — env name only, never paste value)
- Confirm no hardcoded secrets in `apps/` or `packages/`

### 3. Webhooks & internal endpoints

- GitHub webhook signature: `apps/api/src/founder-os/founder-os.controller.ts`
- Stripe webhook: `apps/api/src/paper-trading/paper-trading-stripe.service.ts`
- Metrics sync guard: `apps/api/src/projects/metrics-sync.guard.ts`
- Bot control secret: `services/btc-conservative-agent/bot.py` (POST routes)

### 4. Data model & PII

- `prisma/schema.prisma` — users, founders, paper trading, platform settings
- Public portfolio vs owner portfolio (email redaction)

### 5. Founder privacy stack

- `docs/PRIVACY_STACK.md`, `docs/FOUNDER_VAULT.md`, `docs/BYO_AI.md`
- `apps/api/src/founder-node/` — pairing, encrypted relay
- `apps/founder-node/` — desktop vault client

### 6. Frontend API client

- `apps/web/src/lib/api.ts` — no tokens in source; session headers for paper trading

---

## What reviewers must NOT request

- `.env`, `.env.neon`, `.env.vercel.check`, Railway tokens, OAuth client secrets
- Production `DATABASE_URL`, `JWT_SECRET`, `NEXTAUTH_SECRET`
- Admin passwords, 2FA seeds, exchange API keys

If a finding requires a secret to verify, flag the **environment variable name** and the **code path** only.

---

## Production deployment map (names only)

| Service | Role |
|---------|------|
| **Vercel** | Next.js web — `doxxedcrypto.digital` |
| **Railway** | NestJS API — `doxed-founders-website` |
| **Railway** | Showcase BTC bot — `btc-conservative-agent` |
| **Neon** | PostgreSQL — schema via Prisma |
| **GitHub** | Source — `danishhaiderau-maker/doxed-founders-website` |

Sync scripts live in `scripts/` (excluded from export). Operators run `npm run sync:all` locally with vault access.

---

## Reporting format (for ChatGPT output)

Please structure findings as:

1. **Severity** — Critical / High / Medium / Low / Informational  
2. **Location** — file path + function/route  
3. **Issue** — what can go wrong  
4. **Recommendation** — concrete fix  
5. **False positive?** — note if env-gated or intentional public route  

---

## Document history

| Date | Change |
|------|--------|
| 2026-06 | Initial auditable guide; aligns with paper session tokens, bot control secret, sync-metrics guard |

For human-maintained security notes, see [FOUNDER_OS_AUDIT.md](./FOUNDER_OS_AUDIT.md).
