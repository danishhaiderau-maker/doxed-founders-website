# Reverse Tokenomics — Decision-Capture Spec (Detailed for External Review)

| Field | Value |
|-------|-------|
| **Status** | DRAFT SPEC — DECISION CAPTURE, NOT FOR IMMEDIATE BUILD |
| **Authored** | 2026-07-11 |
| **Ships** | Phase 7+ (after Raise Room Phase 1–3 matures) |
| **Owners** | Founder |
| **Pairs with** | [`RAISE_ROOM_LAUNCH_FLOW.md`](./RAISE_ROOM_LAUNCH_FLOW.md), [`RAISE-ROOM-VALIDATION-VISION-SPEC.md`](./RAISE-ROOM-VALIDATION-VISION-SPEC.md), [`FOUNDER_OS_NORTH_STAR.md`](./FOUNDER_OS_NORTH_STAR.md) |
| **Review audience** | ChatGPT, fintech counsel (AU/US/EU), smart-contract auditor |

> This document captures every locked decision, its reasoning, the concrete formulas, the attack vectors considered, and the open legal questions for the platform's own token ("reverse tokenomics") + the multi-chain launchpad/DEX that funds it. It is a decision-capture document, not a build ticket. Authored for external review before any contract is written.

---

## 0. Origin — what was rejected and why

The original request proposed: lock 80% of supply, use 100 controlled wallets to "snipe" the float back, hide wallet-to-wallet transfers, and give one admin dashboard control to coordinate wash buying across the 100 wallets.

**Rejected in full.** Reasons:
- Coordinated buying across 100 wallets to retain control while fabricating organic volume = **wash trading / market manipulation** (illegal under US §9(a)/§4c, EU/UK/AU equivalents) regardless of chain
- Seeking privacy infra specifically to obscure the admin ↔ 100-wallets relationship = **evasion of the transparency that makes the above illegal**, not user privacy
- The explicit goal ("control remains with us") is deception of other traders about who holds the supply

The **legitimate underlying goals** (fund the platform, reward early believers, retain some coordination) are achievable via a fair-launch structure — which is what this spec captures. The "reverse tokenomics" framing emerged from that pivot.

---

## 1. Core concept — "Reverse Tokenomics"

Every standard token does: **founders lock the big chunk, community gets the float, the end of vesting is a team exit that dumps on the market.**

Reverse Tokenomics inverts all three axes:

| Axis | Normal token | Reverse Tokenomics |
|---|---|---|
| Who gets the locked 80% | Founders/team | Community |
| How vesting pays out | Drips to insiders pro-rata | **Earned** by active community each quarter (Activity-Weighted Vesting) |
| The endgame (year 10) | Team unlock → sell pressure | **Champion payout TO community → no team exit** |

### 1.1 What is genuinely novel (honest claim)

Activity-weighted vesting and loyalty rewards exist in pieces across other projects. What's uncommon is the **combination**:
- Community-owned locked supply (not team)
- Earned quarterly releases (not pro-rata)
- A terminal champion payout instead of a team unlock (the endgame pays *to* the community, not *from* it)

**Marketing claim to make:** "reverse tokenomics." **Claim to avoid:** "first ever" (unverifiable, undermines credibility).

### 1.2 What is NOT novel (acknowledged)

- Bonding curves (Pump.fun since 2024)
- Buyback-and-burn (BNB since 2017, Raydium LaunchLab 2025)
- Creator fee shares (Believe, Moonshot)
- Merkle-claim distributions (standard since 2020 airdrop season)
- Omnichain tokens (LayerZero OFT since 2022)

Our novelty is the *assembly*, not the components.

---

## 2. Locked tokenomics spec

### 2.1 Supply breakdown

```
Total supply: 1,000,000,000 (1B) tokens — confirm (Pump.fun/Moonshot norm)

  Liquidity (LP):           20%   = 200,000,000
    → Uniswap V2 pair on Robinhood Chain: 200M tokens + ~$20 USDC
    → LP tokens locked 10 years (Unicrypt/Team.Finance or custom lock contract)
    → Bootstrap liquidity only; NOT a bonding curve (platform token is fair-launch)

  Community treasury:        80%   = 800,000,000
      ├─ Quarterly vesting:  64%   = 640,000,000
      │     → 2.0% of total supply per quarter = 20,000,000 tokens/quarter
      │     → = 2.5% of the 80% treasury per quarter (founder-chosen rate)
      │     × 32 quarters = 8 years of active releases (Q1 year 1 → Q4 year 8)
      │     → distributed via Activity-Weighted Vesting (AWV), NOT pro-rata
      │
      └─ Champion pool:      16%   = 160,000,000
            → locked untouched in ChampionLeaderboard contract until year-10 settlement
            → 2-year leaderboard finalization window (year 8 → 10)
            → auto-distributes to verified champions at year 10 via Merkle claim
```

### 2.2 Why these numbers (the math)

- Founder chose: 2.5% of the 80% treasury per quarter = 2.0% of total per quarter
- 40 quarters in 10 years × 2.0% = 80% → would fully drain the treasury by year 10, leaving **$0 for the champion pool**
- **Fix:** split the 80% treasury upfront — 64% quarterly vesting + 16% champion pool
- 64% ÷ 2.0% per quarter = 32 quarters = **8 years of vesting**
- Then a **2-year settlement window** (year 8 → 10) for leaderboard auditing + dispute resolution before the champion pool pays out
- 16% champion pool = 160M tokens — meaningful prize without dwarfing quarterly community releases (each quarter releases 20M; champion pool is 8× a single quarterly release)

### 2.3 Alternative considered (rejected)

- 1.5% of total per quarter × 40 quarters = 60% vested over 10 years + 20% champion pool
- Rejected: changes the founder's chosen 2.5%-of-80% rate; 10-year straight drip is less realistic than 8-year active vesting + 2-year audit window; smaller champion pool (20% vs 16% — wait, 20% is bigger; this was rejected because it changes the quarterly rate, not the pool size)

### 2.4 Team allocation

**Zero.** There is no team/found allocation in this spec. The founder earns via:
- The 50% founder revenue share of DEX fees (§5.2) — paid in USDC/native asset, ongoing
- The existing Raise Room Doxxed Builder flow (founder launches own projects via the platform)

This is the strongest fair-launch signal: **the team token allocation is literally 0%.** Every token is either in LP, vesting to community, or in the champion pool. This is the "reverse" in reverse tokenomics — no team exit at year 10 because there's no team allocation to exit.

---

## 3. Activity-Weighted Vesting (AWV) — the quarterly release mechanism

### 3.1 The problem with pro-rata vesting

Standard vesting drips pro-rata to holders — buy-and-hold whales get everything, active builders get nothing. This creates no incentive to participate in the ecosystem during the 8-year vesting window.

### 3.2 AWV formula (concrete)

Each quarter, 20,000,000 tokens release to wallets that proved genuine activity in the prior quarter. Score per wallet per quarter:

```
AWV_score(wallet, quarter) =
    + washFilteredVolume(wallet, quarter) × 0.40
    + lpDays(wallet, quarter) × 0.25
    + holdingDurationDays(wallet, quarter) × 0.20
    + governanceParticipation(wallet, quarter) × 0.10
    + builderContribution(wallet, quarter) × 0.05
```

**Component definitions:**
- `washFilteredVolume` — USDC value of genuine trades (see §3.4 wash filter), normalized to 0–100 scale against top trader that quarter
- `lpDays` — token-days of liquidity provided to DCFSwap pools (1 token for 1 day = 1 LP-day), normalized
- `holdingDurationDays` — days the wallet held platform token without selling below acquisition price, normalized
- `governanceParticipation` — 0–100: votes cast (×1), proposals submitted (×5), disputes resolved (×3)
- `builderContribution` — 0–100: verified commits to ecosystem repos, integrations shipped, audits completed

**Payout per wallet per quarter:**
```
userShareThisQuarter = walletAWV / sum(allWalletsAWV)
userPayout = 20,000,000 × userShareThisQuarter
```

### 3.3 Anti-whale caps

- **Max single-wallet payout per quarter: 5% of that quarter's release** (= 1,000,000 tokens). Excess is redistributed pro-rata to uncapped wallets.
- **Sybil resistance:** AWV score is weighted by `trustWeight` from existing `computeTrustWeight` (`packages/utils/src/trust-weight.ts`) — verified accounts score higher, new wallets score lower. Reuses existing anti-bot infra.
- **Minimum activity threshold:** wallet must have ≥1 genuine trade + ≥1 LP-day in the quarter to qualify. Filters passive holders entirely.

### 3.4 Wash-trade filtering algorithm (concrete)

The indexer computes a `clusterScore` for every trade pair (A, B):

```
clusterScore(A, B):
  1. Funding source clustering (0–3 points):
     - Trace 3 hops back from A and B to initial funding sources
     - Share a common funder within 3 hops → +1
     - Same funding transaction (same tx hash funded both) → +2
     - Same exchange withdrawal address funded both → +2
  2. Timing analysis (0–2 points):
     - A→B and B→A trades within 60s (circular) → +2
     - A and B always trade within ±5 min of each other (>80% of trades) → +1
  3. Behavioral (0–2 points):
     - A only ever trades with B (no other counterparties, >90% of volume) → +2
     - A and B have correlated P&L (one's gains ≈ other's losses, r > 0.8) → +1

  total = sum(1+2+3)

Volume weighting:
  clusterScore 0    → full weight (100% genuine)
  clusterScore 1–2  → half weight (50% — suspicious)
  clusterScore ≥3   → zero weight (flagged wash, excluded entirely)
```

This is deliberately conservative — better to undercount real volume than to let wash trades score. The 2-year champion challenge window (§6.6) catches false negatives that slip through.

### 3.5 Why these weights (40/25/20/10/5)

- **Volume 40%:** the founder explicitly wants "traders who traded the most" rewarded. Highest weight.
- **LP-days 25%:** liquidity providers are the backbone of any AMM; without them the DEX doesn't function. Second-highest.
- **Holding duration 20%:** "diamond hands" — penalizes dump-and-leave, rewards loyalty. Aligns with the "champion" ethos.
- **Governance 10%:** participation is valuable but shouldn't dominate (would favor DAO tourists over real traders).
- **Builder 5%:** small in AWV because builders get their own large champion bucket (§6.3) — double-counting would over-reward them.

---

## 4. Chain decisions

### 4.1 Platform token home: Robinhood Chain

**Decision:** Platform token canonical supply lives on Robinhood Chain only.

**Reasons:**
- Launched public mainnet July 1, 2026 — visibility upside for a new token (first-mover advantage on a fresh L2)
- EVM-compatible Arbitrum Orbit L2 — Chain ID `4663`, ETH for gas
- Full Solidity/Hardhat/Foundry/viem support; no exotic stack
- Uniswap V3 already deployed as day-one partner
- Canonical Arbitrum bridge to Ethereum L1 (audited, Ethereum-secured) for EVM cross-chain
- Block explorer: `robinhoodchain.blockscout.com`
- Public RPC: `https://rpc.mainnet.chain.robinhood.com`
- Testnet: Chain ID `46630`, RPC `https://rpc.testnet.chain.robinhood.com`

### 4.2 DEX + founder-launched tokens: Multi-chain

| Chain | Wallet | Bonding curve | Graduation AMM | Bridge to Robinhood (for burn) |
|---|---|---|---|---|
| Robinhood Chain | Rabby/MetaMask | EVM bonding curve (Solidity) | DCFSwap = Uniswap V2 fork | native (home chain) |
| Ethereum | Rabby/MetaMask | Same EVM bonding curve (redeployed) | DCFSwap = same V2 fork (redeployed) | Canonical Arbitrum bridge |
| Base | Rabby/MetaMask | Same EVM bonding curve (redeployed) | DCFSwap = same V2 fork (redeployed) | Canonical Arbitrum bridge |
| Solana | Phantom | Solana bonding curve program | DCFSwap-Solana = Raydium V4 fork or own program | Wormhole / LayerZero |

**Efficiency:** EVM bonding curve + DCFSwap = **one Solidity codebase deployed 3x** — same audit, three chains. Solana is a separate but parallel integration.

### 4.3 Why platform token is NOT multi-chain

A single supply split across 4 chains would:
- Fragment liquidity (bad for price discovery)
- Break champion scoring (which chain's volume is "real"?)
- Add bridge hack surface (bridges = #1 crypto hack vector: Wormhole $320M, Ronin $620M, Nomad $190M, etc.)

The token can be **bridged via LayerZero OFT v2** if demand exists on other chains (see §8.2), but canonical supply + vesting + champion settlement all live on Robinhood Chain.

---

## 5. Fee capture architecture

### 5.1 What was rejected (the frontend-router trap)

**Rejected: frontend router on existing Uniswap V3 with 0.1% surcharge.**

A frontend surcharge is **bypassable** — users go directly to Uniswap's UI or any aggregator and pay 0% extra. The platform captures nothing while having given away tokens. This only works for aggregators (1inch/Matcha), not launchpads. The founder correctly identified this flaw.

### 5.2 How real launchpads capture fees (research)

| Launchpad | Bonding curve fee | Post-grad fee | Capture mechanism |
|---|---|---|---|
| Pump.fun | 1.25% (1% protocol + 0–0.25% creator) | 0.25% on PumpSwap (20bps LP + 5bps protocol + creator cut) | Own bonding curve → own AMM (PumpSwap) |
| Believe | 2% dynamic (starts very high, drops to 2%) | ~2% embedded in **token contract** (1% creator + 0.1% scout + 0.9% platform) | Own bonding curve → fee embedded in token (non-bypassable on any AMM) |
| Moonshot (DexScreener) | 1% flat buy + sell | Creator up to 50% of pool fees; hourly payouts | Own bonding curve → migration fee to Meteora/Raydium/Uniswap |
| PancakeSwap SpringBoard | 1% on bonding curve | Pairs on PancakeSwap DEX | Own bonding curve → own AMM |
| Raydium LaunchLab | 1% (25% buys back RAY, founder up to +10%) | Migrates to Raydium | Own bonding curve → own AMM + token buyback |
| Spawned | 0.3% to creator | 1% post-grad via Token-2022 transfer fee | Token-level fee post-grad |

**Universal pattern: own the venue, so fee capture is non-bypassable.** Not one relies on a frontend surcharge.

### 5.3 Locked architecture — bonding curve + own AMM fork

```
PHASE 1 — BONDING CURVE (your contract, per chain)
  Every Raise Room graduate launches on YOUR bonding curve
  Constant-product curve: x (USDC/ETH/SOL) × y (token) = k
  Fee: 1% (0.5% founder + 0.5% buyback-and-burn platform token)
  Graduation threshold: $69K market cap (Pump.fun-proven number)
  → captures 100% of pre-graduation fees, no bypass possible
       │
       ▼ graduates
PHASE 2 — DCFSwap (own AMM fork, per chain)
  Fork Uniswap V2 (handles fee-on-transfer cleanly; V3 has FoF issues)
  Protocol-fee switch routes 0.25% → 0.125% founder + 0.125% buyback-and-burn
  Raise Room graduates ONLY migrate here → guaranteed liquidity onboarding
  → fee captured at pool level, non-bypassable on any frontend
```

### 5.4 Bonding curve math (constant-product, per founder-launched token)

```
Virtual reserves initialized at token launch:
  x0 = initial quote asset (USDC/ETH/SOL) raised during Raise Room pledging
  y0 = token supply allocated to bonding curve (e.g., 800M of 1B; 200M to founder)
  k = x0 × y0

Buy Δx quote → receive Δy tokens:
  fee = Δx × 0.01            // 1% bonding curve fee
  Δx_after_fee = Δx - fee
  Δy = y0 × (1 - x0 / (x0 + Δx_after_fee))
  fee split: 0.5% → founder revenue, 0.5% → buyback-and-burn

Sell Δy tokens → receive Δx quote:
  Δx_before_fee = x0 × (1 - y0 / (y0 + Δy))
  fee = Δx_before_fee × 0.01
  Δx = Δx_before_fee - fee
  fee split: same 0.5% / 0.5%

Graduation trigger: when market cap (x × price) ≥ $69K threshold
  → migrate remaining tokens + accumulated quote to DCFSwap pool
  → LP tokens burned to dead address (locked forever, like Pump.fun)
  → graduation is automatic and irreversible
```

### 5.5 Why DCFSwap works for us (and not for a standalone AMM fork)

The hardest part of forking Uniswap is bootstrapping liquidity — nobody trades on an empty AMM. But every Raise Room graduate *must* migrate somewhere, and if that's DCFSwap by default, we get guaranteed liquidity onboarding on every launch. Same moat Pump.fun used for PumpSwap. We're not competing for liquidity — we're funneling our own graduates in.

### 5.6 Long-term migration: Uniswap V4 hooks

When V4 lands on Robinhood Chain/Ethereum/Base, migrate DCFSwap to "Uniswap V4 + custom hook" — the hook charges a fee to treasury on every swap at the pool level, no full AMM fork needed. Less audit burden, same fee capture. V2 fork is the pragmatic now; V4-hooks is the elegant later.

### 5.7 Platform token's own LP — separate, no bonding curve

The platform token (reverse tokenomics) is a **fair launch**, not a launchpad token. It goes straight to Uniswap V2 LP with 20% supply + ~$20 USDC, LP locked 10 years. **No bonding curve, no platform fee capture on its own trading.** Platform revenue comes from launchpad fees (§6), not from the platform token's trading. Keep these separate in the architecture.

---

## 6. Fee structure + split

### 6.1 Locked fee schedule

| Phase | Fee | Split | Rationale |
|---|---|---|---|
| **Bonding curve** (pre-grad, ~90% of new-token volume) | **1%** | 0.5% founder + 0.5% buyback-and-burn | Market rate (Pump.fun 1.25%, Moonshot 1%, LaunchLab 1%) |
| **DCFSwap** (post-grad) | **0.25%** | 0.125% founder + 0.125% buyback-and-burn | Matches PumpSwap |
| **Platform token's own LP** | **0%** platform take | Standard Uniswap LP fees only | Not a launchpad token |

### 6.2 Why 50% founder / 50% burn

- **Founder 50%:** attracts builders to launch on our platform vs competitors (Pump.fun keeps ~all; Believe gives 50%; we give 50% + burn benefit). Founder rev share is the liquidity acquisition lever.
- **Buyback-and-burn 50%:** deflationary flywheel — more DEX volume → more buy pressure → reduced supply → price support → more founders attracted → more volume. Raydium LaunchLab proves the model (25% to RAY buyback; we do 50%, more aggressive).
- **Founder payout currency:** USDC (or native chain asset) by default — predictable revenue. Platform token is used for the burn only, not for founder payout. Easy to change later.

### 6.3 Why we do NOT go cheaper than 1% / 0.25%

We are **tied for cheapest** on both phases. Not uniquely cheapest.

| Option | Bonding | Post-grad | Revenue vs Pump.fun (same volume) | What you lose |
|---|---|---|---|---|
| **Current (recommended)** | 1% | 0.25% | ~80% of Pump.fun | — |
| Aggressive cheapest | 0.5% | 0.1% | ~40% of Pump.fun | Half founder rev + half burn pressure |
| Race-to-bottom | 0.25% | 0.05% | ~20% of Pump.fun | Burn flywheel barely functions |

Reasons not to go cheaper:
1. Undercuts the buyback-burn flywheel — weaker burn = weaker platform token = weaker differentiator
2. Founder revenue share at 0.5% is half as attractive — founders bringing launches is the whole liquidity engine
3. The real moat is the reverse-tokenomics structure, funded *by* the fee
4. Evidence: Pump.fun won the market charging 1.25% (more than competitors); Believe got traction at 2% (the most expensive) via UX. No launchpad has ever won a price war.

**Promotional lever (optional):** temporary fee discount for early launches (e.g., first 100 Raise Room graduates pay 0.5% bonding instead of 1%) to bootstrap liquidity, then normalize to 1%.

### 6.4 Economic projections (fee revenue at various DEX volumes)

Assumes 1% bonding curve fee on ~90% of volume + 0.25% DCFSwap fee on ~10% of post-grad volume. Blended ~0.925% effective fee.

| Daily DEX volume | Annual fees | Founder revenue (50%) | Buyback-and-burn (50%) |
|---|---|---|---|
| $100K/day | $338K/yr | $169K/yr | $169K/yr |
| $1M/day | $3.38M/yr | $1.69M/yr | $1.69M/yr |
| $10M/day | $33.8M/yr | $16.9M/yr | $16.9M/yr |
| $100M/day (Pump.fun peak) | $338M/yr | $169M/yr | $169M/yr |

**Context:** Pump.fun at peak (late 2024) was doing >$1M/day in fees from ~$100M/day volume. Our 1% is comparable. Even at $1M/day volume (modest), the platform funds $1.69M/yr in founder payouts + $1.69M/yr in buyback-burn.

---

## 7. Champion pool auto-distribution (year-10 settlement)

### 7.1 Architecture: off-chain indexer + on-chain Merkle claim

```
OFF-CHAIN INDEXER (open-source, reproducible)
  Reads every DEX trade across platform-launched tokens on ALL 4 chains
  Computes per formulas in §7.3
  Outputs: quarterly Merkle root of (address → score) per category
  Publishes root to ChampionLeaderboard contract + IPFS (full proof data)
       │ publishes root
       ▼
CHAMPION LEADERBOARD CONTRACT (on Robinhood Chain, non-upgradeable)
  - Stores quarterly Merkle roots (one per category per quarter)
  - Stores category totals (builders / traders / LP / active participants)
  - Year 8 → 10: challenge window (disputes via governance + timelock)
  - Year 10: final root → 16% pool (160M tokens) becomes claimable
  - Anyone with Merkle proof claims their slice
  - Single-chain settlement = small audit surface
```

**Why this design:**
- On-chain contract is dead simple (store root + verify proof + pay) — auditable in an afternoon. Complex scoring = hack surface.
- Scoring lives off-chain in an indexer anyone can re-run and verify against published roots. Disagreements surface in the 2-year challenge window, not after payout.
- Wash-trade filtering is easier to iterate off-chain than in a deployed contract.

### 7.2 Category split of the 16% champion pool (160M tokens)

| Category | Share of 16% | Tokens | What it rewards | Measurement |
|---|---|---|---|---|
| **Builders (biggest projects)** | 40% | 64M | Tokens launched + real volume | Project-level wash-filtered volume + LP-days + unique traders |
| **Top traders** | 25% | 40M | Genuine trading activity | Wash-filtered lifetime volume + holding duration |
| **LP loyalty** | 20% | 32M | Longest liquidity providers | Total LP-days across platform pairs |
| **Active participants** | 15% | 24M | Validation, scouting, governance | Founder Points accumulated over 10 years (existing bucket system) |

**Reuses existing Founder Points bucket system** from `RAISE-ROOM-VALIDATION-VISION-SPEC.md` (40/20/15/15/10), adapted for 10-year terminal payout. Builders get the largest share per founder's explicit ask for "biggest projects that created volume" as champions.

### 7.3 Scoring formulas (concrete, per category, over 10 years)

**Builders (40% of 16%):**
```
projectScore(project) = sum over project's lifetime of:
    + washFilteredProjectVolume × 0.50
    + uniqueTraderCount × 0.25
    + projectLpDays × 0.20
    + ecosystemIntegrations × 0.05

Weighted by project age: older successful projects score higher
  ageWeight = min(1.0, projectAgeYears / 5)
  finalProjectScore = projectScore × ageWeight

Project founder claims via Safe multi-sig (no single key drains large payout)
```

**Top traders (25% of 16%):**
```
traderScore(wallet) = sum over 10 years of:
    + washFilteredLifetimeVolume × 0.55
    + holdingDurationYears × 0.30
    + uniquePairsTraded × 0.10
    + crossChainActivityBonus × 0.05

crossChainActivityBonus: wallet active on >1 chain gets 1.5x multiplier
  (rewards genuine multi-chain traders, not single-chain farmers)
```

**LP loyalty (20% of 16%):**
```
lpScore(wallet) = sum over 10 years of:
    + totalLpDays × 0.70
    + lpPctOfPool × 0.20
    + impermanentLossToleranceBonus × 0.10
      (wallets that didn't withdraw LP during drawdowns score higher)
```

**Active participants (15% of 16%):**
```
Reuses existing Founder Points ledger accumulated over 10 years:
  Paper capital bucket (40%)
  Reviewers bucket (20%)
  Early followers bucket (15%)
  Top scouts bucket (15%)
  Builders bucket (10%)  [small — main builder reward is the 40% category above]

userShareInBucket = userBucketPoints / sum(allBucketPoints)
userAllocationPct = 15% × bucketWeight × userShareInBucket
```

### 7.4 "Biggest projects" payout mechanics (the founder's "champions trophy")

The Builders bucket (64M tokens) goes to **projects that launched tokens on the DEX**, weighted by their genuine ecosystem volume. The project's founder claims on behalf of the project via a **Safe multi-sig** (3-of-5: founder + 2 community-elected + 2 platform-nominated), so a single key can't drain a large project payout. This is the contract-enforced "champions trophy for the most successful projects" — the founder's words, implemented trustlessly.

### 7.5 2-year challenge window (year 8 → 10)

Before final settlement at year 10, published quarterly roots are open to dispute:
- Anyone can submit a wash-trading evidence package (funding cluster proof, circular flow proof) against a scored wallet/project
- Disputes adjudicated by elected community multi-sig + 7-day timelock
- If upheld, the wallet's score is slashed and the Merkle root re-published with corrected totals
- This is the anti-farm gate — kills wash-trade-then-claim attacks before payout

---

## 8. Cross-chain reward settlement

Three separate cross-chain flows, with different answers:

### 8.1 Founder revenue payout → NO bridge needed (easy)

The 50% founder share is **paid in the native asset of the chain where the fee was generated:**
- Solana launch → SOL (or USDC on Solana)
- Robinhood Chain launch → ETH/USDC on Robinhood
- Ethereum launch → ETH/USDC on Ethereum
- Base launch → ETH/USDC on Base

Founders live on the chain they launched on. **No bridge, no hack surface.** This is the easy 50%.

### 8.2 Buyback-and-burn → omnichain token, local burn (the hard one)

**Pattern: LayerZero OFT v2 (Omnichain Fungible Token).**
- Platform token has canonical supply on Robinhood Chain
- Bridged representations on Ethereum/Base/Solana via OFT v2
- On each chain, the 50% buyback share market-buys the *bridged* platform token on that chain's DEX → burns it locally → the burn propagates to the canonical supply on Robinhood Chain via OFT v2's burn-and-mint accounting
- **No value crosses chains — only a burn message.** Much lower hack surface than bridging USDC

**Why LayerZero OFT v2:** most-audited omnichain token standard. Moving burn messages (not value) directly addresses the founder's hack fear — bridges moving *value* are the #1 hack vector (Wormhole $320M, Ronin $620M, Nomad $190M). Pattern B moves only messages.

**Rejected alternative (Pattern A — bridge USDC fees to Robinhood, buy+burn there):** simpler but exposes value-in-transit on bridges. Rejected because of hack surface.

### 8.3 Champion pool payout (year 10) → NO bridge needed (easy)

The 16% champion pool contract lives on Robinhood Chain and holds the platform token. At settlement:
- Winners identified by off-chain indexer (aggregates volume across all 4 chains)
- Each winner specifies an **EVM address on Robinhood Chain** to claim
- Submit Merkle proof → contract pays out platform token on Robinhood Chain
- If they want tokens on Solana/Ethereum/Base afterward, *they* bridge via OFT v2 — their choice, their risk

A Solana trader winning a champion slice just needs an EVM wallet (Rabby/MetaMask on Robinhood Chain) to claim. Most active crypto users have both wallet types. Settlement contract stays single-chain = small audit surface = low hack risk.

### 8.4 Cross-chain reward map (summary)

```
FEE CAPTURED (1% bonding / 0.25% DCFSwap) on each chain
       │
       ├─ 50% FOUNDER REVENUE (paid in chain's native asset — NO bridge)
       │     Founder on Solana → SOL
       │     Founder on Robinhood → ETH/USDC
       │     Founder on Ethereum → ETH/USDC
       │     Founder on Base → ETH/USDC
       │
       └─ 50% BUYBACK-AND-BURN (omnichain token, local burn)
             LayerZero OFT v2. Buy bridged platform token on local DEX,
             burn locally → burn message propagates to canonical supply
             on Robinhood Chain. No value crosses chains.

CHAMPION POOL (16%, settles year 10) on Robinhood Chain
       Winners (traders/builders/LP/active across ALL 4 chains —
       indexer aggregates cross-chain volume) claim via an EVM wallet
       on Robinhood Chain. They bridge afterward if they want.
       NO bridge needed for the payout itself.
```

---

## 9. How traders are rewarded (per chain)

Traders **pay** the fee — they don't receive it. They're rewarded through three mechanisms:

| Reward | Who earns it | Paid in | When | Cross-chain mechanics |
|---|---|---|---|---|
| **Champion pool — trader bucket (25% of 16%)** | Top traders by wash-filtered lifetime volume | Platform token (on Robinhood Chain) | Year-10 settlement | Solana + EVM volume both count (indexer aggregates cross-chain); winner claims via EVM wallet on Robinhood Chain |
| **Founder Points — early-follower / paper-capital buckets** | Early backers of successful launches | The *launched* token's community allocation | At each launch's snapshot | Lives on whichever chain the launched token is on — no cross-chain issue |
| **Buyback-and-burn benefit** | Anyone holding the platform token | Price support + deflation | Continuous | 0.5% buyback buys platform token on DEX and burns it — benefits all holders regardless of chain (if token bridged via OFT v2) |

---

## 10. Attack vectors considered

| # | Attack | Mitigation |
|---|---|---|
| 1 | **Bonding-curve sniping** (bots buy at launch, dump on retail) | Believe-style dynamic fee: starts high (e.g., 5%) for first $5K of volume, drops to 1% as trading stabilizes. Penalizes snipers, rewards genuine late entry. |
| 2 | **Champion pool gaming via wash trades** | Wash-trade filter (§3.4) + 2-year challenge window (§7.5) + cluster-score zeroing at score ≥3 |
| 3 | **Buyback sandwich attacks** (MEV bots front-run our buyback) | Daily sweep (not per-swap) + tight slippage limits (50bps) + Cowswap-style batched auctions for the buyback tx + publish every burn on-chain |
| 4 | **Multi-sig signer collusion** | 3-of-5 threshold + elected signers + 7-day timelock + annual rotation + pause-only authority (cannot move treasury) |
| 5 | **Bridge exploit (LayerZero)** | Pattern B (messages not value) + LayerZero DVN (decentralized verifier network) config + multiple DVNs required + no value-at-risk in transit |
| 6 | **Bonding curve rug by founder** (founder pulls liquidity pre-graduation) | LP tokens burned at graduation (irreversible) + 100K DDollar escrow gate before launch (existing Raise Room anti-rug) + Doxxed Builder status required |
| 7 | **AWV gaming** (farm activity to earn quarterly vesting) | Wash filter + holding-duration weight (penalizes churn) + 5% per-wallet cap per quarter + trustWeight sybil resistance + minimum activity threshold |
| 8 | **Front-running champion leaderboard publication** | Merkle root challenge window (2 years) + dispute mechanism + re-publication of corrected roots |
| 9 | **Token contract bug** | OpenZeppelin ERC-20 standard (audited baseline) + external audit + Immunefi bug bounty + testnet deploy first |
| 10 | **Governance capture** (vote-buying for champion disputes) | Elected multi-sig (not token-weighted voting) + 7-day timelock + quadratic voting on disputes (1 person 1 vote via Proof of Humanity, not 1 token 1 vote) |
| 11 | **OFT bridge minting exploit** (attacker mints bridged tokens maliciously) | LayerZero's DVN consensus + rate-limiting on mint/burn + pause-capable via multi-sig |
| 12 | **Indexer compromise** (attacker publishes wrong Merkle root) | Anyone can re-run the open-source indexer and verify against published roots; challenge window surfaces discrepancies; multi-sig can reject fraudulent roots |

---

## 11. Hack-hardening (founder's stated fear, ranked by impact)

1. **Minimize novel contract code** — reuse Uniswap V2 (forked for DCFSwap), Sablier (vesting), OpenZeppelin (token + multi-sig), LayerZero OFT v2 (omnichain burn). Only novel contracts: platform token (trivial ERC-20), vesting schedule, champion leaderboard, bonding curve, FeeProcessor, BuybackBurner. All small and auditable.
2. **Testnet first** — Robinhood Chain testnet Chain ID `46630`, free. Deploy the full flow there, run a simulated 10-year settlement (fast-forward) before any mainnet ETH.
3. **Safe multi-sig treasury** — no single key controls anything. 3-of-5 elected signers, 7-day timelock, pause-only authority.
4. **Blast-radius limits** — champion pool contract only holds 16%; vesting contract only releases on schedule; **no admin override on the schedule** (only governance *pause* for critical bugs).
5. **No plaintext keys** — original "folder of 100 private keys" idea is the #1 self-hack vector. Treasury keys live in Safe + hardware signers; dashboard never touches keys.
6. **Bug bounty** — Immunefi listing once mainnet live.
7. **External audit** — for novel contracts (champion leaderboard, vesting, bonding curve, FeeProcessor, BuybackBurner). ~$15–40k for a reputable firm; cheap relative to a 10-year contract holding 16% of supply.
8. **Bridge risk minimized** — Pattern B (burn messages, not value) via LayerZero OFT v2; sweep frequently rather than accumulate large bridge balances; canonical Arbitrum bridge for EVM (audited, Ethereum-secured).

---

## 12. Launch integrity (the "fair" part)

- **Mint authority:** renounced at launch OR locked in a 10-year Sablier stream (renounce is stronger fair-launch signal; stream handles critical-bug edge case via governance pause)
- **LP tokens:** locked for the full 10 years via public locker contract (Unicrypt/Team.Finance or custom). Proof link on dashboard.
- **Treasury movement:** only the vesting contract can release, only on schedule, only to AWV-scored recipients. No admin key can override the schedule. Optional governance timelock (7-day) for pause-only in case of critical bug — controlled by elected multi-sig, not founder alone.
- **Supply distribution:** published at launch — dashboard shows top-10 holder %, Herfindahl index, LP lock proof, vesting countdown in real time. **The transparency is the marketing.**
- **Team allocation: 0%** — strongest fair-launch signal (§2.4).

---

## 13. Multi-sig governance (concrete)

**Safe multi-sig configuration:**
- **5 signers:** 2 founder-nominated (founder + 1 trusted advisor), 3 community-elected (annual election, 1-person-1-vote via Proof of Humanity / Gitcoin Passport to prevent whale capture)
- **Threshold:** 3-of-5 for any action
- **Timelock:** 7 days on all actions (publicly visible before execution)
- **Authority scope:** **pause-only** — can pause vesting/champion/buyback in case of critical bug; **cannot** move treasury, override vesting schedule, change fee splits, or redirect champion payouts
- **Rotation:** 1 community signer seat up for election annually
- **Signer removal:** any signer can be removed by 4-of-5 vote (prevents collusion)

**Why these choices:**
- 3-of-5 (not 2-of-3): tolerant of 2 signers going rogue/unavailable
- Elected (not token-weighted): prevents a wealthy attacker buying governance
- Pause-only (not admin): no human can redirect funds; the schedule is law
- 7-day timelock: community can detect + challenge malicious pause attempts

---

## 14. Public dashboard (reframed from original "admin control" ask)

Original LAN-URL admin dashboard (`http://10.0.0.102:9001/?admin_token=...`) becomes the **dev/ops monitor** for the founder. The real dashboard is **public read-only** and shows:

- Holder concentration (top-10 %, Herfindahl index) — proves no single wallet controls float
- Vesting countdown + next release timer + current AWV leaderboard preview
- Current Champions leaderboard (live, wash-filtered, per category)
- LP lock proof + on-chain link
- Buyback-and-burn rate (live: how much platform token burned this week from DEX volume)
- Builder bounty queue + quadratic funding round status
- Soulbound milestone badge roster (holders of 1yr/3yr/5yr/10yr badges)
- Bridge flows (OFT v2 mint/burn events across chains)

This is the opposite of the original "hide my control" dashboard — it's "prove to the world nobody controls this."

---

## 15. Contract list + audit status

| # | Contract | Chain(s) | Type | Audit status |
|---|---|---|---|---|
| 1 | PlatformToken (ERC-20) | Robinhood | OpenZeppelin standard | Baseline audited (OZ) |
| 2 | VestingContract (AWV quarterly release) | Robinhood | Novel (Merkle claim + schedule) | Needs external audit |
| 3 | ChampionLeaderboard | Robinhood | Novel (Merkle store + claim) | Needs external audit |
| 4 | BondingCurve | All 4 chains | Novel (constant-product + graduation) | Needs external audit |
| 5 | DCFSwapPool | All 4 chains | Uniswap V2 fork + fee switch | Re-audit changes only |
| 6 | FeeProcessor | All 4 chains | Novel (50/50 split + sweep) | Needs external audit |
| 7 | BuybackBurner | All 4 chains | Novel (market buy + burn) | Needs external audit |
| 8 | LayerZeroOFTAdapter | All 4 chains | LayerZero OFT v2 standard | Baseline audited (LayerZero) |
| 9 | SafeMultiSig (treasury) | All 4 chains | Safe standard | Baseline audited (Safe) |
| 10 | GovernanceTimelock | All 4 chains | OpenZeppelin standard | Baseline audited (OZ) |
| 11 | SoulboundBadgeNFT | Robinhood | Novel (ERC-721 + transfer disabled) | Needs audit (simple) |
| 12 | DDollarPledgeEscrow | All 4 chains | Existing in codebase, extended | Existing audit + review changes |

**Novel contracts requiring external audit:** 5 (VestingContract, ChampionLeaderboard, BondingCurve, FeeProcessor, BuybackBurner) + 1 simple (SoulboundBadge). Estimated audit cost: $15–40k for a reputable firm.

---

## 16. Token standard choices

| Chain | Standard | Reason |
|---|---|---|
| EVM (Robinhood/Ethereum/Base) | **ERC-20 + ERC-2612 (permit)** | Max compatibility, gasless approvals via permit. NOT ERC-777 (reentrancy history). |
| Solana (founder-launched tokens) | **Token-2022** | Native TransferFee extension — enables Believe-style embedded fee at the token level if we want non-bypassable fee capture post-grad on Solana too |
| Solana (platform token bridged rep) | **Token-2022 via OFT** | Consistent with Solana token program + LayerZero OFT |

---

## 17. Integration with existing Raise Room + Founder OS

### 17.1 What already exists (reused, not duplicated)

From `RAISE_ROOM_LAUNCH_FLOW.md` + `RAISE-ROOM-VALIDATION-VISION-SPEC.md`:
- **Founder OS shell** has "I'm Ready — Launch My Token" button (Phase 7+ gated)
- **Raise Room** has 100K DDollar community-pledge threshold (anti-rug escrow), Doxxed Builder status, 90-day auto-refund
- **Founder Points + 5 buckets** already spec'd for earned (not equal) distribution: Paper capital 40% / Reviewers 20% / Early followers 15% / Top scouts 15% / Builders 10%
- **Fee tiers Bronze/Silver/Gold** (0.10% / 0.05% / 0% platform fee) already in spec
- **FounderLaunchScore** formula (6-component, 0–100) already spec'd
- **trustWeight** sybil-resistance scoring already in `packages/utils/src/trust-weight.ts`

### 17.2 What changes

| Existing spec | Updated to |
|---|---|
| Solana-only (Phantom, Meteora, Pump.fun) | Multi-chain: founders choose Robinhood/Ethereum/Base/Solana at graduation |
| Jupiter swap, 0.5% fee | DCFSwap (own AMM fork) + bonding curve, 1% / 0.25% tiered |
| One-shot Founder Points snapshot | **Extended to 10-year accumulating ledger** feeding champion pool scoring |
| 5% to pledgers at launch | Keep, plus integration with platform token champion pool scoring |
| Bronze 0.10% / Silver 0.05% / Gold 0% platform fee | Replaced by bonding-curve 1% / DCFSwap 0.25% tiered (more revenue, non-bypassable) |

### 17.3 Key reuse insight

Champion pool's category split (40/25/20/15) is the **existing Founder Points bucket system extended from one-shot snapshot to 10-year accumulating score, with terminal payout.** Same machinery, longer horizon, bigger payoff. Existing design work is the foundation, not wasted.

---

## 18. Regulatory analysis (per jurisdiction — for legal review)

### 18.1 United States

| Question | Analysis | Risk level |
|---|---|---|
| **Is the platform token a security?** (Howey test) | Likely NOT at launch: investment of money ✓, common enterprise ✓, expectation of profits from efforts of others ✗ (no team allocation, no promoter effort — pure community vesting). BUT champion pool may change analysis: holders trade expecting champion payout. | Medium — needs SEC counsel |
| **Is the champion pool a security?** | Howey "efforts of others" prong arguably fails (winners earn by own activity, not promoter effort). But SEC could argue the platform's indexer/scoring is "efforts of others." | Medium-high — needs counsel |
| **Is the bonding curve a securities offering?** | SEC v. Ripple + recent cases suggest token sales via bonding curve may be securities if marketed as investments. Mitigation: market as "utility tokens for community building," not investments. | High — geofencing US may be required initially |
| **Is the DEX a "swap" or "facility" under CFTC?** | CFTC has asserted jurisdiction over DEXs. May need to register as SEF or geofence. | High |
| **Multi-chain fee collection = money transmission?** | FinCEN may view cross-chain fee routing as MSB activity. LayerZero OFT pattern (messages not value) is the mitigation. | Medium |
| **Buyback-and-burn = manipulation?** | Legal in equities (Rule 10b-18). Crypto treatment evolving. On-chain disclosure is the mitigation. | Medium |

### 18.2 Australia (home jurisdiction)

| Question | Analysis | Risk level |
|---|---|---|
| **AFSL required?** | If platform token or champion pool is a "financial product" under Corporations Act, AFSL needed. Existing spec already flags this (§H of RAISE-ROOM-VALIDATION-VISION-SPEC). | High — needs AU fintech counsel |
| **CSF regime applies?** | Crowd-sourced funding exemptions may apply to fair-launch distribution. | Medium |
| **AML/CTF obligations?** | DEX + fee collection likely triggers AML/CTF Act registration. | High |

### 18.3 European Union

| Question | Analysis | Risk level |
|---|---|---|
| **MiCA applies?** | Crypto-asset regulation effective Dec 2024. Launchpad may need CASP (crypto-asset service provider) authorization. Platform token may be "utility token" exempt if genuinely utility. | High — needs EU counsel |
| **Bonding curve = "asset-referenced token"?** | If so, stricter rules apply. | Medium |

### 18.4 Recommended regulatory posture (for counsel to refine)

1. **Geofence US + restricted jurisdictions at launch** (same as existing Raise Room spec §11)
2. **Market as utility, not investment** — "community vesting for ecosystem participation," not "buy for champion payout"
3. **Disclose everything on-chain** — buyback-burn, vesting schedule, champion scoring formula all public
4. **No team allocation** is the strongest defense against "promoter effort" prong of Howey
5. **AU fintech counsel engaged before Phase 4** (existing spec requirement, extended to this spec)
6. **EU MiCA counsel before any EU user onboarding**

---

## 19. Open questions / decisions still to make

| # | Question | Default proposal |
|---|----------|------------------|
| 1 | Founder revenue payout currency: USDC or platform token? | USDC (predictable); platform token for burn only |
| 2 | Bonding curve graduation threshold | $69K market cap (Pump.fun-proven) |
| 3 | Promotional early-launch discount | First 100 Raise Room graduates at 0.5% bonding, then normalize to 1% |
| 4 | Champion pool category percentages | 40/25/20/15 as proposed (reuse Founder Points buckets) |
| 5 | Platform token total supply size | 1B (Pump.fun/Moonshot norm) — confirm |
| 6 | Uniswap V4 hooks migration timing | When V4 lands on Robinhood Chain; V2 fork until then |
| 7 | Mint authority: renounce vs 10yr Sablier stream | Renounce (stronger fair-launch signal); governance pause via multi-sig for critical bugs |
| 8 | Bridged platform token representations: when to enable on non-Robinhood chains | When DEX demand exists on that chain; not at launch |
| 9 | Buyback frequency (gas vs MEV exposure) | Daily sweep or when accumulated > $threshold; tight slippage (50bps); publish every burn on-chain |
| 10 | Geofencing: which jurisdictions blocked from DEX | Needs legal input — same open question as `RAISE_ROOM_LAUNCH_FLOW.md` §11 |
| 11 | AWV component weights (40/25/20/10/5) — confirm or adjust | As proposed; may tune after first quarter of operation |
| 12 | Wash-trade cluster-score thresholds (0 / 1–2 / ≥3) | As proposed; may tighten after observing gaming patterns |
| 13 | Multi-sig signer election mechanism | Proof of Humanity / Gitcoin Passport for 1-person-1-vote; confirm |
| 14 | Bonding curve dynamic fee (Believe-style anti-sniper) | Implement: 5% for first $5K volume → drops to 1% — confirm |
| 15 | Token decimals + name + ticker | TBD by founder |

---

## 20. What "EZA" referred to (unresolved)

During research the founder mentioned "EZA" as a launchpad to compare. Search returned `eza` (a command-line file-listing tool, `eza-community/eza`), not a crypto launchpad. Possibly a different name was meant — candidates: **Raydium LaunchLab**, **PancakeSwap SpringBoard**, **Believe** (the "tweet-to-launch" one, also referred to as "Billy/Bilib"). The fee-capture pattern is identical across all of them regardless, so this doesn't change the architecture.

---

## 21. External review checklist (for ChatGPT / legal / auditor)

When reviewing this spec, pressure-test these questions:

1. **Is "reverse tokenomics" legally defensible framing?** Does calling community-owned locked supply + earned vesting + champion payout "reverse tokenomics" create securities-law exposure vs just calling it a vesting schedule?
2. **Is the champion pool a "security" or "investment contract"?** (Howey test: investment of money, common enterprise, expectation of profits from efforts of others — does the "efforts of others" prong fail because winners earn by *their own* activity, or does the platform's indexer/scoring count as "efforts of others"?)
3. **Is the bonding curve itself a regulated instrument?** Some jurisdictions treat bonding-curve launches as securities offerings. Geofencing may be required.
4. **Is the 50% buyback-and-burn "manipulative"?** Buybacks are legal in equities (Rule 10b-18) but crypto regulatory treatment is evolving. On-chain disclosure is the mitigation — is it sufficient?
5. **Multi-chain fee collection + omnichain burn — money-transmitter / MSB exposure in US or AU?** Does LayerZero OFT v2 (messages not value) actually avoid MSB classification?
6. **AWV (Activity-Weighted Vesting) — is paying people to trade "incentivizing volume," adjacent to wash-trading concerns?** The wash-trade *filter* is the defense — is it legally sufficient?
7. **10-year contract immutability — if a regulator orders a halt mid-vesting, can the contract actually stop?** The governance pause is the answer — is multi-sig + timelock legally sufficient?
8. **AU fintech framing** — does the additional complexity (bonding curve, buyback-burn, champion pool, omnichain token, multi-chain DEX) change the AFSL/CSF/AML analysis from the existing Raise Room spec?
9. **Zero team allocation — does this actually defeat Howey "promoter effort" prong, or does the founder's ongoing platform operation count as promoter effort?**
10. **Bonding curve dynamic fee (5% → 1%) — does this constitute "discriminatory pricing" that could be challenged?**
11. **Champion pool 2-year challenge window — is "dispute adjudicated by elected multi-sig" legally enforceable, or does it create a dispute-resolution venue that needs recognition?**
12. **OFT v2 bridged representations on Solana/Ethereum/Base — do these count as separate token issuances in each jurisdiction?**
13. **Bonding curve math — is the constant-product + graduation + LP-burn pattern safe from "graduation rug" attacks where a founder times the graduation?**
14. **Wash-trade filter — is the cluster-score algorithm robust enough to survive legal challenge (if a flagged wallet sues for exclusion from champion pool)?**
15. **Fee revenue projections (§6.4) — are these realistic, and do they create any "expectation of returns" marketing problem if shared publicly?**

---

## Changelog

| Date | Change |
|------|--------|
| 2026-07-11 | Initial decision-capture spec authored from multi-turn design conversation: original manipulation request rejected → fair-launch pivot → reverse tokenomics framing → locked tokenomics (20/64/16, 0% team) → Robinhood Chain as platform token home → multi-chain DEX (4 chains) → bonding curve + own AMM fork (corrected from frontend-router model) → 1% / 0.25% tiered fees → 50% founder / 50% burn split → concrete AWV scoring formula → concrete champion pool scoring formulas → wash-trade filter algorithm → cross-chain settlement via LayerZero OFT v2 (burn messages, not value) → 12 attack vectors enumerated → 15-contract audit list → per-jurisdiction regulatory analysis (US/AU/EU) → 15-question external review checklist. |
