# DCFSwap Fees, Custody, and Authority Rotation

| Field | Value |
|---|---|
| Status | Working launch templates; Founder Enterprise, settlement, Growth Reserve, and emergency migration require founder, legal, security, tax, AML, and audit approval |
| Updated | 2026-07-21 |
| Applies to | Bonding curve launches, graduated pools, founder vesting/payouts, platform treasury, acquisition settlement, liquidity, buyback/burn, and emergency migration |
| Supersedes | Conflicting post-graduation splits and permanent-wallet assumptions in older tokenomics documents |

This document records the final recommended economics without pretending that a website login is a blockchain security system. Contract code must not ship until legal/security review and an independent contract audit are complete.

## 1. Decisions captured

### Bonding curve

- The total trading fee during the bonding curve is **1.00%**.
- **0.50% of trade volume** goes to the verified launching founder in the quote asset.
- **0.25% of trade volume** goes to the platform operating reserve in USDC or is converted from the native quote asset to USDC in bounded batches.
- **0.25% of trade volume** funds a platform Growth Reserve in the quote asset. It adds protocol-owned liquidity first; buyback or burn is never automatic.
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

- An admin dashboard may prepare treasury conversions, liquidity additions, basket rebalances, platform-token buybacks, burns, payout-address changes, pauses, and emergency actions.
- A dashboard click creates a proposal. It must not directly use a server-held private key.
- High-risk proposals require an on-chain multisig threshold, transaction simulation, a delay where appropriate, and a permanent receipt.
- Deployer and developer keys are removed after deployment. A contractor or AI system must have no retained production authority.

## 2. Launch economics under review

### Supply and liquidity

- Fixed supply: **1,000,000,000 project tokens**.
- The earlier **Fair Launch** makes **80%** available through the bonding curve and reserves **20%** for initial post-graduation liquidity with the accumulated curve quote reserve.
- The Fair Launch has **0% unlocked founder allocation** and **0% undisclosed platform allocation** at genesis. Any optional Conviction Vault is public and time-locked from creation.
- Mint authority and freeze authority are revoked after the audited launch/migration setup no longer needs them.
- Graduation is automatic and atomic when the configured quote target is reached. The curve closes and cannot be reopened.
- Graduated liquidity is locked or placed into an audited Burn-and-Earn/Fee-Key structure; it cannot be withdrawn through an admin dashboard.

### Founder Enterprise Launch (latest proposal)

This is a separate long-horizon company-building template, not a hidden modification of the Fair Launch:

- **50% Founder Stewardship Vault:** 500,000,000 tokens reserved for the verified founder/team and locked at genesis.
- **40% bonding curve:** 400,000,000 tokens available to public participants under the disclosed curve.
- **10% graduated-liquidity reserve:** 100,000,000 tokens paired with the curve's quote reserve under the audited migration design.
- **0% DCF project-token allocation:** DCF earns only the disclosed quote-asset fees in this template unless a later independently approved allocation is stated before launch.
- Vesting is linear and continuous from the exact on-chain start timestamp to the exact ten-year end timestamp. Claimable amount is `floor(totalGrant * elapsed / duration) - alreadyClaimed`; daily jobs and monthly cliffs are not used.
- For illustration only, a 500 million grant over an average 3,652.5-day decade accrues about **136,893 tokens per day**, equal to **0.01369% of total supply per average day**. The contract's timestamp formula controls, including leap years and integer rounding.
- Accrual may be displayed daily, but unclaimed amounts stay in the vault. Claiming does not sell, swap, or transfer any other holder's tokens.
- Each team grant has a disclosed beneficiary, service/role terms, start/end, claimed/claimable/unvested balances, and departure policy. A team administrator cannot silently redirect another beneficiary's vested amount.
- Beneficiary rotation is a two-step, delayed authority change that preserves the grant and vesting clock. The original creation wallet is not an irreplaceable dependency.
- An acquisition/change-of-control policy must state whether unvested grants continue, accelerate on one trigger, accelerate only after a second employment trigger, or return to a disclosed reserve. It cannot be invented after an offer arrives.

This resembles private-company founder vesting more than a hype launch, but ten years is much longer than the common four-year/one-year-cliff startup pattern. The longer term is a deliberate alignment decision, not proof that the token is legally equity. Founder/team control, investor rights, company shares, token voting, and acquisition proceeds must be defined in enforceable company and offering documents.

### Fee example on USD 100,000 of curve volume

| Recipient/use | Rate | Value |
|---|---:|---:|
| Launching founder, quote asset | 0.50% | USD 500 |
| Platform operating reserve, USDC | 0.25% | USD 250 |
| Platform Growth Reserve, quote asset | 0.25% | USD 250 |
| **Total** | **1.00%** | **USD 1,000** |

### No automatic project-token sale by DCF

- Curve fees are assessed and accounted for in SOL, USDC, ETH, or the configured quote asset on both buys and sells.
- The DCF operating reserve and Growth Reserve do not collect project tokens as a transfer fee and expose no automatic `sellProjectToken` route.
- Project tokens may enter only through the disclosed optional Conviction Vault or an accidental-transfer recovery path.
- Vesting, a price threshold, or an unlock date cannot trigger a sale. Every disposal is a separate public multisig proposal under the basket policy below.
- This restriction does not prevent ordinary holders from selling and does not create a hidden transfer restriction, freeze backdoor, or permanent platform delegate in the project token.

### Optional DCF Conviction Vault (proposal)

The founder has proposed replacing some quote-asset platform revenue with a transparent project-token position that demonstrates long-term alignment. The recommended implementation is a separate genesis allocation, not a transfer fee and not an automatic market seller:

- **Proposed allocation:** 2% of the fixed supply, taken from the curve allocation. An aligned launch would therefore use 78% curve, 20% graduated liquidity, and 2% DCF Conviction Vault. Supply remains fixed at 1 billion.
- **Vesting:** linear vesting over ten months. The dashboard may show ten monthly checkpoints, but the contract should vest continuously to avoid ten predictable cliff dates.
- **No automatic sale:** vesting only changes `unvested` to `vested`. It does not create a swap, market order, or permission for a backend worker to sell.
- **Separate approval:** any later disposal of vested tokens requires a public treasury proposal, transaction simulation, the platform multisig threshold, a delay, price-impact and volume limits, and an immutable receipt. The platform may continue holding vested tokens indefinitely.
- **No token transfer tax:** the project token remains a normal token. The vault allocation is visible at launch and does not impose hidden buy/sell behavior on holders or AMMs.
- **Founder choice and disclosure:** this should be an explicit **Aligned Launch** term accepted before token creation, not a silent default added after a project becomes valuable.

The Conviction Vault should not replace all quote-asset operating revenue. A new or illiquid project token cannot reliably pay infrastructure, audits, support, tax, or incident response. The 0.25% quote-asset operating reserve should remain until financial modelling and legal review prove a lower amount is sustainable. If the proposal is adopted, the public launch screen must show the exact vault allocation, vesting curve, multisig address, vested/unvested balances, current price source, liquidity, next checkpoint, end date, and every proposed or completed disposal.

### Growth Reserve lifecycle (recommended)

The 0.25% Growth Reserve is a quote-asset treasury bucket, not an immediate buyback promise:

1. **Liquidity-first stage:** use approved, bounded batches to build protocol-owned platform-token liquidity. Adding liquidity still requires simulation, slippage/MEV controls, and multisig execution.
2. **Activation gate:** do not enable buyback or burn merely because one spot quote shows a USD 1 million market cap. Require at least 30 consecutive days above that threshold using an independent time-weighted price source, adequate liquidity depth, organic-volume checks, and a funded operating runway.
3. **Governed stage:** after the gate, the dashboard may propose liquidity, reserve, buyback, or burn allocations. Default automation remains off. Every execution has a public transaction receipt.
4. **Policy changes:** a timelocked governance proposal may change how future Growth Reserve accruals are used. It cannot undo a completed burn, seize founder fees, or rewrite a user's existing claim.

This keeps the founder's USD 1 million target while preventing a briefly manipulated price from activating irreversible treasury behavior.

### DCF Launch Basket (recommended internal treasury policy)

The collection of vested Conviction Vault positions can be shown as a transparent multi-project launch basket. It should remain a treasury reporting view, not be sold to users or called an ETF until specialist counsel confirms a licensed structure.

- A rebalance may include the same percentage of every eligible vested position, with **1% of each vested position per cycle as the outer ceiling**, rather than selecting one successful project to fund the entire platform.
- The executable amount is always the smallest of that 1% ceiling, the published per-asset limit, and the amount allowed by trailing organic volume, liquidity depth, maximum price impact, and oracle checks.
- Illiquid, manipulated, paused, or compromised assets are excluded automatically. Exclusion is protective and does not authorize a larger sale of another project.
- Proceeds may fund operating runway or protocol-owned liquidity. Buyback and burn remain separate proposals; basket rebalancing never triggers them automatically.
- The dashboard shows each mint, cost basis, current independent price, liquidity, 24-hour/30-day organic volume, vested/unvested amount, next checkpoint, vesting end, unrealized/realized value, proposed amount, limits, approvals, and transaction receipt.
- Marketing must not promise returns, price support, or diversification benefits. In Australia, pooling assets and offering users an interest can be a managed investment scheme, while an ETF is a regulated exchange-traded product.

### Acquisition interest and Settlement Vault (proposal)

- Project profiles may expose **Open to acquisition interest** only when the founder enables it. Verified buyers submit non-binding offers; private contact and data-room access require founder acceptance.
- The profile shows source-labelled market, liquidity, holder distribution, founder vested/claimable/claimed/unvested balances, authority, and product evidence. It must distinguish on-chain facts, founder assertions, third-party estimates, and independently verified documents.
- Company acquisition proceeds normally follow the company's legal ownership and transaction documents. Token supply percentages do not automatically define the acquisition waterfall.
- If executed legal documents allocate a fixed amount to eligible token holders, the buyer or regulated escrow fully funds an **Acquisition Settlement Vault** before claims open.
- The settlement publishes the snapshot block, eligible-supply denominator, exclusions, payout asset, exchange rate, treatment of founder vault/LP/treasury/bridge/custody balances, claim deadline, dispute process, and unclaimed-funds policy.
- An independently reproducible snapshot/Merkle root fixes wallet entitlements. Eligible holders complete required identity/sanctions checks and burn or irrevocably surrender tokens to claim stablecoin. Claims are idempotent and permanently receipted.
- Pause authority may stop claims during an incident but cannot redirect settlement funds. No admin can alter one holder's entitlement after the root becomes final except through the published dispute/governance process.
- Burn-to-claim stablecoin settlement is technically feasible only after legal approval. Token-to-company-share conversion is a separate regulated issuance/transfer-agent/cap-table workflow and is out of scope for the first settlement implementation.

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

### Platform treasury and growth actions

- Use a Solana multisig vault for Solana assets and a Safe-style multisig for EVM assets.
- Start with separated roles: proposer, approvers, executor, emergency pauser, and guardian/recovery.
- Do not use `1-of-n`. Do not require every key either. A practical initial personal setup is `2-of-3`, with keys on separate devices and one recovery key stored offline; expand for a team.
- Apply destination allowlists, per-asset daily limits, liquidity/rebalance/buyback slippage limits, oracle checks, and transaction simulation.
- Use time locks for wallet/authority changes and large transfers. Emergency pause may be immediate but may not move funds.
- Keys held by one person on three devices improve recovery but still leave one human as the governance and coercion point. Move to independent co-signers before public mainnet value is material.

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

1. **Accrual:** show quote fees and Conviction Vault positions by launch, token, chain, vesting state, age, and independently sourced USD estimate.
2. **Select:** admin chooses a permitted Growth Reserve action or a basket rebalance. Unvested project tokens are never eligible. The dashboard never exposes a raw private key.
3. **Policy:** apply the confirmed stage, daily/per-asset cap, minimum batch, approved routes, price source, slippage, volume/liquidity limits, and MEV protection.
4. **Preview:** simulate assets in/out, price impact, pool health, route, gas, minimum received, and the post-transaction treasury and liquidity state.
5. **Propose:** create an immutable transaction bundle and proposal ID.
6. **Approve:** hardware-backed multisig members inspect the same payload and approve separately.
7. **Execute:** perform only the approved liquidity, reserve, rebalance, buyback, or burn action. There is no chained automatic sell-then-burn behavior.
8. **Prove:** store transaction hashes, amounts, price impact, approvals, policy version, and any liquidity/burn proof; make the economic receipt public.

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

### Emergency response and token migration

- The admin dashboard may expose one **Start emergency response** command, but it creates a signed incident proposal and runbook rather than silently moving every asset.
- A narrowly scoped guardian can immediately pause DCF-owned curve, router, claim, and treasury actions. The pause key cannot transfer funds, change recipients, mint, upgrade, or sell.
- If a token replacement is required, publish the incident, select a snapshot block, deploy an independently reviewed replacement, and provide a 1:1 claim path. Holders sign their own claims; the platform cannot rewrite balances held in user wallets.
- Migrate only protocol-owned liquidity under multisig approval. Third-party LP positions and tokens in external wallets cannot be seized or moved.
- Do not retain Solana Token-2022 `PermanentDelegate` merely to enable forced migration: that authority can transfer or burn tokens from any holder account and cannot be revoked by the holder. Revoke mint/freeze authority once the audited launch lifecycle permits.
- Upgradeable DCF contracts use a multisig plus timelock and validated storage layouts. Emergency pause is fast; upgrades and treasury movement are deliberately slower.
- Rehearse compromised-key, oracle failure, bridge failure, pool drain, bad upgrade, and chain-halt scenarios on testnet. Publish recovery time objectives and the exact point where DCF cannot recover third-party assets.

Useful official security primitives:

- OpenZeppelin AccessManager and delayed admin rules: https://docs.openzeppelin.com/contracts/5.x/access-control
- Solana token authority changes: https://solana.com/docs/tokens/basics
- Solana Permanent Delegate authority: https://solana.com/docs/tokens/extensions/permanent-delegate
- OpenZeppelin Pausable utility: https://docs.openzeppelin.com/contracts/5.x/api/utils
- OpenZeppelin upgrade-safety rules: https://docs.openzeppelin.com/upgrades-plugins/writing-upgradeable
- Squads thresholds, time locks, and vault guidance: https://docs.squads.so/main/navigating-your-squad/settings
- Squads security practices: https://docs.squads.so/main/additional-resources/advanced-security-best-practices

## 8. Legal and market-integrity gate

Buyback/burn promises, founder/team vesting, discretionary treasury sales, token launches, creator revenue, custody, acquisition inquiries, acquisition-proceeds rights, stablecoin redemption, a pooled launch basket, and multi-chain settlement require specialist AU/US/EU advice before launch. A token marketed with company ownership, managerial-profit, share-conversion, or acquisition-payout rights is likely to engage securities/financial-product rules. The public policy must not promise price support. A public interest in the basket may be a managed investment scheme or another financial product; calling it an ETF does not make it one. Stablecoin claims may engage virtual-asset service, customer-due-diligence, sanctions, reporting, custody, tax, and unclaimed-property obligations. Use disclosed, bounded execution rules and immutable receipts. This document is product/security design, not legal or financial advice.

## 9. Build order

1. Obtain a written legal classification of the Fair Launch, Founder Enterprise grant, acquisition inquiry, token-holder settlement right, and stablecoin claim flow in the first supported jurisdictions.
2. Decide whether v1 offers only the Fair Launch or also the Founder Enterprise template; lock the exact supply split and acquisition waterfall before contract code.
3. Choose the Solana v1 venue after a Raydium LaunchLab versus Meteora DBC proof of concept for the selected template and quote-asset fee model.
4. Define chain-readable fee, vesting, grant departure/change-of-control, migration, claim, settlement, and authority interfaces before dashboard work.
5. Build wallet linking, signature verification, beneficiary/fee-recipient rotation, and continuous vesting on devnet.
6. Build treasury and acquisition-settlement proposal/simulation/approval/receipt APIs against devnet multisigs and fully funded test escrows.
7. Add project intelligence, holder snapshots/cohorts, liquidity-first Growth Reserve rules, observability, disputes, and emergency drills.
8. Obtain external contract audit and run adversarial launch, graduation, vesting, acquisition, claim, rotation, compromise, and recovery scenarios.
9. Only then replace the current fixed-price `DexStubService` and old 0.1% UI copy.
