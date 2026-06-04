# Repository Layout — Public vs Private

This document defines **what belongs in git**, what stays **outside the repo**, and how to avoid mixing sensitive files with publishable source.

---

## Three zones

```text
┌─────────────────────────────────────────────────────────────────┐
│  PUBLIC (git) — doxedcryptofounder/                             │
│  Application source, docs, Prisma schema, services, packages  │
│  No .env · no production tokens · no vault paste files          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ npm run audit:export
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  AUDIT EXPORT (generated, not committed)                        │
│  ../doxedcryptofounder-audit/                                   │
│  Code-only snapshot for ChatGPT / external review                 │
│  See docs/AUDIT_FOR_CHATGPT.md                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  PRIVATE (never git, never ChatGPT)                             │
│  ../doxedcryptofounder-secrets/vault/                           │
│  .env.neon · .env.vercel.check · .env.x.secrets · paste files   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Public — inside this git repository

| Path | Purpose |
|------|---------|
| `apps/web/` | Next.js frontend (production: Vercel) |
| `apps/api/` | NestJS API (production: Railway) |
| `apps/founder-node/` | Electron desktop vault (releases on GitHub) |
| `packages/` | Shared utils, UI, types |
| `prisma/schema.prisma` | Database model (PostgreSQL) |
| `docs/` | Mission, privacy stack, deploy guides, **AUDIT_FOR_CHATGPT.md** |
| `services/btc-conservative-agent/` | Showcase bot source (no runtime `data/`) |
| `.github/founder-os/` | **Example** founder memory templates (not secrets) |
| `.github/workflows/` | CI (cron jobs with secrets run on deploy repo only) |

**Rule:** If it authenticates to production or holds a key, it does **not** belong here.

---

## Private — sibling secrets vault

**Path:** `../doxedcryptofounder-secrets/vault/`  
**Override:** `DCF_SECRETS_VAULT` environment variable

| File (examples) | Contents |
|-----------------|----------|
| `.env.neon` | `DATABASE_URL` |
| `.env.vercel.check` | `JWT_SECRET`, `NEXTAUTH_SECRET`, sync secrets |
| `.env.x.secrets` | `RAILWAY_TOKEN`, X OAuth tokens |
| `railway-*.env` | Paste buffers for Railway dashboard |

After clone:

```bash
npm run secrets:link    # symlink vault → local .env for dev
npm run secrets:strip   # remove accidental repo copies when vault is canonical
```

---

## Audit export — generated bundle

**Path:** `../doxedcryptofounder-audit/`  
**Override:** `DCF_AUDIT_EXPORT`  
**Command:** `npm run audit:export`

| Included | Excluded |
|----------|----------|
| `apps/`, `packages/`, `docs/` | `scripts/`, `docker/`, all `.env*` |
| `prisma/schema.prisma`, `seed.ts` | `node_modules`, build artifacts |
| `services/` source | Bot runtime `data/`, logs |
| Generated `AUDIT_SCOPE.txt` | Root README, lockfiles |

**Safe to zip and send to ChatGPT** together with `docs/AUDIT_FOR_CHATGPT.md`.

---

## GitHub remotes

| Name | Typical use |
|------|-------------|
| `danishhaiderau-maker/doxed-founders-website` | Deploy-connected repo (Vercel + Railway) |
| Local folder `doxedcryptofounder` | Developer monorepo name |

Production site: **https://doxxedcrypto.digital**

---

## Common mistakes (avoid mix-ups)

| Mistake | Fix |
|---------|-----|
| Committing `.env` | Add to vault; run `npm run secrets:strip` |
| Pasting Railway token into API env on dashboard | Use vault scripts only; never commit |
| Putting cron workflows in audit zip | Delete `*-daily.yml` from audit copy; run on deploy repo |
| Sharing vault folder with reviewers | Use `audit:export` only |
| Storing exchange keys in repo | Admin UI → encrypted in Neon via API |

---

## Quick commands

| Task | Command |
|------|---------|
| Link secrets for dev | `npm run secrets:link` |
| Export for ChatGPT audit | `npm run audit:export` |
| Full production sync | `npm run sync:all` |
| Read mission & product | [MISSION.md](./MISSION.md) |
