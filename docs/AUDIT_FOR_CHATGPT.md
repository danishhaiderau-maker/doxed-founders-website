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
**Command center proof pack (production verification):** [FOUNDER_OS_COMMAND_CENTER_PROOF.md](./FOUNDER_OS_COMMAND_CENTER_PROOF.md)

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

## Download bundle (scrubbed export)

**Preferred for ChatGPT:** attach the **scrubbed** zip from `npm run audit:export` (excludes `.env*`, `scripts/`, ops paths). Do **not** use the full GitHub repo zip unless you accept a larger tree and more surface area.

| Item | Value |
|------|-------|
| **Local zip (this machine)** | `c:\Users\user\Desktop\Final Bots\doxedcryptofounder\docs\exports\doxedcryptofounder-audit-2026-06-05.zip` |
| **Size** | ~19.7 MB (20,654,231 bytes) |
| **Generated from** | `../doxedcryptofounder-audit/` (sibling folder; includes `AUDIT_SCOPE.txt`) |
| **GitHub direct zip** | Not committed (over ~10 MB); use local path or regenerate |

**Regenerate:**

```bash
npm run audit:export
```

Then zip `../doxedcryptofounder-audit/` or save as `docs/exports/doxedcryptofounder-audit-YYYY-MM-DD.zip` (gitignored).

**Attach to ChatGPT:**

1. Upload the local zip above (or a freshly built zip).
2. Paste the [suggested ChatGPT prompt](#suggested-chatgpt-paste-as-is) from the Command Center section (or the review checklist in this file).
3. Optionally add doc links: [proof pack](https://github.com/danishhaiderau-maker/doxed-founders-website/blob/master/docs/FOUNDER_OS_COMMAND_CENTER_PROOF.md), [this guide](https://github.com/danishhaiderau-maker/doxed-founders-website/blob/master/docs/AUDIT_FOR_CHATGPT.md).

**Fallback (full repo, not scrubbed):** https://github.com/danishhaiderau-maker/doxed-founders-website/archive/refs/heads/master.zip — git-tracked files only; still **no** vault secrets, but **not** the same scope as `AUDIT_SCOPE.txt`.


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

## Command Center audit (2026-06)

Use this section when asking ChatGPT (or another reviewer) to audit the **Founder OS Command Center** work — Sprints A–F and follow-up proof/sync docs on `master`.

### Shareable links (GitHub)

| What | URL |
|------|-----|
| **Repository** | https://github.com/danishhaiderau-maker/doxed-founders-website |
| **Latest `master`** | https://github.com/danishhaiderau-maker/doxed-founders-website/commit/f9229fd |
| **Sprint A–F implementation** | https://github.com/danishhaiderau-maker/doxed-founders-website/commit/1e6f1ce |
| **Sprint + follow-up range** | https://github.com/danishhaiderau-maker/doxed-founders-website/compare/1e6f1ce^...f9229fd |
| **Sync audit record** | https://github.com/danishhaiderau-maker/doxed-founders-website/commit/d52bd89 |
| **This audit guide** | https://github.com/danishhaiderau-maker/doxed-founders-website/blob/master/docs/AUDIT_FOR_CHATGPT.md |
| **Proof pack** | https://github.com/danishhaiderau-maker/doxed-founders-website/blob/master/docs/FOUNDER_OS_COMMAND_CENTER_PROOF.md |
| **Architecture** | https://github.com/danishhaiderau-maker/doxed-founders-website/blob/master/docs/FOUNDER_OS_COMMAND_CENTER_ARCHITECTURE.md |
| **Sprint A–F status** | https://github.com/danishhaiderau-maker/doxed-founders-website/blob/master/docs/SPRINT_COMMAND_CENTER_A-F_STATUS.md |
| **North star** | https://github.com/danishhaiderau-maker/doxed-founders-website/blob/master/docs/FOUNDER_OS_NORTH_STAR.md |

**Full code audit (recommended):** run `npm run audit:export` from repo root, zip `../doxedcryptofounder-audit/`, and attach `AUDIT_SCOPE.txt` plus the proof pack link above. The export excludes `.env*`, `scripts/`, and ops-only paths by design.

### What changed in Sprints A–F

Commit `1e6f1ce` transformed Founder OS from a status dashboard into a **founder operating system** in one pass. **Sprint A** removed Ask/Build and provider pickers so a single Founder Brain auto-routes intent (`founder-brain-router.ts`, hero chat). **Sprint B** added a computed CEO inbox with buckets — Needs Attention, Review, Approval, Publishing, Deployment, Decision — via `GET /copilot/founder-queue`. **Sprint C** made Agent Runtime own builds: `agentRuns.start` on Cursor dispatch, in-chat step streaming, and `AGENT_REVIEW` queue items with `sourceRunId`. **Sprint D** shipped Agent Bus v1 (research→build, build→content handoffs with dedupe) plus control actions (merge PR, publish, sync) from Mission Control. **Sprint E** added decision memory — journal entries auto-detected in chat and injected into Brain context. **Sprint F** added Desktop Bridge metadata (branch, open file names, task label) from Founder Node heartbeat without syncing file contents. Production verification and the three acceptance tests (dashboard / command center / OS) are documented in the proof pack.

### Suggested ChatGPT prompt (paste as-is)

```text
You are auditing the Founder OS Command Center for DoxxedCrypto.digital — a NestJS + Next.js monorepo. No secrets are included; do not ask for .env values.

Start with these docs (read in order):
1. https://github.com/danishhaiderau-maker/doxed-founders-website/blob/master/docs/FOUNDER_OS_COMMAND_CENTER_PROOF.md
2. https://github.com/danishhaiderau-maker/doxed-founders-website/blob/master/docs/FOUNDER_OS_COMMAND_CENTER_ARCHITECTURE.md
3. https://github.com/danishhaiderau-maker/doxed-founders-website/blob/master/docs/SPRINT_COMMAND_CENTER_A-F_STATUS.md
4. https://github.com/danishhaiderau-maker/doxed-founders-website/blob/master/docs/AUDIT_FOR_CHATGPT.md

Primary code change: commit 1e6f1ce (Sprints A–F). Compare range for follow-ups: 1e6f1ce^...f9229fd.

Focus areas:
- Intent routing and single-Brain UX (packages/utils/src/founder-brain-router.ts, apps/web founder-copilot-chat)
- CEO inbox derivation and bucket semantics (packages/utils/src/founder-queue.ts)
- Agent Runtime + builder step streaming (apps/web/src/lib/builder-run-live.ts, founder-copilot.service.ts)
- Agent Bus handoffs and dedupe (packages/utils/src/agent-bus.ts, founder-command-center.service.ts)
- Decision journal privacy and context injection (packages/utils/src/founder-decision-log.ts)
- Desktop Bridge — confirm only metadata, no file contents (apps/founder-node, events.controller heartbeat)
- Auth on /copilot/* routes; webhook and control-action guards

Run the three acceptance tests from the proof pack conceptually: (1) dashboard fail — task title only, (2) command center pass — initiative + outcomes + blocker + queue, (3) OS pass — research→build→content without leaving the tab.

Structure findings as: Severity | Location (file + route/function) | Issue | Recommendation | False positive note.

If I attach a zip from npm run audit:export, scope your review to AUDIT_SCOPE.txt and the paths listed in AUDIT_FOR_CHATGPT.md.
```

### Command-center code map (quick reference)

| Sprint | Key paths |
|--------|-----------|
| A — Brain | `packages/utils/src/founder-brain-router.ts`, `apps/web/src/components/founder-copilot-chat.tsx` |
| B — CEO inbox | `packages/utils/src/founder-queue.ts`, `apps/web/src/components/founder-command-center-panels.tsx` |
| C — Runtime | `apps/api/src/events/founder-copilot.service.ts`, `apps/web/src/lib/builder-run-live.ts` |
| D — Agent Bus | `packages/utils/src/agent-bus.ts`, `apps/api/src/events/founder-command-center.service.ts` |
| E — Decisions | `packages/utils/src/founder-decision-log.ts` |
| F — Desktop Bridge | `apps/founder-node/src/sync-client.ts`, `apps/api/src/events/events.controller.ts` |

---

## Document history

| Date | Change |
|------|--------|
| 2026-06 | Initial auditable guide; aligns with paper session tokens, bot control secret, sync-metrics guard |
| 2026-06 | Download bundle section — local zip path, ChatGPT attach steps
| 2026-06 | Command Center audit section — Sprints A–F links, prompt, code map |

For human-maintained security notes, see [FOUNDER_OS_AUDIT.md](./FOUNDER_OS_AUDIT.md).
