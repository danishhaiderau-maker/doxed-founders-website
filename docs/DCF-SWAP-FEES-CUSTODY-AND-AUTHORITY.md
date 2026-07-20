# DCFSwap Fees, Custody, and Authority Rotation

| Field | Value |
|---|---|
| Status | Founder decision capture; two arithmetic/mechanics questions remain open |
| Updated | 2026-07-21 |
| Applies to | Bonding curve launches, graduated pools, founder payouts, platform treasury, buyback and burn |
| Supersedes | Conflicting post-graduation splits and permanent-wallet assumptions in older tokenomics documents |

This document records the economic intent without pretending that a website login is a blockchain security system. Contract code must not be written until the two items marked **Open** are confirmed and legal/security review is complete.

## 1. Decisions captured

### Bonding curve

- The total trading fee during the bonding curve is **1.00%**.
- **0.50% of trade volume** goes to the verified launching founder.
- The founder payout destination is founder-controlled and can be changed through a safe on-chain authority-transfer flow.
- The platform does not keep the founder's seed phrase or private key.

### After graduation

- The intended founder benefit is **0.25% of post-graduation trade volume**.
- The platform takes **0% of that founder benefit**.
- The founder can claim to a connected self-custody wallet or change the payout address through an authenticated, on-chain transfer.
- A founder may not change the recipient for fees already claimed, but can change the destination for future claims.

### Platform control

- An admin dashboard may prepare treasury conversions, platform-token buybacks, burns, payout-address changes, pauses, and emergency actions.
- A dashboard click creates a proposal. It must not directly use a server-held private key.
- High-risk proposals require an on-chain multisig threshold, transaction simulation, a delay where appropriate, and a permanent receipt.
- Deployer and developer keys are removed after deployment. A contractor or AI system must have no retained production authority.

## 2. Two points that must be confirmed

### Open A: the remaining bonding-curve half

Two different descriptions were given for a USD 1,000 fee collected on USD 100,000 of bonding-curve volume:

| Interpretation | Founder | Platform treasury in USDC | Buyback and burn |
|---|---:|---:|---:|
| A - first statement | USD 500 | USD 0 | USD 500 |
| B - later dashboard statement | USD 500 | USD 250 | USD 250 |

The safest working assumption is **B**, because it matches the requested admin flow: the platform-side USD 500 is split 50/50 between treasury USDC and buyback/burn. This is not locked until the founder confirms it explicitly.

### Open B: what the post-graduation 0.25% means

The full AMM trading fee cannot normally go to the founder while also paying liquidity providers. Raydium's standard CPMM fee is 0.25%, but most of it funds LPs; an optional creator fee can be added separately. Therefore choose one of these contract-readable models:

1. **Recommended v1:** a 0.25% founder creator fee on top of the base pool/LP fee. This is simple and honest, but the trader's total fee is higher than 0.25%.
2. Give the founder a transferable claim on some or all LP fees through a Fee Key or locked-liquidity fee share. The trader sees the base pool fee, but the exact founder yield depends on LP ownership.
3. Build a custom DCFSwap AMM that subsidizes LPs elsewhere. This is not recommended for v1 because it adds novel contract and liquidity risk.

User-facing copy must show the total trader fee and its exact recipients, not only the founder component.

## 3. Recommended custody model

### Founder revenue

- A connected wallet signs token creation and launch configuration.
- The launch stores a public `founderFeeReceiver` or transferable fee-claim authority.
- Fees accrue in a claim vault; they are not routed through the platform's operating wallet.
- Changing the receiver is a two-step on-chain action: current authority proposes, new authority accepts.
- The platform may offer a passkey smart account later, but v1 should default to a founder-owned wallet.

### Platform treasury and buybacks

- Use a Solana multisig vault for Solana assets and a Safe-style multisig for EVM assets.
- Start with separated roles: proposer, approvers, executor, emergency pauser, and guardian/recovery.
- Do not use `1-of-n`. Do not require every key either. A practical initial personal setup is `2-of-3`, with keys on separate devices and one recovery key stored offline; expand for a team.
- Apply destination allowlists, per-asset daily limits, buyback slippage limits, and transaction simulation.
- Use time locks for wallet/authority changes and large transfers. Emergency pause may be immediate but may not move funds.

### Website identity

- X login identifies the account; it does not prove ownership of a blockchain wallet.
- TOTP, passkeys, and a YubiKey protect the web account and may gate proposal creation.
- Wallet signatures and multisig approvals authorize the on-chain transaction.
- Treasury endpoints require recent step-up authentication, not only an old admin session.

## 4. Safe key rotation

"Connect a new wallet" is only the start of rotation. The current authority must execute an on-chain transfer before the old key loses control.

1. Add and verify the new signer on a separate device.
2. Create an authority-change proposal containing old address, new address, affected roles, and chain.
3. Simulate it and show the exact before/after authority graph.
4. Collect the multisig threshold and wait through the configured delay.
5. Execute on-chain and verify every affected contract/vault.
6. Revoke the old signer, API sessions, device sessions, delegates, and spending limits.
7. Publish an immutable rotation receipt and update the dashboard from chain state.
8. Test a low-value transaction before resuming normal limits.

If a single current key is lost before a recovery mechanism exists, rotation may be impossible. Recovery must be designed before launch, not after a compromise.

## 5. Admin treasury workflow

The requested admin experience should be one coherent flow:

1. **Accrual:** show claimable fees by launch, token, chain, age, and USD estimate.
2. **Select:** admin chooses assets and amount; the dashboard never exposes a raw private key.
3. **Policy:** apply the confirmed split, daily cap, minimum sweep size, approved routes, slippage, and MEV protection.
4. **Preview:** simulate expected USDC, expected platform tokens, price impact, route, gas, and minimum received.
5. **Propose:** create an immutable transaction bundle and proposal ID.
6. **Approve:** hardware-backed multisig members inspect the same payload and approve separately.
7. **Execute:** convert the treasury portion to USDC, execute a bounded platform-token buyback, then burn to the canonical burn mechanism.
8. **Prove:** store transaction hashes, amounts, price impact, approvals, and burn proof; make the economic receipt public.

Prefer daily or threshold-based batches over buying on every swap. This reduces gas and makes slippage, sandwich protection, and reporting controllable.

## 6. Market comparison as of 2026-07-21

| Venue | Current documented economics | DCF implication |
|---|---|---|
| Pump.fun | 1.25% bonding-curve total: 0.30% creator and 0.95% protocol; graduated canonical pools use a market-cap-dependent schedule | DCF's 1% total and 0.5% founder share are creator-friendlier |
| Raydium CPMM | Standard 0.25% trade fee largely funds LPs; optional creator fee is separate | Do not promise the whole 0.25% to founders unless LP economics are separately funded |
| Raydium LaunchLab | Platform configs can control creator fees and post-migration Fee Key distribution | Strong candidate for an audited Solana v1 under a DCFSwap-branded frontend |
| Moonit | Official pages describe a 1% trade fee but currently conflict on whether creator rewards are 80% or 90% | Contract-readable fee disclosure and one consistent UI are product requirements |
| Meteora | Audited DBC/DAMM primitives and configurable fee-sharing are available | Candidate for an audited graduation and fee-sharing path |

Primary sources:

- Pump.fun fees: https://pump.fun/docs/fees?stream=top
- Raydium LaunchLab platforms: https://docs.raydium.io/user-flows/launchlab-platforms
- Raydium creator fees: https://docs.raydium.io/user-flows/how-creator-fees-work
- Raydium CPMM fees: https://docs.raydium.io/products/cpmm/fees
- Moonit overview: https://docs.moon.it/docs/welcome
- Moonit terms: https://docs.moon.it/docs/terms-of-use
- Meteora audits: https://docs.meteora.ag/resources/audits/overview

## 7. Security implementation rules

- No seeds or private keys in the database, environment variables, Founder Vault, logs, support tools, analytics, or admin browser storage.
- Store public addresses, chain IDs, roles, policy versions, transaction payload hashes, and receipts.
- Read authority and balances from chain state; the database is an index, not the source of truth.
- Separate fee-receiver rotation, contract upgrade, pause, treasury transfer, and buyback permissions.
- Use two-step/delayed admin transfer on EVM and a multisig vault/PDA on Solana.
- Buyback code uses allowlisted tokens/routes, oracle sanity checks, minimum received, maximum price impact, expiry, and replay protection.
- Any upgradeability must be explicit, delayed, independently pausable, and visible to users.
- Every production contract requires independent audit, invariant/fuzz testing, testnet rehearsal, and a public incident plan.

Useful official security primitives:

- OpenZeppelin AccessManager and delayed admin rules: https://docs.openzeppelin.com/contracts/5.x/access-control
- Solana token authority changes: https://solana.com/docs/tokens/basics
- Squads thresholds, time locks, and vault guidance: https://docs.squads.so/main/navigating-your-squad/settings
- Squads security practices: https://docs.squads.so/main/additional-resources/advanced-security-best-practices

## 8. Legal and market-integrity gate

Buyback/burn promises, discretionary treasury sales, token launches, creator revenue, custody, and multi-chain settlement require specialist AU/US/EU advice before launch. The public policy must not promise price support. Use disclosed, bounded execution rules and immutable receipts. This document is product/security design, not legal or financial advice.

## 9. Build order

1. Confirm Open A and Open B in plain arithmetic.
2. Choose Solana v1 venue after a Raydium LaunchLab versus Meteora DBC proof of concept.
3. Define chain-readable fee and authority interfaces before dashboard work.
4. Build wallet linking, message-signature verification, and founder fee-recipient rotation on devnet.
5. Build the treasury proposal/simulation/approval/receipt API and admin dashboard against a devnet multisig.
6. Add buyback limits, burn proof, observability, and emergency pause drills.
7. Obtain counsel and external contract audit.
8. Run adversarial testnet launch, graduation, rotation, compromise, and recovery scenarios.
9. Only then replace the current fixed-price `DexStubService` and old 0.1% UI copy.

