# Founder Economics — MVP vs Production

**Status:** Honest split so we never claim on-chain is live when it is not.

| Layer | MVP (shipped / shipping) | Production (blocked) |
|-------|--------------------------|----------------------|
| DDollar scoring + ledger | NestJS `DdollarEngineService` | Same, plus anti-sybil hardening |
| Knowledge graph | Off-chain Prisma + API | Same |
| Epoch settlement | Off-chain Merkle root publish | Calls `EpochDistributor.publishRoot` |
| Claim UI | Sign-in + **Connect wallet** CTA + Merkle proof JSON | Wallet signs `claim(...)` on-chain |
| Contracts | Design artifacts under `services/founder-economics/contracts/` | Deployed + audited + funded |
| Keeper / cron | `scripts/founder-economics-keeper.mjs` **fails loudly** until env is set | Runs every epoch, publishes root |

## What founders see today

1. `/founder-economics` dashboard (GDP, DDollar, knowledge, proofs).
2. Claim panel always shows **Connect wallet** — even with zero claimable epochs — so the production CTA is not hidden behind a stub.
3. “Get Merkle proof (MVP)” returns API proof JSON only. It does **not** broadcast a transaction.

## What is deliberately NOT done

- No fake mainnet/testnet deploy of `PlatformToken` / `VestingVault` / `EpochDistributor`.
- No treasury keys in this repo.
- No claim that vesting is live on-chain.

## Next steps (production)

1. Counsel sign-off for token classification / distribution jurisdiction.
2. Install Foundry/Hardhat + OpenZeppelin; compile contracts in `services/founder-economics/contracts/`.
3. Deploy with a dedicated ops key (never commit private keys).
4. Set env for the keeper (see script header) and run `node scripts/founder-economics-keeper.mjs`.
5. Wire wallet connect (WalletConnect / Phantom / etc.) to submit `claim` with the MVP proof fields.

See also: `services/founder-economics/contracts/README.md`.
