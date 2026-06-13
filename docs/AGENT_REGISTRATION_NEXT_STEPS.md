# Agent Registration — Next Steps

Conservative BTC Agent discovery + admin fee wallet setup.

## What is wired in code

| Item | URL / path |
|------|------------|
| AgentCard (SAID metadata) | `https://doxxedcrypto.digital/.well-known/agent-card.json` |
| ERC-8004 JSON (The Spawn) | `https://doxxedcrypto.digital/.well-known/agent.json` |
| Admin registration UI | `/admin/agent-registrations` |
| Fee treasury | Admin → Platform (Solana pubkey = your Phantom) |
| Signal fee settlement | DDollar first; else USDC to treasury + `POST .../cycles/:id/settle` |

## Your steps (requires wallet signatures)

### Step 1 — Phantom as admin fee wallet (~2 min)

1. Log in as admin on [doxxedcrypto.digital](https://doxxedcrypto.digital).
2. **Account → Security** → Connect **Phantom**.
3. **Admin → Platform** (or **Admin → Agent registrations**) → paste the same Solana address → **Save fee wallets**.
4. Optional: set EVM address for Base/Spawn mint.

All signal success fees (10% of subscriber profit) route to this Solana treasury as USDC when DDollar balance is insufficient.

### Step 2 — SAID Protocol (Solana)

1. Ensure ~0.02 SOL in Phantom for register + verify.
2. Run locally (or use SAID wizard):

```bash
npx said-sdk register -k agent-wallet.json -n "Conservative BTC Agent" --uri "https://doxxedcrypto.digital/.well-known/agent-card.json"
```

3. Sign the transaction in Phantom.
4. Optional verified badge: `npx said-sdk verify` (~0.01 SOL).
5. In **Admin → Agent registrations**, click **Mark registered** for SAID.

### Step 3 — The Spawn / ERC-8004 (Base)

1. Request API key at [thespawn.io](https://thespawn.io) if needed.
2. Run prep script:

```bash
npm run build:utils
THESPAWN_API_KEY=... node scripts/prepare-agent-registrations.mjs
```

3. Sign the returned `register(string)` transaction on **Base** with your EVM wallet (~small Base ETH for gas).
4. After indexer catches up: `npx spawnr@latest check base:<agent_id>`.
5. Mark **SPAWN** registered in admin UI.

### Step 4 — Auto-indexed directories

After Base mint confirms, agents appear on:

- [8004scan.io](https://8004scan.io)
- [agentscan.info](https://agentscan.info)
- [thespawn.io/agents/base/…](https://thespawn.io)

No extra submission required.

### Step 5 — Deploy + sync

After code changes:

```bash
npm run build:utils
npm run sync:all
```

Verify live metadata:

```powershell
Invoke-RestMethod "https://doxxedcrypto.digital/.well-known/agent.json"
Invoke-RestMethod "https://doxxedcrypto.digital/.well-known/agent-card.json"
```

## What we do next (after you sign)

| Priority | Task |
|----------|------|
| P0 | You connect Phantom + save treasury; sign SAID + Spawn txs |
| P0 | Deploy schema (`AgentRegistryEntry`, signal fee Solana fields) to Neon |
| P1 | Wait for live APPROVE → confirm `SignalCycle` + `/signals/latest` |
| P1 | Run `spawnr check` until quality tier passes |
| P2 | WebSocket `EXIT_URGENT` for thesis cuts |
| P2 | x402 on signal endpoints for autonomous agents |
| P3 | Hyperliquid reference subscriber script |

## Fee flow (subscriber)

1. Subscriber reports `EXIT` with `pnl_usd`.
2. Platform computes success fee (10% profit, $0 on loss, waive if &lt; $0.20).
3. If subscriber has DDollar → instant debit; admin credited in ledger.
4. Else → USDC payment instructions to **your Phantom treasury**; subscriber confirms with `tx_signature` on `/settle`.

Your admin account controls the treasury address; we never hold your seed phrase.
