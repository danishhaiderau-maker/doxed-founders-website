# AGENTS.md

## Cursor Cloud specific instructions

### Core local stack

- **Must run for web+API E2E:** NestJS API (`npm run dev:api` → `:4000`) + Next.js web (`npm run dev:web` → `:3000`) + a Postgres-compatible `DATABASE_URL`.
- **Optional:** Redis (`REDIS_URL`), BTC bot (`services/btc-conservative-agent`, `:7002`), Founder Node Electron, Google OAuth, Stripe, Phala CVM.
- Standard commands: see root `README.md` (`npm run dev`, `db:push` / `db:seed`, `smoke:test`).

### Database gotchas (Cloud / Linux)

- Root `npm run setup` / `setup:neon` / `setup:local` are PowerShell-oriented. On Linux Cloud VMs, copy `.env.example` → `.env` and set `DATABASE_URL` yourself.
- On an **empty** database, prefer `npm run db:push` over `npm run db:deploy`. Checked-in migrations are incremental and assume a prior base schema (`ListingApplication` etc.); `migrate deploy` fails on a blank Neon/Postgres.
- Seed (`npm run db:seed`) only loads chains/categories (+ admin). If `DATABASE_URL` contains `neon`, you **must** set `SEED_ADMIN_PASSWORD` or seed exits after reference data.
- `npm run db:verify` expects legacy sample founders/projects (≥3/≥5); current seed does not create those — treat verify FAIL as expected unless you insert curated samples yourself.
- Redis is optional; API falls back to in-memory rate-limit/cache when `REDIS_URL` is unset.

### Dev servers

- Always `npm run build:utils` (or `npm run dev`, which does it) before API/web if `@dcf/utils` / `@dcf/founder-vault` dist is missing.
- Load `.env` into the shell before `dev:api` (`set -a && source .env && set +a`). Nest reads `DATABASE_URL` from the environment.
- Avoid `!` inside double-quoted bash exports (history expansion); use single quotes or no bangs in passwords.

### Lint / test notes

- `npm run lint --workspace=@dcf/api` fails today: `eslint` is not installed / not listed in API deps.
- `npm run lint --workspace=@dcf/web` runs interactive `next lint` setup (no committed ESLint config); not usable non-interactively until ESLint is configured.
- API unit tests: `npm run test --workspace=@dcf/api` (should pass without services).
- Web unit tests: `npm run test --workspace=@dcf/web`. One `api.session-summary` assertion expects a relative URL and fails when `NEXT_PUBLIC_API_URL` is an absolute `http://localhost:4000` — unset that env for that suite or ignore as env-coupled.
- `npm run smoke:test` needs API up. `projects` / `project-detail` fail until at least one approved curated project exists (seed alone is not enough).

### Do not blunt-sync the trading bot

See `.cursor/rules/no-blunt-bot-sync.mdc` and `config/bot-architecture.lock.json`. Edit `services/btc-conservative-agent/` in place; do not run `sync:btc-research-bot` without explicit force confirmation.
