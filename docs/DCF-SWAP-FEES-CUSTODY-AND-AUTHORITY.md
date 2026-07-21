# DCFSwap Fees, Custody, and Authority Rotation

| Field | Value |
|---|---|
| Status | Final recommended economics; legal, security, implementation, and audit gates remain |
| Updated | 2026-07-21 |
| Applies to | Bonding curve launches, graduated pools, founder payouts, platform treasury, buyback and burn |
| Supersedes | Conflicting post-graduation splits and permanent-wallet assumptions in older tokenomics documents |

This document records the final recommended economics without pretending that a website login is a blockchain security system. Contract code must not ship until legal/security review and an independent contract audit are complete.

## 1. Decisions captured

### Bonding curve

- The total trading fee during the bonding curve is **1.00%**.
- **0.50% of trade volume** goes to the verified launching founder in the quote asset.
- **0.25% of trade volume** goes to the platform operating reserve in USDC or is converted from the native quote asset to USDC in bounded batches.
- **0.25% of trade volume** funds platform-token buyback and burn using the quote asset.
- The founder payout destination is founder-controlled and can be changed through a safe on-chain authority-transfer flow.
- The platform does not keep the founder's seed phrase or private key.
- The platform and founder fee vaults do not receive the newly launched project token as a fee.

### After graduation

- The intended founder benefit is **0.25% of post-graduation trade volume**.
- The platform takes **0% of that founder benefit**.
- The recommended implementation is a separate **0.25% creator fee** on top of the underlying pool's disclosed LP/venue fee. With a 0.25% base pool fee, the trader sees a transparent 0.50% total.
- The founder can claim to a connected self-custody wallet or change the payout address through an authenticated, on-chain transfer.
- A founder may not change the recipient for fees already claimed, but can change the destination for future claims.

### Platform control

- An admin dashboard may prepare treasury conversions, platform-token buybacks, burns, payout-address changes, pauses, and emergency actions.
- A dashboard click creates a proposal. It must not directly use a server-held private key.
- High-risk proposals require an on-chain multisig threshold, transaction simulation, a delay where appropriate, and a permanent receipt.
- Deployer and developer keys are removed after deployment. A contractor or AI system must have no retained production authority.

## 2. Final recommended launch economics

### Supply and liquidity

- Fixed supply: **1,000,000,000 project tokens**.
- **80%** is available through the bonding curve.
- The remaining **20%** becomes initial post-graduation liquidity with the accumulated curve quote reserve.
- **0% unlocked founder allocation** and **0% platform allocation** at genesis. The founder's durable incentive is fee revenue, not an undisclosed token inventory.
- Mint authority and freeze authority are revoked after the audited launch/migration setup no longer needs them.
- Graduation is automatic and atomic when the configured quote target is reached. The curve closes and cannot be reopened.
- Graduated liquidity is locked or placed into an audited Burn-and-Earn/Fee-Key structure; it cannot be withdrawn through an admin dashboard.

### Fee example on USD 100,000 of curve volume

| Recipient/use | Rate | Value |
|---|---:|---:|
| Launching founder, quote asset | 0.50% | USD 500 |
| Platform operating reserve, USDC | 0.25% | USD 250 |
| Platform-token buyback and burn | 0.25% | USD 250 |
| **Total** | **1.00%** | **USD 1,000** |

### No project-token sale by DCF

- Curve fees are assessed and accounted for in SOL, USDC, ETH, or the configured quote asset on both buys and sells.
- The DCF treasury, operating reserve, and buyback vault do not accrue project tokens and expose no `sellProjectToken` route.
- If project tokens are accidentally transferred to a controlled vault, the recovery policy may return them to a disclosed project treasury or burn them after multisig approval. It may not market-sell them.
- This restriction applies to the platform fee machinery. It does not prevent ordinary holders from selling, and it does not create a hidden transfer restriction in the project token.

### Post-graduation

- Base pool/LP fee: target **0.25%**, distributed according to the audited venue's disclosed rules.
- Founder creator fee: **0.25%** of trade volume, paid to the founder-controlled claim vault in the quote asset.
- On Raydium CPMM, initialize the pool with creator fees enabled and `creator_fee_on` locked to the quote mint (`OnlyToken0` or `OnlyToken1`, depending on pool ordering). This takes creator fees from quote input on buys and quote output on sells.
- DCF platform creator-fee share: **0%**.
- Expected trader total: **0.50%** when the base pool fee is 0.25%. The interface must display the total and both components before confirmation.

This is deliberately simpler than a market-cap-dependent schedule for v1. It is cheaper than Pump.fun's current low-market-cap canonical-pool total, gives the founder predictable revenue, preserves LP compensation, and gives DCF no post-graduation founder-fee take.

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
2. **Select:** admin chooses supported quote assets and amount; project tokens are not eligible. The dashboard never exposes a raw private key.
3. **Policy:** apply the confirmed split, daily cap, minimum sweep size, approved routes, slippage, and MEV protection.
4. **Preview:** simulate expected USDC, expected platform tokens, price impact, route, gas, and minimum received.
5. **Propose:** create an immutable transaction bundle and proposal ID.
6. **Approve:** hardware-backed multisig members inspect the same payload and approve separately.
7. **Execute:** convert the operating portion of the quote asset to USDC where needed, execute a bounded platform-token buyback with the buyback portion, then burn to the canonical burn mechanism. Never sell the launched project token.
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

1. Choose Solana v1 venue after a Raydium LaunchLab versus Meteora DBC proof of concept using this exact 80/20 and quote-asset fee model.
2. Define chain-readable fee, supply, migration, no-project-token-sale, and authority interfaces before dashboard work.
3. Build wallet linking, message-signature verification, and founder fee-recipient rotation on devnet.
4. Build the treasury proposal/simulation/approval/receipt API and admin dashboard against a devnet multisig.
5. Add buyback limits, burn proof, observability, and emergency pause drills.
6. Obtain counsel and external contract audit.
7. Run adversarial testnet launch, graduation, rotation, compromise, and recovery scenarios.
8. Only then replace the current fixed-price `DexStubService` and old 0.1% UI copy.
