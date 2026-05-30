# DoxedCryptoFounder

Curated crypto intelligence platform for serious blockchain businesses with public founders, documentation, and transparent teams.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | Next.js 15, React, TypeScript, Tailwind CSS, shadcn/ui |
| Backend | NestJS, Node.js, TypeScript |
| Database | PostgreSQL + Prisma |
| Cache | Redis |
| Auth | NextAuth (Phase 3) |
| Monorepo | Turborepo + npm workspaces |

## Project Structure

```text
doxedcryptofounder/
  apps/
    web/          # Next.js frontend
    api/          # NestJS backend
    founder-node/ # Electron desktop vault (see apps/founder-node/README.md)
  packages/
    ui/           # Shared UI components
    types/        # Shared TypeScript types
    config/       # Shared configuration
    utils/        # Shared utilities
  prisma/         # Database schema & migrations
  docker/         # Docker configs
  scripts/        # Utility scripts
```

## Quick Start

### 1. Install dependencies

```bash
npm install
```

### 2. Configure environment

```bash
cp .env.example .env
```

### 3. Start infrastructure

**Option A — Docker (recommended long-term):**

```powershell
npm run setup
```

**Option B — No Docker (fastest if Docker won't start):**

```powershell
npm run setup:neon
```

Uses free [Neon](https://neon.tech) cloud PostgreSQL. No WSL/Docker required.

**If Docker fails to start:**

```powershell
npm run setup:repair   # runs wsl --update
# RESTART PC, open Docker Desktop, then npm run setup
```

> **Docker Desktop must show "Running"** before `docker compose up` works.
> If you see `Docker Desktop is unable to start`, run `npm run setup:repair`,
> restart your PC, or use `npm run setup:neon` instead.

### 4. Run database migrations

```bash
npm run db:migrate
```

### 5. Seed initial data

```bash
npm run db:seed
```

### 6. Start development servers

```bash
npm run dev
```

- **Frontend:** http://localhost:3000
- **Backend API:** http://localhost:4000/api/health

## Production deploy (Neon + Railway + Vercel)

1. **Database:** `npm run setup:neon` → copy connection string to Railway/Vercel env as `DATABASE_URL`.
2. **API (Railway):** connect repo, set env from `.env.production.example`, first deploy set `PRISMA_DB_PUSH=true`, then run `npm run db:seed` against Neon once.
3. **Web (Vercel):** import repo, set **Root Directory** to `apps/web`, add `NEXT_PUBLIC_API_URL`, `NEXTAUTH_URL`, `NEXTAUTH_SECRET`, Google/Stripe keys.
4. **Stripe webhook:** point to `https://YOUR-API/api/paper-trading/stripe/webhook`.
5. **Google OAuth redirect:** `https://YOUR-DOMAIN/api/auth/callback/google`.

Railway uses `railway.toml`. Vercel uses `apps/web/vercel.json`.

## Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Start all apps in dev mode |
| `npm run dev:web` | Start frontend only |
| `npm run dev:api` | Start backend only |
| `npm run build` | Build all apps |
| `npm run build:api` | Build utils + Prisma client + API |
| `npm run verify:prod` | Build API + web and verify deploy files |
| `npm run smoke:test` | Hit public API endpoints (API must be running) |
| `npm run start:api:prod` | Migrate/push DB + start API (production) |
| `npm run db:deploy` | Run Prisma migrate deploy (PostgreSQL) |
| `npm run db:migrate` | Run Prisma migrations |
| `npm run db:seed` | Seed chains, categories, founders & projects |
| `npm run db:verify` | Verify Phase 2 seed data |
| `npm run dev:founder-node` | Start Founder Node desktop app (dev) |
| `npm run pack:founder-node` | Build Founder Node installer (.exe / .dmg) |

## Supported Chains

Ethereum, Solana, Polygon, Arbitrum, Optimism, Base, Avalanche, BNB Chain

## Development Phases

- [x] **Phase 1** — Monorepo foundation
- [x] **Phase 2** — Database seed + sample projects
- [x] **Phase 3** — Auth + users (email/password + Google OAuth wiring)
- [x] **Phase 4** — Projects + founders APIs
- [x] **Phase 5** — Admin listing review + publish pipeline
- [x] **Phase 6** — Market data (DexScreener preview + live metrics sync)
- [x] **Phase 7** — Public frontend pages
- [x] **Phase 9** — Paper trading + feed + portfolio sharing + Stripe reset
- [x] **Phase 10** — Trending feed spotlight + leaderboard
- [x] **Phase 11** — Listing applications → live projects
- [x] **Phase 8** — Watchlist + basic analytics events
- [x] **Phase 12** — Production deploy live on [Neon](https://neon.tech) + Railway API + [Vercel](https://doxxedcrypto.digital) (see Production deploy above)
- [x] **Founder Node Phase 2** — One-click `.exe` / `.dmg` installers ([setup guide](apps/founder-node/README.md))

## Founder Node

Desktop self-custody vault for project memory. **Installers:** [GitHub Releases](https://github.com/danishhaiderau-maker/doxed-founders-website/releases/latest) · **Setup:** [doxxedcrypto.digital/founder-node](https://doxxedcrypto.digital/founder-node) · **Docs:** [apps/founder-node/README.md](apps/founder-node/README.md)

## License

Private — All rights reserved.
