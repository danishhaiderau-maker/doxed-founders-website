# Phases 1–9 audit — 2026-07-13

## Decision

**Do not launch a token, fund a liquidity pool, or deploy Founder Economics to
mainnet.** The source is restored and the local build/test gates below pass, but
the production gates in this document remain open.

## Evidence collected

- Authoritative checkout restored at commit `ed7d019c9d9729bd9cf9f764f48276b58e64a82d` on branch `codex/phase-1-9-hardening`.
- `npm.cmd run db:generate` passed.
- `npm.cmd run build:utils` passed.
- `npm.cmd run build --workspace=@dcf/api` passed.
- `npm.cmd run build --workspace=@dcf/web` passed.
- `tsx --test packages/utils/src/founder-economics/merkle-tree.test.ts` passed: 3 tests.
- `npm.cmd test --prefix services/founder-economics` passed: 4 contract tests.
- Vercel CLI can read the existing `doxed-founders-website` production project.
- Railway CLI authentication failed with an invalid or unauthorized token; no Railway action was attempted.
- Only checked-in `.env*.example` files were present. No actual environment file was read or printed.

## Phase status

| Phase | Status | Audit result and remaining gate |
| --- | --- | --- |
| 1. Kernel / Memory | Partial | Checkout restored; Learning Engine watermark and a short replica lease are Prisma-backed. Apply migrations and run a real multi-instance recovery test. |
| 2. Founder OS | Implemented, deployment-gated | Founder OS no longer advertises a Solana launch/DEX or a fake Phase-7 waitlist. The dashboard now returns the real founder tier/DDollar balance and reports actual build sessions, update work, bounties, and verified integrations. Staging browser smoke tests still need a live account and connected infrastructure. |
| 3. Founder IDE | Blocked | The misleading shell/filesystem fallback was removed: the IDE target now reports offline until verified IPC exists. A real Void fork, signed installer, updater, rollback path and live IPC protocol are not present in this repository. |
| 4. Learning Engine | Implemented, deployment-gated | Durable `LearningEngineState`, a two-hour replica lease, migration, scheduled rollup, and recovery tests are in place. Migration application and operational outcome tests remain required. |
| 5. Routing | Partial | Capability and Flight Recorder code exists, but a distributed-cache/fallback/outcome test has not been demonstrated against staging. |
| 6. Idea Validator | Implemented, deployment-gated | Queue attempts, stale-work recovery, retry backoff and a one-minute durable worker were added. Apply the migration and test a restart against a real database. |
| 6.5 Debug Squasher | Partial | Existing consent and scheduled surfaces remain; this audit did not demonstrate real repository checks or a production consent/cron run. |
| 7. Deployment modes | Blocked for promotion | Vercel read access is confirmed, but Railway authentication is invalid and no isolated Neon/staging validation has run. No Vercel or Railway deployment was made. |
| 8. Founder Economics | Testnet hardening implemented; production blocked | Exact-unit Merkle allocation, one verified Robinhood EVM testnet wallet, immutable snapshots, IPFS pin requirement, receipt/state verification, governance model registry, seven-day challenge/veto, 365-day expiry return, reconciliation and the locked fixture are implemented and locally tested. Contract deployment, five-minute testnet harness, independent security review and counsel gate remain mandatory. |
| 9. LAM | Implemented, deployment-gated | The queue now persists leases/retries, reclaims stale workers, resumes the original plan without replanning, and pauses browser-write/desktop-control steps for per-step founder confirmation. A real Computer Use provider and restart test against staging remain required. |

## Locked Founder Economics fixture

| Allocation | Tokens | Share |
| --- | ---: | ---: |
| Initial DCF liquidity pool | 200,000,000 | 20.00% |
| 40 decaying epoch distributions | 509,414,048 | 50.94% |
| Champions terminal reward | 160,000,000 | 16.00% |
| Remaining team/treasury reserve | 130,585,952 | 13.06% |
| Total | 1,000,000,000 | 100.00% |

- The vault controls 800M tokens: community distributions, quarterly team/treasury releases, then the terminal Champions reserve.
- Community releases use 2.5% of the remaining 800M virtual emission reference for epochs 1–39; epoch 40 normalizes the integer remainder so the locked 509,414,048 allocation is exact.
- Team/treasury releases every 90 days for 40 epochs. The terminal release is unavailable until day 3,650, not merely after 40 × 90 days.
- A model/rule change must be approved by governance using a source/build hash and activation epoch. The API can only mirror a hash already active in `ModelRegistry`; it cannot switch a model through an environment variable.
- Governance may veto a proposed root during its seven-day challenge. Finalized roots and paid claims are immutable. Unclaimed finalized balances return to the terminal vault after 365 days.

## Required production gates

1. Rotate the previously exposed Neon credential outside source control and update the deployment secret only. Confirm replacement presence/connectivity without printing it.
2. Apply and verify all Prisma migrations on an isolated Neon branch/staging database.
3. Deploy contracts **only** to Robinhood EVM testnet (chain ID 46630); wire deployment Safe → 200M test liquidity allocation and 800M vault, then record addresses in protected testnet configuration.
4. Run a five-minute testnet epoch simulation through all 40 releases, root proposal/finalization, claims, expiry return and the terminal reward. Reconcile actual chain logs.
5. Obtain an independent Solidity/security review and counsel approval.
6. Restore a valid Railway deployment credential, validate GitHub → Neon staging → Vercel/Railway smoke tests, then request/perform an explicitly approved promotion.
7. Build the actual Void-based Founder IDE plus installer/updater/rollback and verified IPC. Do not label it connected before that bridge is live.
