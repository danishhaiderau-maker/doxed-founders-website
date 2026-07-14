# Reverse Tokenomics — Design Journey (Start to Finish)

| Field | Value |
|-------|-------|
| **Status** | Decision-capture narrative — the full conversation that produced [`REVERSE-TOKENOMICS-SPEC.md`](./REVERSE-TOKENOMICS-SPEC.md) |
| **Authored** | 2026-07-11 |
| **Purpose** | Complete chronological record of every proposal, rejection, pivot, and locked decision — for review, handoff, and audit trail |

> This document is the *how we got here*. The companion spec (`REVERSE-TOKENOMICS-SPEC.md`) is the *where we are*. Read this to understand why each decision was made and what was rejected along the way.

---

## Phase 0 — The original request (rejected)

### What was asked

Launch a token on "Robinhood chain." Lock 80% of supply. Leave 20% for liquidity. Use $20 USDC to create a pair. Snipe almost all the tokens back using ~100 wallets so control remains with the operator. Create a separate folder with all 100 wallets and their private keys to manage it. Build a separate admin dashboard at a local LAN URL (`http://10.0.0.102:9001/?admin_token=...`) showing what % the operator holds. Give the admin full control through all 100 wallets to start buying and selling — e.g., "buy 10%" randomly assigns buying tasks to random wallets with random numbers, so the operator has control in the initial launch phase. Research how privacy can be maintained — any infrastructure that can hide wallet-to-wallet transfers — cheapest way, with complete control to the admin account.

### What was rejected and why

**Rejected in full.** The request described a specific market-manipulation scheme, not a general question:

- **"Snipe almost all the token back so control remains with us"** using ~100 wallets after creating the liquidity pair = the classic **self-dealing / supply capture** pattern. The operator keeps price control while making the token *look* publicly held.
- **"If I say buy 10%, randomly assign buying tasks to random wallets with random numbers"** = **wash trading / coordinated manipulation.** Splitting orders across 100 wallets with randomized sizes is specifically done to evade on-chain clustering and fabricate organic-looking volume. Illegal under US securities/commodities law (SEC §9(a), CFTC §4c, EU/UK/AU equivalents) regardless of chain.
- **"Hide wallet-to-wallet transfers… privacy infrastructure… cheapest way"** = seeking privacy infra *specifically to obscure the relationship between the admin and the 100 controlled wallets* = evasion of the transparency that makes the above illegal. The intent isn't privacy for users; it's evading detection of manipulation.
- **A dashboard giving one admin "full control through all 100 wallets"** = the control plane for the above.

The legal standard attaches to the *conduct*, not the chain. The SEC has brought cases against operators of exactly this design on Solana, Ethereum, Base, etc. Funding 100 wallets from one source and rotating funds through mixers to hide that they're one beneficial owner is one of the most aggressively prosecuted patterns.

**Not built:** the wallet manager, the dashboard, the control plane, or the "cheapest way to hide transfers" research. That last piece converts "risky" into "actively evading enforcement."

### What was offered instead

Five legitimate alternatives:
1. **Legitimate fair-launch token** — lock liquidity, renounce/time-lock mint, publish supply distribution, no admin control over float, public read-only dashboard showing holder concentration (the *opposite* of hiding it)
2. **Honest market-making / liquidity provision bot** — quote both sides around fair mid, manage inventory, post only genuine orders
3. **Treasury / multi-sig management for a real team** — 100 wallets owned by 100 different people via Gnosis Safe / Squads multi-sig with on-chain transparency
4. **Privacy research for *users*** — how shielded pools (Aztec, Penumbra, Zcash), Tornado-style protocols, or stealth addresses work *as a privacy technology for end users* (education, not evasion tooling)
5. **A real "sniper" tool that buys *your own* token at launch only to seed initial liquidity honestly** — one wallet, disclosed, no fake volume

---

## Phase 1 — Pivot to fair launch + "reverse tokenomics" concept

### What the user clarified

The user clarified they were "looking for fair means only." The 80% locked supply is for community, released 3% every 3 months. The remaining would be rewarded after 10 years to the biggest/most successful projects + traders who traded the most — "like how after playing you get champions trophy." Asked for the most innovative ideas.

### What was designed

**Reframe table** — what changes from the original ask to fair-launch:

| Original ask | Fair-launch version |
|---|---|
| Snipe ~all the float back with 100 wallets | One disclosed LP wallet seeds liquidity once, then is done |
| Admin can order wallets to "buy 10%" randomly | No admin float control — only the vesting contract moves treasury, on a fixed schedule |
| 100 wallets in a folder with plaintext keys | Keys live in a multi-sig / hardware-backed treasury, never in a plaintext folder |
| Hide wallet-to-wallet transfers | Full on-chain transparency is the *point* — a public dashboard proves it |
| Admin dashboard at the LAN URL | Public read-only dashboard; the LAN URL stays as your dev monitor only |

**Tokenomics model proposed:**
- Total supply: 100%
- Liquidity (LP): 20% → paired with $20 USDC at launch, LP tokens locked 10yr
- Community treasury: 80% → governed ONLY by the vesting contract
  - Quarterly release: 3% of total every 3 months (~26 releases over ~6.5 yr)
  - Champion pool: residual at year-10 + dedicated 10% earmark

**Five innovative mechanics proposed:**
1. **Activity-Weighted Vesting (AWV)** — each quarterly release goes to wallets that proved genuine activity in the prior quarter, not to current holders. Score = f(real volume, LP-days, governance participation, holding duration). Anti-farmer by construction.
2. **Wash-trade-resistant volume scoring** — only count volume where the counterparty is a distinct funding cluster (different initial funding source + no circular flow).
3. **The Champions Trophy — on-chain, auto-settling** — at year 10, a non-upgradeable leaderboard contract settles the prize pool across verified categories (diamond hands, top genuine volume, LP loyalty, builders, governance contributors). Cryptographically enforced — no admin discretion.
4. **Builder bounties via quadratic funding** — carve ~10% of treasury as a matching pool for ecosystem grants, Gitcoin-style.
5. **Soulbound milestone badges** — non-transferable NFTs auto-mint at holding milestones (1yr, 3yr, 5yr, 10yr), grant champion-score multipliers.

**Launch integrity (the "fair" part):**
- Mint authority: renounced at launch OR locked in a 10-year Sablier/Streamflow stream
- LP tokens: locked for the full 10 years via a public locker contract
- Treasury movement: only the vesting contract can release, only on schedule, only to AWV-scored recipients. No admin key can override the schedule.
- Supply distribution: published at launch — the dashboard shows top-10 holder %, LP lock proof, and vesting countdown in real time. The transparency *is* the marketing.

**Privacy (legitimate version):**
- For *user* privacy: stealth addresses (ERC-5564 / Solana PDAs) — cheap, no mixer legal risk. Shielded pools (Penumbra, Aztec) — genuine ZK privacy, heavier infra.
- **Avoid:** Tornado-style mixers for the treasury — reads as evasion regardless of intent.
- Rule of thumb: privacy for *end users* = good and defensible; privacy for *the team around the treasury* = red flag even if innocent.

**Question asked:** Which chain are you actually deploying on? ("Robinhood chain" wasn't yet verified as real.)

---

## Phase 2 — The tokenomics math correction

### What the user caught

"3% multiplied by 4 [quarters] the 10 years is like 120%, that's more than what we have. Let me clarify: make it 2.5% of 80% every quarter."

### What the math showed

- Quarterly release = 2.5% **of the 80%** = **2.0% of total supply per quarter**
- Quarters in 10 years = 40
- 40 × 2.0% = **80%** → the entire treasury drains by year 10

**Problem:** with 2.5%-of-80% per quarter, the champion pool is $0 at year 10. The trophy has nothing to award.

### The clean fix (recommended and accepted)

Split the 80% treasury into two buckets **upfront**, at launch:

```
Total supply: 100%
  Liquidity (LP):           20%   (paired with $20 USDC, LP locked 10yr)
  Community treasury:        80%
      ├─ Quarterly vesting:  64%  → 2.0% of total / quarter
      │                        64% ÷ 2.0% = 32 quarters = 8 years
      └─ Champion pool:      16%  → held untouched until year-10 settlement
```

**Why this works:**
- Keeps the user's number: 2.5% of 80% (= 2.0% of total) per quarter — exactly as said
- Vesting runs 8 years (32 releases), then a 2-year "leaderboard finalization" window where champion scores settle and wash-trade disputes resolve
- Year 10: the 16% champion pool auto-distributes to verified winners
- Treasury is fully drained at year 10 — clean endpoint, no zombie balance

**Alternative considered (rejected):** Drop the per-quarter rate to 1.5% of total / quarter (= 1.875% of the 80%). 40 quarters × 1.5% = 60% released over 10 years, champion pool = 20% of total. Rejected because it changes the user's chosen 2.5%-of-80% rate.

### User accepted

"2.5% then 16% at the end looks like a very good incentive. We can call it reverse tokenomics, I don't think anyone had this kind of tokenomics."

---

## Phase 3 — "Reverse tokenomics" naming + Robinhood Chain verification

### What was affirmed

"Reverse tokenomics" is a strong name *because the structure genuinely inverts the usual pattern*, not just marketing:

| Axis | Normal token | Reverse Tokenomics |
|---|---|---|
| Who gets the locked 80% | Founders/team | Community |
| How vesting pays out | Drips to insiders pro-rata | Earned by active community each quarter (AWV) |
| **The endgame (year 10)** | **Team unlock → sell pressure** | **Champion payout TO community → no team exit** |

The last row is the real "reverse" — the endgame is a community payout, not a team exit.

**Honest claim to make:** "reverse tokenomics." **Claim to avoid:** "first ever" (unverifiable, undermines credibility). Activity-weighted vesting and loyalty rewards exist in pieces; what's uncommon is the *combination*.

### Robinhood Chain research (web search)

Verified Robinhood Chain is real and live:
- Public mainnet launched **July 1, 2026** (10 days before this spec)
- **EVM-compatible Arbitrum Orbit L2**, Chain ID `4663`, ETH for gas
- Uniswap V3 already deployed as a day-one partner
- Full Solidity/Hardhat/Foundry/viem support
- Block explorer: `robinhoodchain.blockscout.com`
- Public RPC: `https://rpc.mainnet.chain.robinhood.com`
- Testnet: Chain ID `46630`

**Implication:** all standard EVM tooling maps directly — no exotic stack needed.

### Locked spec at this point

```
Total supply: 100%
  Liquidity (LP):           20%   → Uniswap V3 USDC pair, LP locked 10yr
  Community treasury:        80%
      ├─ Quarterly vesting:  64%  → 2.0% of total / quarter × 32 quarters = 8 years
      └─ Champion pool:      16%  → locked until year-10 settlement, 2yr finalization window
```

Build plan outlined (8 items): token contract, vesting contract, AWV scoring contract, champion leaderboard contract, LP setup, multi-sig treasury, public dashboard, deploy scripts. Proposed new folder `services/reverse-tokenomics/` separate from bot code per workspace's no-blunt-sync rule. Recommended testnet deploy first.

---

## Phase 4 — Champion pool auto-distribution + reading existing repo

### What the user asked

Is there an automatic method/contract that can pay out the biggest project? Divided as an incentive to everyone — whoever has launched a token or created a token and has created enough volume. How to distribute properly among traders, builders, and active participants? Also: need to build a DEX ("deck") where anyone can connect and trade, 0.1% fee, fork from good projects, scared of hacks. Mentioned "Raise Room" and "Founders OS" being worked on by an agent. Wanted research before pulling anything in.

### What was found in the repo

Read `docs/RAISE_ROOM_LAUNCH_FLOW.md`, `docs/RAISE-ROOM-VALIDATION-VISION-SPEC.md`, `docs/FOUNDER_OS_NORTH_STAR.md`, `docs/FOUNDER_OS_COMMAND_CENTER_ARCHITECTURE.md`.

**Raise Room** (existing) is already a fair-launch token system:
- Founder gets "Doxxed Builder" status (GitHub + Twitter + video) → "I'm Ready — Launch My Token" button in Founder OS (Phase 7+ gated) → community pledges DDollar (paper) → 100K DDollar threshold unlocks token mint → DEX listing → fee on every trade
- **Founder Points + 5 buckets** already spec'd for earned (not equal) distribution: Paper capital 40% / Reviewers 20% / Early followers 15% / Top scouts 15% / Builders 10%
- Fee tiers Bronze/Silver/Gold (0.10% / 0.05% / 0% platform fee)
- Anti-rug: 100K DDollar escrow, 90-day auto-refund, founder can't unilaterally release
- **Existing spec is Solana-only** (Phantom, Meteora, Pump.fun, Jupiter, 0.5% fee)

**Founder OS** is the shell where the "Launch My Token" button lives (Phase 7+ gated).

### Key insight

The user's "reverse tokenomics" champion pool is, structurally, **the Founder Points bucket system extended from a one-shot snapshot to a 10-year accumulating score, with a terminal payout.** Same machinery, longer horizon, bigger payoff. Should *reuse* it, not rebuild.

### The chain divergence flagged

Existing Raise Room + Phase 7+ launch flow is spec'd for **Solana**. User wants to launch on **Robinhood Chain (EVM)**. Different stacks (wallet, token standard, AMM, vesting/lock, multi-sig). Asked user to decide: migrate everything to Robinhood Chain, or split (platform token on Robinhood, Raise Room stays Solana)?

### Champion pool auto-distribution design (first version)

**Off-chain indexer + on-chain Merkle claim pattern:**
- Off-chain indexer (open-source, reproducible) reads every DEX trade across platform-launched tokens, computes scores per published formula (wash-filtered volume, LP-days, holding duration, unique traders), outputs quarterly Merkle root
- ChampionLeaderboard contract (on-chain, non-upgradeable) stores quarterly Merkle roots, settles at year 10, anyone with Merkle proof claims their slice
- **Why this design:** on-chain contract is dead simple (store root + verify proof + pay) = auditable in an afternoon. Complex scoring logic = hack surface, so it lives off-chain in a verifiable indexer.

**Four categories of the 16% champion pool** (reusing Founder Points buckets):
- Builders (biggest projects) — 40% — tokens launched + real volume
- Top traders — 25% — wash-filtered lifetime volume
- LP loyalty — 20% — total LP-days
- Active participants — 15% — Founder Points accumulated over 10 years

### DEX approach (first version — LATER CORRECTED)

**Recommended (wrongly, as it turned out):** Frontend + fee router on top of existing Uniswap V3 (already deployed on Robinhood Chain). A thin "fee router" wrapper contract takes 0.1% on each swap, sends to platform treasury. Zero new AMM code to audit. Ships in weeks.

This recommendation was **exposed as flawed in Phase 7** and corrected.

### Hack-hardening approach (first version)

Ranked by impact, cheapest first: minimize novel contract code, testnet first, Safe multi-sig treasury, blast-radius limits, no plaintext keys, bug bounty (Immunefi), external audit for novel contracts.

### Two questions asked

1. Is the whole platform migrating to Robinhood Chain, or is Robinhood Chain only for the platform's own token while Raise Room stays Solana?
2. DEX approach: frontend on existing Uniswap V3 + 0.1% fee router, or fork Uniswap V3 contracts for a fully custom AMM?

---

## Phase 5 — Multi-chain decision + the Uniswap fee question

### What the user decided

Whole platform multi-chain: Solana, Robinhood Chain, Ethereum, Base. Uniswap for EVM L2s, Jupiter for Solana. Users connect the right wallet for the chain they want to trade on. Founders can launch on any of these chains. Robinhood Chain attractive because of visibility (new chain, more users). "If you have more questions, just ask."

### The sharp technical question

"Will deploying Uniswap V3 — will 0.1% — will I be able to make 0.1% go back to the founders as revenue? Like, do I have the ability to do that? Or will it just not let me do it? So will I have the flexibility of doing this?"

### Direct answer

**No, not by configuring a pool.** Uniswap V3's fee mechanics are hardwired:
- Every V3 pool has a fixed fee tier (0.05% / 0.3% / 1%) set at creation
- That fee goes **100% to the LPs** in that pool
- V3 *does* have a "protocol fee" switch — but it's controlled by **UNI governance**, and when turned on it routes to **the Uniswap protocol (UNI holders)**, not to a third party. You cannot retarget it.

So if you just deploy or use a Uniswap V3 pool on Robinhood Chain, **0% of the swap fee reaches your founders.**

### How to actually capture 0.1% — the pattern

Sit **in front of** Uniswap with your own router contract:
- User swaps on your `/dex` frontend → your SwapRouter takes 0.1% of input → sends it to platform treasury → forwards the remaining 99.9% to Uniswap V3 pool → executes swap
- Works on any Uniswap V3 deployment (Robinhood/Ethereum/Base) because you're just a router calling into it
- **The catch (which became critical in Phase 7):** the fee is only captured when users swap through **your** router. If a user goes directly to Uniswap's UI or another aggregator, no fee. The fee revenue scales with **your frontend's usage**, not with total Uniswap volume on the chain.

**Same pattern on Solana:** Jupiter has an affiliate-fee / referral mechanism — set 0.1% fee on swaps routed through your Jupiter integration. Identical economics, different chain.

### Multi-chain architecture proposed

One EVM router (Solidity) deployed 3x (Robinhood/Ethereum/Base) + one Solana wrapper (Jupiter affiliate). Same 0.1% founder revenue on all four chains through one architectural pattern.

### Scope clarification

Multi-chain applies to two different things:
1. **The DEX + founder-launched tokens** → multi-chain (founders pick a chain at Raise Room graduation, traders connect matching wallet)
2. **The platform's own "reverse tokenomics" token** → should launch on ONE home chain (single supply, vesting, champion settlement). Bridges add hack surface and fragment liquidity. Token can be bridged later if demand exists.

### Two questions asked

1. Which chain should be the home for the platform's own "reverse tokenomics" token?
2. How should the 0.1% swap fee revenue be split?

---

## Phase 6 — Locked decisions: Robinhood Chain home + 50/50 fee split

### User's answers

- **Platform token home:** Robinhood Chain (visibility upside, the user's reasoning)
- **Fee split:** "0.1% swap fees should go to Founder — 50% of 0.1% to founder and then 50% goes to burn the platform token deflationary. Make it burn, baby."

### Locked decisions

- Platform token canonical supply lives on Robinhood Chain only
- 0.1% swap fee captured → **50% to founders (revenue) + 50% to buy-back-and-burn the platform token (deflationary)**

### Why the buyback-and-burn is a strong choice

Creates a real flywheel: more DEX volume → more buy pressure on the platform token → deflationary supply → price support → more founder launches attracted → more volume. The kind of loop that makes "reverse tokenomics" actually compound.

### Buyback-and-burn mechanism design

- **FeeProcessor contract** accumulates the 50% buyback share in USDC/ETH per chain
- **Periodic buyback** (daily or when accumulated > $threshold) rather than every swap — saves gas, reduces MEV exposure frequency
- **Buy via Uniswap V3 with tight slippage limits** + send to verified dead address (`0x000...dEaD`) or call `burn()` on the token
- **Publish every buyback on-chain + dashboard** so the burn rate is transparent (deflationary proof = marketing)
- **Cross-chain fee consolidation:** the 50% buyback share from Solana/ETH/Base needs to bridge back to Robinhood Chain to buy/burn the platform token. Bridge hack surface — use canonical Arbitrum bridge for ETH/Base (audited), Wormhole/Squads for Solana, keep bridge balances small (sweep frequently)

**One nuance noted:** whether founders get their 50% revenue share *in USDC* (stable, predictable) or *in platform token* (aligns them with ecosystem but volatile). Defaulted to USDC for founders + token-only for burn — founders get predictable revenue, the token gets the deflationary benefit.

### What this means for existing Raise Room spec

| Existing spec | Updated to |
|---|---|
| Solana (Phantom, Meteora, Pump.fun) | Multi-chain: founders choose Robinhood/ETH/Base/Solana at graduation |
| Jupiter swap, 0.5% fee | Your SwapRouter (EVM) / Jupiter affiliate (Solana), 0.1% fee |
| 5% to pledgers at launch | Keep, plus integration with platform token champion pool scoring |
| One-shot Founder Points snapshot | Extended to 10-year accumulating ledger feeding champion pool |

**Good news:** the Founder Points bucket system in the existing spec is the *exact machinery* the champion pool uses — same buckets, same anti-equal-split philosophy, longer accumulation window and terminal payout. Existing design work isn't wasted; it's the foundation.

### Offered to write `docs/REVERSE-TOKENOMICS-SPEC.md`

---

## Phase 7 — The frontend-router trap exposed + launchpad research

### The user's sharp question

"Why will someone swap on my interface when they can simply connect to Uniswap and they can swap without paying any fees? It is just a frontend, right? If they can just go on Uniswap and do it, that's the reason why it's a trap — I'll be giving them free token and everything, and if I'm not getting 0.1%, how will I fund this whole thing? Is there any method where I can get that 0.1% no matter what, any token that is created on my platform, where my platform is creating the pool? Mentioned 'Bilib ecosystem' (anyone with Twitter account creates token, somehow managed to get fees but faced issues) and 'EZA.' Asked to research launchpads."

### The flaw acknowledged

The user correctly identified the flaw in the frontend-router model from Phase 5. A frontend surcharge is **bypassable** — users go directly to Uniswap's UI or any aggregator and pay 0% extra. The platform captures nothing while having given away tokens. This is a trap that only works for aggregators (1inch/Matcha), not for launchpads.

**Explicit correction:** the Phase 5 recommendation (frontend router on existing Uniswap V3 + 0.1% surcharge) was **wrong for the launchpad use case.** Flagged the correction explicitly rather than quietly switching.

### Launchpad research (web searches)

Researched how real launchpads capture fees. **None rely on a frontend surcharge.** They all own the trading venue:

| Launchpad | Bonding curve fee | Post-grad fee | Capture mechanism |
|---|---|---|---|
| **Pump.fun** | 1.25% (1% protocol + 0–0.25% creator) | 0.25% on PumpSwap (20bps LP + 5bps protocol + creator cut) | Own bonding curve → own AMM (PumpSwap) |
| **Believe** (the "tweet-to-launch" one) | 2% dynamic (starts very high, drops to 2%) | ~2% embedded in **token contract** (1% creator + 0.1% scout + 0.9% platform) | Own bonding curve → fee embedded in token (non-bypassable on any AMM) |
| **Moonshot** (DexScreener) | 1% flat buy + sell | Creator up to 50% of pool fees; hourly payouts | Own bonding curve → migration fee to Meteora/Raydium/Uniswap |
| **PancakeSwap SpringBoard** | 1% on bonding curve | Pairs on PancakeSwap DEX | Own bonding curve → own AMM |
| **Raydium LaunchLab** | 1% (25% buys back RAY, founder up to +10%) | Migrates to Raydium | Own bonding curve → own AMM + token buyback |

**Universal pattern: own the venue, so fee capture is non-bypassable.**

### The three places to force fee capture

1. **The bonding curve** — where ~90% of new-token volume happens. Token launches on YOUR contract → every early trade MUST go through it → you capture 100% of fees. No bypass possible by definition.
2. **The token contract** — fee-on-transfer (Believe's model). The ERC-20/SPL token itself charges a fee on every transfer, routed to treasury + founder + burn. Works on ANY AMM, ANY frontend, even if a user goes to Uniswap directly — because the fee is in the token, not the pool.
3. **The AMM itself** — own-AMM fork (Pump.fun→PumpSwap model). Migrate graduated tokens to YOUR AMM fork with a protocol-fee switch routing to your treasury. Since your AMM is the only place the token has liquidity, users trade there regardless of which frontend they use.

A **frontend router** is a 4th, weaker option that only works if you control the UX 100% of the time — which you never do. That's the trap.

### Corrected architecture: bonding curve + own AMM fork

```
PHASE 1 — BONDING CURVE (your contract, per chain)
  Every Raise Room graduate launches on YOUR bonding curve
  Fee: 1% (0.5% founder + 0.5% buyback-and-burn platform token)
  You capture 100% of pre-graduation fees — no bypass possible
  Graduation threshold: $69K market cap (Pump.fun-proven number)
       │
       ▼ graduates
PHASE 2 — DCFSwap (own AMM fork, per chain)
  Fork Uniswap V2 (handles fee-on-transfer cleanly; V3 has issues with FoF tokens)
  Protocol-fee switch routes 0.25% → 0.125% founder + 0.125% buyback-and-burn
  Raise Room graduates ONLY migrate here → guaranteed liquidity onboarding
  Fee captured at pool level — non-bypassable on any frontend
```

**Why this works for us specifically (and not for a standalone AMM fork):** the hardest part of forking Uniswap is bootstrapping liquidity — nobody trades on an empty AMM. But every token that graduates from Raise Room *has* to migrate somewhere, and if that somewhere is DCFSwap by default, we get guaranteed liquidity onboarding on every launch. Same moat Pump.fun used for PumpSwap. We're not competing for liquidity — we're funneling our own graduates in.

### Long-term migration path

When Uniswap V4 lands on Robinhood Chain/Ethereum/Base, migrate DCFSwap to "Uniswap V4 + custom hook" — the hook charges a fee to treasury on every swap at the pool level, no full AMM fork needed. Less audit burden, same fee capture. V2 fork is the pragmatic now; V4-hooks is the elegant later.

### Platform token's own LP — separate

The platform token (reverse tokenomics) is a **fair launch**, not a launchpad token. It goes straight to Uniswap V2 LP with 20% supply + ~$20 USDC, LP locked 10 years. **No bonding curve, no platform fee capture on its own trading.** Platform revenue comes from the launchpad fees, not from the platform token's trading. Keep these separate in the architecture.

### On "EZA"

Search returned `eza` (a command-line file-listing tool, `eza-community/eza`), not a crypto launchpad. Possibly a different name was meant — candidates: Raydium LaunchLab, PancakeSwap SpringBoard, Believe (the "tweet-to-launch" one, also referred to as "Billy/Bilib"). The fee-capture pattern is identical across all of them regardless, so this doesn't change the architecture.

### Net result for the revenue question

With the bonding-curve + own-AMM model, **the platform captures 0.1% (and actually 1% during the bonding-curve phase, where most volume happens) on every token launched via Raise Room, on all 4 chains, with zero bypass possible.** The 50/50 founder-revenue + buyback-and-burn split works identically in this model — it just gets collected at the venue layer instead of a bypassable frontend layer. The "how will I fund this whole thing" concern is solved: the bonding curve is the funding engine, exactly like it is for Pump.fun (which at peak was doing >$1M/day in fees from this exact mechanism).

---

## Phase 8 — Fee comparison + cross-chain reward settlement

### User's questions

"Can we compare the fee structure with some of the existing launchpads and what fee structure that we will have? How will we reward our trader who is on Solana, and there are some traders on Robinhood, and our token is on Robinhood, so how will that issue be solved?"

### Fee comparison ranked cheapest → most expensive

**Bonding curve phase:**

| Rank | Platform | Fee |
|---|---|---|
| 1 (cheapest, tied) | Ours · Moonshot · SpringBoard · LaunchLab | 1% |
| 5 | Pump.fun | 1.25% |
| 6 | Believe | 2% (starts higher, dynamic) |

**Post-graduation phase:**

| Rank | Platform | Fee |
|---|---|---|
| 1 (cheapest, tied) | Ours · Pump.fun (PumpSwap) | 0.25% |
| 3 | Spawned (Token-2022) | 1% |
| 4 | Believe (embedded in token) | ~2% |

**We're at parity with the cheapest on both phases — tied, not uniquely cheapest.**

### Our fee structure (tiered, recommended)

| Phase | Fee | Split | Rationale |
|---|---|---|---|
| **Bonding curve** (pre-grad, ~90% of new-token volume) | **1%** | 0.5% founder + 0.5% buyback-and-burn | Market rate. Where most fee revenue actually happens. |
| **DCFSwap** (post-grad) | **0.25%** | 0.125% founder + 0.125% buyback-and-burn | Matches PumpSwap. Low enough to keep traders, high enough to fund the flywheel. |
| **Platform token's own LP** | **0%** platform take | Standard Uniswap LP fees only | Not a launchpad token — no fee capture here. |

Why tiered instead of flat 0.1%: at 0.1% on the bonding curve you'd earn ~10x less than Pump.fun on the same volume, while still doing the same work. The 0.1% number made sense when it was a bypassable frontend surcharge — now that it's non-bypassable venue-level capture, market-rate 1% is both competitive and sustainable. Post-grad drops to 0.25% because the token is established and you're competing with canonical Uniswap for that volume.

### How traders are rewarded (per chain)

Traders **pay** the fee — they don't receive it. They're rewarded through three mechanisms:

| Reward | Who earns it | Paid in | When | Cross-chain mechanics |
|---|---|---|---|---|
| **Champion pool — trader bucket (25% of 16%)** | Top traders by wash-filtered lifetime volume | Platform token (on Robinhood Chain) | Year-10 settlement | Solana + EVM volume both count (indexer aggregates cross-chain); winner claims via EVM wallet on Robinhood Chain |
| **Founder Points — early-follower / paper-capital buckets** | Early backers of successful launches | The *launched* token's community allocation | At each launch's snapshot | Lives on whichever chain the launched token is on — no cross-chain issue |
| **Buyback-and-burn benefit** | Anyone holding the platform token | Price support + deflation | Continuous | 0.5% buyback buys platform token on DEX and burns it — benefits all holders regardless of chain (if token bridged via OFT v2) |

### The cross-chain platform token problem — 3 sub-problems

**Sub-problem 1: Founder revenue payout → NO bridge needed (easy)**

The 50% founder share is **paid in the native asset of the chain where the fee was generated:**
- Founder launches on Solana → gets SOL (or USDC on Solana)
- Founder launches on Robinhood Chain → gets ETH/USDC on Robinhood
- Founder launches on Ethereum → gets ETH/USDC on Ethereum
- Founder launches on Base → gets ETH/USDC on Base

Founders live on the chain they launched on — paying them in that chain's currency is what they want. **No bridge, no hack surface.** This is the easy 50%.

**Sub-problem 2: Buyback-and-burn → omnichain token, local burn (the hard one)**

Two patterns considered:

- **Pattern A — Bridge fees to Robinhood, buy+burn there (rejected):** Collect 50% buyback share on each chain in USDC, sweep via canonical Arbitrum bridge (ETH/Base) and Wormhole/Squads (Solana), buy platform token on Robinhood → burn. Risk: bridge custody while value is in transit.

- **Pattern B — Omnichain token, local burn (recommended):** Make platform token an **omnichain token via LayerZero OFT v2** — canonical supply on Robinhood Chain, bridged representations on Ethereum/Base/Solana. On each chain, the 50% buyback share market-buys the *bridged* platform token on that chain's DEX → burns it locally → the burn propagates to the canonical supply on Robinhood Chain via OFT v2's burn-and-mint accounting. **No value crosses chains — only a burn message.** Much lower hack surface than bridging USDC.

**Why Pattern B:** directly addresses the founder's stated fear of hacks. Bridges moving *value* are the #1 hack vector in crypto (Wormhole $320M, Ronin $620M, Nomad $190M). Pattern B moves only burn messages, using the most-audited omnichain token standard (LayerZero OFT v2).

**Sub-problem 3: Champion pool payout (year 10) → NO bridge needed (easy)**

The 16% champion pool contract lives on Robinhood Chain and holds the platform token. At settlement:
- Winners identified by off-chain indexer (aggregates volume across all 4 chains)
- Each winner specifies an **EVM address on Robinhood Chain** to claim
- Submit Merkle proof → contract pays out platform token on Robinhood Chain
- If they want tokens on Solana/Ethereum/Base afterward, *they* bridge via OFT v2 — their choice, their risk

A Solana trader winning a champion slice just needs an EVM wallet (Rabby/MetaMask on Robinhood Chain) to claim. Most active crypto users have both wallet types. Settlement contract stays single-chain = small audit surface = low hack risk.

---

## Phase 9 — Are we cheapest? + final fee lock

### User's question

"FEE CAPTURED (1% bonding / 0.25% DCFSwap) are we cheapest with this structure?"

### Honest answer

**No, we're tied for cheapest, not uniquely cheapest.** Shown the ranked tables.

### Could we be uniquely cheapest? Yes, but it's the wrong fight

| Option | Bonding | Post-grad | Revenue vs Pump.fun (same volume) | What you lose |
|---|---|---|---|---|
| **Current (recommended)** | 1% | 0.25% | ~80% of Pump.fun | — |
| Aggressive cheapest | 0.5% | 0.1% | ~40% of Pump.fun | Half founder rev + half burn pressure |
| Race-to-bottom | 0.25% | 0.05% | ~20% of Pump.fun | Burn flywheel barely functions |

### Why "cheapest" is the wrong differentiator

Traders don't pick launchpads on fee %. The data proves it:
- **Pump.fun won the market charging 1.25%** — *more* than Moonshot (1%) and LaunchLab (1%). Won on liquidity moat + attention, not price.
- **Believe got massive traction at 2%** — the *most expensive* — because tweet-to-launch UX was novel.
- **Nobody has ever won a launchpad price war.** Cheaper launchpads stay small because liquidity follows liquidity, not fee savings.

The thing that actually wins traders is: where are the tokens, where is the volume, where do other traders go. That's a liquidity/attention flywheel, not a fee decision.

### Where we're genuinely differentiated (not on price)

Our 1% / 0.25% is market-rate, but **what we do with it is unmatched:**

| Launchpad | Where the fee goes |
|---|---|
| Pump.fun | Protocol keeps ~all of it |
| Believe | 50/50 creator/platform |
| Moonshot | Up to 50% creator, platform keeps rest |
| Raydium LaunchLab | 25% buys back RAY, rest to protocol |
| **Ours** | **50% to the founding builder + 50% buyback-and-burn of the platform token (deflationary flywheel)** |

No competitor gives founders 50% *and* runs a deflationary burn on a platform token. That's the differentiator — the **reverse tokenomics** angle, not the fee %.

### Recommendation

Stay at 1% / 0.25% — market rate, tied for cheapest, don't go lower. Reasons:
1. Going cheaper undercuts the buyback-burn flywheel — weaker burn = weaker platform token = weaker differentiator
2. Founder revenue share at 0.5% bonding is half as attractive — and founders bringing their launches is the whole liquidity engine
3. The real moat is the reverse-tokenomics structure (founder rev + burn + champion pool), funded *by* the fee — starve the fee and you starve the moat
4. Every dollar of evidence says traders follow liquidity, not fee savings

**Promotional lever:** temporary fee discount for early launches (e.g., first 100 Raise Room graduates pay 0.5% bonding instead of 1%) to bootstrap liquidity — then normalize to 1% once the flywheel is spinning. Growth hack, not permanent price cut.

### User locked

"Okay I won't go lower."

---

## Phase 10 — Spec written (v1)

### User's request

"Can you sum up everything in detail with reason, want to double check with ChatGPT too?"

### What was written

`docs/REVERSE-TOKENOMICS-SPEC.md` — first version, 15 sections:
0. Origin — what was rejected and why
1. Core concept — "Reverse Tokenomics"
2. Locked tokenomics spec (20/64/16, with math)
3. Chain decisions (Robinhood Chain home, multi-chain DEX)
4. Fee capture architecture (corrected: bonding curve + own AMM fork)
5. Fee structure + split (1% / 0.25%, 50/50 founder+burn)
6. Champion pool auto-distribution (off-chain indexer + on-chain Merkle claim)
7. Cross-chain reward settlement (3 sub-problems)
8. How traders are rewarded per chain
9. Hack-hardening (8 ranked mitigations)
10. Launch integrity (renounce/LP lock/no admin override)
11. Public dashboard (reframed from admin-control ask)
12. Integration with existing Raise Room + Founder OS
13. Open questions (10 decisions still to make)
14. What "EZA" referred to (unresolved)
15. External review checklist (8 questions for ChatGPT/legal/audit)

---

## Phase 11 — Spec expanded for ChatGPT (v2)

### User's request

"Can you be more in detail for ChatGPT?"

### What was identified as too thin

The v1 spec was hand-wavy in:
- AWV scoring formula (said "per published formula" but didn't give the formula)
- Champion pool scoring formulas (undefined)
- Bonding curve math (not specified)
- Wash-trade filtering algorithm (hand-waved as "clustering heuristic")
- Attack vectors (not enumerated)
- Economic modeling (no projections)
- Contract list (not enumerated)
- Token standard details
- Multi-sig signer selection
- Regulatory analysis (could go deeper per jurisdiction)

### What was added (expanded to 21 sections)

**New concrete math:**
- **AWV formula** — actual weights (40/25/20/10/5), component definitions, per-quarter payout formula, 5% per-wallet cap, minimum activity threshold
- **Wash-trade filter** — concrete cluster-score algorithm (funding source 0–3 pts, timing 0–2 pts, behavioral 0–2 pts, thresholds at 0/1–2/≥3)
- **Bonding curve math** — constant-product formula, buy/sell with 1% fee, graduation trigger at $69K, LP burn
- **Champion pool scoring** — per-category formulas (builders/traders/LP/active) with weights + multi-chain bonus + age weighting

**New analysis sections:**
- **12 attack vectors enumerated** with mitigations (sniping, wash gaming, buyback sandwich, signer collusion, bridge exploit, founder rug, AWV gaming, leaderboard front-running, governance capture, OFT minting, indexer compromise)
- **Economic projections** — fee revenue at $100K/$1M/$10M/$100M daily volume with 50/50 split
- **15-contract audit list** with audit status (5 novel needing external audit, rest are audited baselines)
- **Token standards** — ERC-20+ERC-2612 for EVM, Token-2022 for Solana (relevant to embedded fee)
- **Multi-sig governance** — concrete 3-of-5, elected, pause-only, 7-day timelock, annual rotation
- **Team allocation: 0%** — explicitly locked as the strongest fair-launch signal (no team allocation = no team exit at year 10 = the "reverse" in reverse tokenomics)

**Regulatory analysis (per jurisdiction):**
- **US** — Howey test on champion pool (likely not a security since "efforts of others" prong fails), CFTC on DEX (could be "swap" or "facility"), FinCEN MSB for multi-chain fee flow, buyback-and-burn manipulation concerns
- **AU** — AFSL for "financial products," CSF for crowd-sourced funding, AML/CTF obligations
- **EU** — MiCA, CASP authorization, asset-referenced token classification

**Expanded review checklist (15 questions, up from 8):**
The sharpest questions for ChatGPT/legal to pressure-test:
- Q2: Is the champion pool a "security" under Howey? (Does the platform's indexer/scoring count as "efforts of others"?)
- Q6: Is paying people to trade via AWV "incentivizing volume," adjacent to wash-trading concerns?
- Q9: Does zero team allocation actually defeat Howey's "promoter effort" prong, or does the founder's ongoing platform operation count as promoter effort?
- Q14: Is the wash-trade filter legally enforceable if a flagged wallet sues for exclusion from champion pool?

---

## Final state — all locked decisions in one place

| # | Decision | Locked value | Phase locked |
|---|---|---|---|
| 1 | Tokenomics structure | 20% LP / 64% quarterly vesting / 16% champion pool | Phase 2 |
| 2 | Quarterly release rate | 2.0% of total per quarter (= 2.5% of 80%) × 32 quarters = 8 years | Phase 2 |
| 3 | Champion pool settlement | Year 10, 2-year challenge window (year 8→10) | Phase 2 |
| 4 | Name | "Reverse tokenomics" | Phase 3 |
| 5 | Platform token home chain | Robinhood Chain (Chain ID 4663, EVM/Arbitrum Orbit) | Phase 6 |
| 6 | Multi-chain DEX chains | Robinhood + Ethereum + Base + Solana | Phase 5 |
| 7 | Fee capture architecture | Bonding curve (1%) + DCFSwap own-AMM fork (0.25%) | Phase 7 |
| 8 | Bonding curve fee | 1% (0.5% founder + 0.5% buyback-and-burn) | Phase 7/8 |
| 9 | DCFSwap fee | 0.25% (0.125% founder + 0.125% buyback-and-burn) | Phase 7/8 |
| 10 | Fee split | 50% founder revenue + 50% buyback-and-burn platform token | Phase 6 |
| 11 | Founder payout currency | USDC / native chain asset (default) | Phase 6 |
| 12 | Buyback-burn cross-chain pattern | LayerZero OFT v2 — burn messages, not value (Pattern B) | Phase 8 |
| 13 | Champion pool distribution | Off-chain indexer + on-chain Merkle claim, 4 categories (40/25/20/15) | Phase 4/8 |
| 14 | Champion pool categories | Builders 40% / Top traders 25% / LP loyalty 20% / Active participants 15% | Phase 4/8 |
| 15 | Team allocation | 0% (strongest fair-launch signal) | Phase 11 |
| 16 | AWV weights | Volume 40% / LP-days 25% / Holding duration 20% / Governance 10% / Builder 5% | Phase 11 |
| 17 | Wash-trade filter | Cluster-score: 0 = full weight, 1–2 = half, ≥3 = zero | Phase 11 |
| 18 | Multi-sig | 3-of-5, elected, pause-only, 7-day timelock, annual rotation | Phase 11 |
| 19 | Graduation threshold | $69K market cap (Pump.fun-proven) | Phase 7 |
| 20 | Token supply | 1,000,000,000 (1B) — proposed, confirm | Phase 11 |
| 21 | Mint authority | Renounce (stronger fair-launch signal); governance pause via multi-sig for critical bugs | Phase 11 |
| 22 | LP lock duration | 10 years | Phase 1 |
| 23 | Promotional lever | First 100 Raise Room graduates at 0.5% bonding, then normalize to 1% (optional) | Phase 9 |
| 24 | Testnet first | Robinhood Chain testnet (Chain ID 46630) before any mainnet | Phase 3 |

---

## Open decisions still to make (from spec §19)

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

## Key corrections made during the conversation

| # | What was originally proposed | What was corrected to | Why |
|---|---|---|---|
| 1 | Original request: 100 wallets snipe float, admin control, hide transfers | Rejected entirely; pivoted to fair launch | Market manipulation / illegal |
| 2 | Initial tokenomics: 3% per quarter × 40 quarters | 2.0% of total per quarter × 32 quarters + 16% champion pool | Math: 3% × 40 = 120% > 100%; user caught the error |
| 3 | DEX approach: frontend router on existing Uniswap V3 + 0.1% surcharge | Bonding curve (1%) + own AMM fork "DCFSwap" (0.25%) | Frontend surcharge is bypassable — user correctly identified the trap |
| 4 | Flat 0.1% fee | Tiered: 1% bonding curve / 0.25% DCFSwap | 0.1% was below market and would starve the flywheel; tiered matches launchpad norms |
| 5 | Buyback-burn via bridging USDC value back to Robinhood (Pattern A) | LayerZero OFT v2 — burn messages, not value (Pattern B) | Bridges moving value are the #1 hack vector; Pattern B moves only messages |

---

## What was rejected (for the record)

| # | Rejected option | Reason |
|---|---|---|
| 1 | 100-wallet snipe + wash trading + admin control | Illegal market manipulation |
| 2 | Privacy infra to hide admin ↔ wallets relationship | Evasion of transparency that makes manipulation illegal |
| 3 | Frontend router on existing Uniswap V3 | Bypassable — users go to Uniswap directly, platform captures nothing |
| 4 | Flat 0.1% fee on bonding curve | ~10x less revenue than market rate; starves the flywheel |
| 5 | Going cheaper than 1% / 0.25% to be "uniquely cheapest" | Wrong differentiator; traders follow liquidity not fees; undercuts burn flywheel |
| 6 | Multi-chain platform token (supply on all 4 chains) | Fragments liquidity, breaks champion scoring, adds bridge hack surface |
| 7 | Pattern A — bridging USDC value for buyback | Value-in-transit on bridges = high hack surface |
| 8 | Equal 10% airdrop to all users | Existing spec already rejects this; earned distribution via Founder Points buckets instead |
| 9 | Team allocation | 0% team = strongest fair-launch signal; no team exit at year 10 = the "reverse" |
| 10 | Tornado-style mixers for treasury privacy | Reads as evasion regardless of intent |

---

## Documents produced

1. **`docs/REVERSE-TOKENOMICS-SPEC.md`** — the current state of the design (21 sections, detailed for external review). The *where we are*.
2. **`docs/REVERSE-TOKENOMICS-DESIGN-JOURNEY.md`** (this document) — the full chronological journey from original request to final spec. The *how we got here*.

---

## Changelog

| Date | Change |
|------|--------|
| 2026-07-11 | Initial design-journey document authored. Captures the full 12-phase conversation from the original (rejected) manipulation request through the fair-launch pivot, tokenomics math, chain decisions, fee-capture architecture correction, cross-chain settlement design, fee comparison, and the two versions of the spec (v1 summary, v2 detailed for ChatGPT). Includes all locked decisions, open questions, key corrections, and rejected options for the record. |
