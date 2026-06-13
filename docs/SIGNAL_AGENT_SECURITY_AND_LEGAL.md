# Conservative BTC Agent — Security, Legal & Visibility

## Admin ownership (non-negotiable)

| Control | Who | Where |
|---------|-----|--------|
| Showcase bot (pause/start, keys, Railway push) | **Admin only** | `/admin/control` |
| Platform fee treasury (Phantom pubkey) | **Admin only** | `/admin/platform`, `/admin/agent-registrations` |
| SAID / Spawn registration & verify | **Admin only** | `/admin/agent-registrations` |
| Signal publisher (live APPROVE → cycles) | **Admin bot** | Railway `btc-conservative-agent` |
| Hire isolated instance (7 days) | **Any signed-in user** | 2,000 DDollar universal fee → admin |

Users **observe** the showcase or **hire** their own instance. They **never** control the admin showcase bot, credentials, or fee treasury.

## What is public vs protected

### Public (safe for directories)

- `/.well-known/agent-card.json` — signal API URLs only, **no** Railway bot URL
- `/.well-known/agent.json` — ERC-8004 metadata, **no** strategy internals
- `GET /signals/mandate`, preview `GET /signals/latest` (no API key = preview only)
- `GET /trading-agents/conservative-btc/dashboard` — **sanitized** (`publicSafe: true`)

### Never exposed in agent metadata or public API

- Raw research bot `/api/state` (full pipeline, AI prompts, feature snapshots)
- Admin research dashboard JSON
- Exchange/API keys (Neon encrypted; Admin Control → Railway only)
- GitHub repo secrets (`.env`, vault paths in `.gitignore`)

### Subscriber API (API key required)

- Full ENSE intent payload
- Lifecycle events + settlement
- Success fee on profitable `EXIT` only

## Trade cycle & billing enforcement

1. Bot APPROVE → platform creates `SignalCycle` (INTENT).
2. Subscriber POST `ORDER_PLACED` → `FILLED` (**requires** `stop_loss_placed: true`).
3. Subscriber POST `EXIT` with `pnl_usd`.
4. Platform computes fee: 10% profit, $0 on loss, waive if 10% < $0.20.
5. Settlement: DDollar debit first; else USDC to **admin Solana treasury** + `POST .../settle` with tx proof.

Mandate + docs include **legal disclaimer** (`SIGNAL_LEGAL_DISCLAIMER` in `@dcf/utils`).

## Legal disclaimer (summary)

Signals are **informational only**, not investment advice. Subscribers execute on their own accounts and accept all risk. Fees are contractual success fees on reported profitable closes, not performance guarantees.

Full text: exported as `SIGNAL_LEGAL_DISCLAIMER` and returned in `GET .../signals/mandate`.

## Directory visibility plan

| Site | Fit | Action |
|------|-----|--------|
| SAID Protocol | ✅ Agent identity | Admin signs Solana tx — `/admin/agent-registrations` |
| The Spawn / ERC-8004 | ✅ Agent identity | Admin signs Base tx |
| aiagentsdirectory.com | ✅ Agent listing | Manual submit AgentCard + hub URL |
| openserv.ai | ⚠️ Review | Apply with API docs if accepted |
| 8004scan / AgentScan | ✅ Auto | After Spawn mint |
| DexScreener | ❌ Token chart | Only if you launch a token (optional promo) |
| Linktree | ⚠️ Promo | Optional link-in-bio to hub |
| clawpump.tech / litcoin.app | ❌ Token launch | Not applicable unless token product |

## x402 roadmap (Phase 2)

Current: `x402Support: false` — settlement is **post-trade success fee** (not pay-before x402).

Phase 2:

- x402 on `GET /signals/latest` for autonomous agents (micropayment per poll)
- SAID client cross-chain messaging with x402 tier
- Keep success fee on `EXIT` for profit share

## GitHub hygiene

- Never commit `.env`, vault files, or research bot embedded keys
- `sync-btc-research-bot` strips keys; credentials flow Admin Control → Neon → Railway
- Bot Railway URL is env-only, not in public agent-card
