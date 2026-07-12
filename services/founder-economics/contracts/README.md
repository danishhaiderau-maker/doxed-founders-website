# Founder Economics — Smart Contracts (Design Artifacts)

These `.sol` files are **design artifacts** for the Phase 8 Founder Economics
MVP. They do not need to compile without a Solidity toolchain installed.

## Architecture — on-chain simple, off-chain swappable

```
ON-CHAIN (non-upgradeable, immutable, auditable)
  PlatformToken.sol    → ERC-20 + ERC-2612 permit, fixed supply minted once
  VestingVault.sol     → releases 20M tokens/epoch on 90-day schedule
  EpochDistributor.sol → stores Merkle roots per epoch, verifies proofs, pays

OFF-CHAIN (swappable, upgradeable without touching contracts)
  DistributionModel    → computes who gets what each epoch (TypeScript)
  DDollar Engine       → tracks activity, grants DDollar (NestJS)
  Knowledge Graph      → tracks contribution vs impact, lineage (NestJS)
  Proof of Success     → verifies real milestones (Stripe, GitHub, Vercel)
  Settlement Job       → runs each epoch, computes Merkle root, publishes root
```

## Why this shape

- **VestingVault.releaseEpoch() is permissionless** — anyone can call it when
  the epoch ends. No admin key, no pause, no upgrade. The schedule is law.
- **EpochDistributor is blind** — it has no idea how the Merkle root was
  computed. It only verifies proofs and pays. This means swapping the
  distribution model (v1 pro-rata → v2 reputation-weighted → ...) requires
  zero contract changes — only the off-chain settlement job changes which
  model it runs.
- **Claim windows are 365 days** — after that, unclaimed tokens stay in the
  distributor treasury for future epochs (no admin clawback because there is
  no admin).

## Deployment

**MVP:** contracts stay as design artifacts. Off-chain settlement + claim UI
ship without a live deploy. See
[docs/FOUNDER-ECONOMICS-MVP-VS-PRODUCTION.md](../../../docs/FOUNDER-ECONOMICS-MVP-VS-PRODUCTION.md).

**Keeper stub:** `npm run keeper:founder-economics` exits non-zero until
production env + counsel deploy are configured — it will never fake a tx.

When we wire deployment (post-MVP / counsel-gated):

1. Install Foundry or Hardhat + `@openzeppelin/contracts@^5`.
2. Deploy `PlatformToken(name, symbol, totalSupply, vestingVaultAddress)`.
3. Deploy `VestingVault(token, distributor, releasePerEpoch, epochSeconds)`.
4. Deploy `EpochDistributor(token)`.
5. The off-chain settlement job calls `publishRoot(epoch, root, total)` each
   epoch after computing the Merkle tree from the active DistributionModel.
6. Founders call `claim(epoch, account, amount, proof)` with the proof the
   settlement job published to IPFS.

## Constants (production)

| Constant | Value |
|---|---|
| `releasePerEpoch` | 20,000,000 × 10^18 |
| `epochSeconds` | 7,776,000 (90 days) |
| `CLAIM_WINDOW_SECONDS` | 31,536,000 (365 days) |
| `totalSupply` | depends on vesting length (e.g. 80 epochs × 20M = 1.6B) |
