# Founder Economics contracts — testnet-only

These Solidity contracts implement the immutable **1,000,000,000-token**
Founder Economics fixture. They are complete enough for local contract tests,
but are **not deployed, funded, audited, or approved for production**.

Permitted network: **Robinhood EVM testnet only** (chain ID `46630`). Mainnet,
DEX launch, treasury funding, and the initial liquidity action remain outside
this repository's implementation scope until every production gate passes.

## Locked allocation

| Allocation | Tokens | Share |
| --- | ---: | ---: |
| Initial DCF liquidity pool | 200,000,000 | 20.00% |
| 40 decaying community epochs | 509,414,048 | 50.94% |
| Champions terminal reward | 160,000,000 | 16.00% |
| Team / treasury reserve | 130,585,952 | 13.06% |
| **Total** | **1,000,000,000** | **100.00%** |

The initial 200M allocation is funded outside `VestingVault`. The vault holds
the other 800M: 40 quarterly community/team releases, followed by the terminal
Champions release on day 3,650. Community releases apply 2.5% to the remaining
virtual emission reference for epochs 1–39; epoch 40 normalizes rounding so the
locked 509,414,048 allocation is exact.

## Contract responsibilities

```text
PlatformToken       fixed 1B ERC-20 supply
VestingVault        permissionless 90-day release schedule
EpochDistributor    funded root proposal → 7-day challenge → final claim
ModelRegistry       governance-approved source/build hash and activation epoch
```

- `VestingVault` cannot change the allocation, cadence, terminal date, or team
  recipient after deployment.
- `EpochDistributor` only accepts a root for an already funded epoch from the
  approved keeper. A root is claimable only after its seven-day challenge
  window; governance may veto during that window.
- Leaves are `keccak256(abi.encode(address,uint256))` and use exact `uint256`
  token units. A wallet may occur only once in an epoch.
- Expired unclaimed balances return to the vault for the terminal reserve.
- A model can be used only when its code hash has been governance-approved for
  the relevant activation epoch. Environment variables are not an authority
  path for model selection.

## Local verification

From the repository root:

```text
npx tsx --test packages/utils/src/founder-economics/merkle-tree.test.ts
npm test --prefix services/founder-economics
```

The tests cover exact allocation and rounding, Solidity-compatible Merkle
proofs, duplicate-wallet rejection, funded epochs, model approval, challenge
and veto behaviour, claims, expiry returns, and the day-3,650 terminal gate.

## Required gates before any deployment

1. Apply migrations to an isolated Neon staging database and verify recovery.
2. Deploy and fund only on Robinhood EVM testnet (`46630`), using dedicated
   protected operations keys.
3. Run the accelerated five-minute, 40-epoch end-to-end testnet simulation and
   reconcile every contract event.
4. Complete independent Solidity/security review and legal/counsel approval.
5. Obtain explicit approval before any production, liquidity, or mainnet step.
