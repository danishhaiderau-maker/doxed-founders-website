# Anti-Abuse Strategy — Founder OS Free AI vs. Raise Room Revenue

| Field | Value |
|-------|-------|
| **Status** | DRAFT — research synthesis + recommended strategy |
| **Authored** | 2026-07-10 |
| **Owners** | Head of Product · Anti-Abuse · Founder |
| **Pairs with** | [`DDOLLAR_ECONOMY.md`](./DDOLLAR_ECONOMY.md) (reputation/staking), [`RAISE_ROOM_LAUNCH_FLOW.md`](./RAISE_ROOM_LAUNCH_FLOW.md) (DEX fees), [`FOUNDER_OS_NORTH_STAR.md`](./FOUNDER_OS_NORTH_STAR.md) (lock-in surface), [`API-ABUSE-AUDIT.md`](./API-ABUSE-AUDIT.md) (cost abuse) |
| **Reads against code** | `packages/utils/src/ai-proxy.ts`, `packages/utils/src/reputation-points.ts`, Raise Room DEX fee path |

---

## TL;DR (read this first)

- **Can we technically prevent a founder from copying AI-generated code and launching elsewhere?** **No.** Once code is rendered to their screen, it is on their machine. Every platform studied (Replit, Bolt, Lovable, v0, GitHub Copilot) accepts this and explicitly assigns output ownership to the user. There is no cryptographic or DRM-like way to recall code that has been seen.
- **Can we detect it after the fact?** **Yes, partially.** Token launches on Solana (pump.fun) and EVM chains are fully observable in real time via gRPC/RPC streams. Name/symbol/metadata matching against our project registry is feasible and cheap. Code watermarking exists in research but is fragile against trivial refactors — treat it as a weak signal, not enforcement.
- **The #1 most effective strategy is not prevention or detection — it is positive lock-in (the sticky approach).** Make staying obviously better than leaving: Memory Engine, DDollar reputation, community/pledgers, and deepest Raise Room liquidity. This is what YC, Replit, and every successful platform actually relies on.
- **Contractual clauses (ToS, doxxing agreement, liquidated damages) are worth having for posture and the few high-value cases, but are weak as a primary defense** — they are jurisdiction-dependent, costly to enforce, and the lost DEX fees rarely justify litigation.
- **Recommended balance:** ~70% of effort into stickiness/lock-in, ~20% into detection/monitoring (cheap and scalable), ~10% into contractual posture (write it once, enforce rarely).

---

## 1. The threat model — what exactly are we preventing?

### The core attack

1. A doxxed, verified founder signs up and receives **free AI compute** (GLM 5.2, DeepSeek V4 — cost to us ≈ **$8/founder/month**).
2. Over weeks/months they use the Founder Brain / Builder to generate a full project: smart contracts, frontend, pitch, tokenomics.
3. At launch time, instead of routing the token through the **Raise Room** (we earn **0.1% DEX fees**), they deploy directly to **pump.fun, Raydium, PumpSwap, or another DEX** and we earn **$0**.
4. They keep their DDollar/reputation optional — the sunk cost to them is near zero because the AI was free.

### What we are NOT trying to prevent

- Founders who *genuinely* leave the platform because the project failed. That's churn, not abuse.
- Founders who use a *competitor's* AI tool and launch on our Raise Room. That's a win for us.
- Copying our AI prompts or product ideas. That is a different (harder, less valuable) threat.

### Quantifying the loss per defection

| Variable | Assumption |
|----------|-----------|
| AI cost sunk per defecting founder (avg 3 months) | $24 |
| Raise Room DEX fee | 0.1% of launch + ongoing volume |
| Typical launch raise | $25k–$500k |
| Typical 30-day post-launch volume | $1M–$20M |
| **Lost DEX fees per defection (mid-range)** | **~$1,000–$15,000+** |

So a single defection can cost us 40x–600x the AI spend we sunk into that founder. This is why the threat matters — but also why the response must be **proportional and scalable**, not litigation-heavy.

---

## 2. Can we technically prevent it? (the honest answer)

### **No — not at the code layer.**

This is settled across the industry. Once the model emits tokens to the founder's session, those tokens are:

- Visible in the browser DOM
- Copyable to clipboard
- Exportable via GitHub sync, copy-paste, screenshot, or simply retyping

There is **no DRM for source code** that survives a determined user. Even fully-proprietary IDEs (Replit Agent, Bolt's WebContainer) acknowledge this: their "lock-in" is *infrastructural* (their DB, their deploy, their secrets manager), not *code-portability*. The moment a user pushes to a standard GitHub repo, the code is portable.

### What IS technically controllable

| Asset | Portable? | Lock-in lever |
|-------|-----------|---------------|
| **Generated source code** | ✅ Fully | None — accept it |
| **Project context / Memory Engine** | ❌ Stays with us | High — switching = starting over |
| **DDollar + reputation history** | ❌ Stays with us | High — non-transferable |
| **Community / pledgers / followers** | ⚠️ Partially | High — relationships are platform-bound |
| **Raise Room liquidity & fee tier** | ❌ Ours only | High — they get our deepest book |
| **AI session/credits** | ❌ Revocable | Medium — can cut off, but sunk cost is zero |

**The strategic implication is decisive:** stop trying to lock the code, and instead lock the **assets that are genuinely non-portable.** Section 6 develops this.

---

## 3. How other platforms handle it

### AI app builders (Replit, Bolt.new, Lovable, v0) — code portability

2026 benchmarks are unanimous: **all four generate real, exportable code, and users own the output.**

| Platform | Portability (1=locked, 5=free) | Where the stickiness actually is |
|----------|-------------------------------|----------------------------------|
| **Lovable** | 5 — standard React+Vite+Supabase, two-way GitHub sync | Supabase Edge Functions backend is annoying to re-platform |
| **Replit Agent** | 3 — code portable, infra is not | Replit DB, secrets, deploy pipeline, the Agent loop itself |
| **Bolt.new** | 2.5 — WebContainer quirks | Browser-native runtime + Bolt Cloud coupling |
| **v0** | Vercel-aligned | Tightly coupled to the Vercel deploy/orbit |

Key takeaways for us:
- **None of them try to prevent code export.** It would be product suicide — founders flee from no-code black boxes.
- Their lock-in is **infrastructural gravity** (hosting, DB, deploy) and **workflow gravity** (the Agent loop, the preview, the iteration speed). The code is *free to leave*; the *experience* is not.
- Lovable wins on portability and still retains users, because **the speed of staying beats the cost of migrating.** This is the model we should copy.

### GitHub Copilot — output ownership

GitHub's terms are explicit: **"GitHub does not claim ownership of your Input or Output. You retain ownership of Your Code."** Users can generate code with Copilot and walk away the same day. Copilot's retention comes from being embedded in the developer's daily workflow (IDE integration, GitHub itself), not from any portability lock.

The relevant precedent: **no major AI coding tool asserts ongoing claims over generated output.** A ToS clause saying "code generated here must launch here" would be a market anomaly and would deter adoption.

### YC — the incubator model (closest analog to us)

YC is the most directly relevant comparison because, like us, they give away something valuable (cash + advice + brand) and monetize the *outcome* (equity in successful companies).

- **No legal exit penalty.** There is no clause forcing a company to stay. The SAFE investment converts to equity regardless.
- **What actually prevents leaving:**
  1. **Equity (7% standard)** — YC already owns a piece, so even if you "leave," they ride along. *We do not have this lever directly, but DDollar + future token allocation is a softer analog.*
  2. **Network effects** — Bookface (alumni forum), Demo Day investor access, partner intros. Leaving = losing the network.
  3. **Brand association** — "YC company" is a signal that opens doors. Defectors lose the label.
  4. **Ongoing support** — office hours, group partners, the batch structure. Access stops if you leave.
- **The enforcement mechanism that DOES exist** is a **founder ethics policy** + community norms. YC can remove founders from the forum for violating norms (e.g., leaking Bookface content). They "generally" return shares in serious cases, but the *reputational cost* is the real deterrent, not the legal one.

**The Founder OS equivalent of YC's model:** verified-founder status, DDollar reputation, the pledger community, Raise Room liquidity, and the Memory Engine are our "equity + network + brand + support." We do not need a legal lock; we need the leaving cost to be obviously higher than the staying cost.

### Launchpad economics (PinkSale, BSCPad, pump.fun)

Competitor launchpads do not give away free AI — they monetize the launch directly:
- **Listing/presale fees:** 2–5% of raise (PinkSale, BSCPad)
- **DEX swap commissions** on integrated AMMs (PinkSwap)
- **Staking-gated participation** (must hold/stake native token to access sales — this is *our* DDollar tier analog)
- **Liquidity locking services** (PinkLock)
- **Creator fee sharing** routes ongoing volume back to founders (pump.fun-style) — *this is a stickiness feature we should consider*

The lesson: launchpads that succeed build a **fee-and-stake ecosystem** where every action reinforces staying. None rely on preventing users from "taking their idea elsewhere."

---

## 4. Technical approaches

### 4.1 Code watermarking — real but fragile

**State of the art (2026):**
- **STONE** (EACL 2026) — syntax-aware watermarking that embeds only in non-syntactic tokens, preserving correctness across Python/C++/Java.
- **RoSeMary** — ML/crypto co-design with zero-knowledge proof verification, so ownership can be proven without revealing the signature.
- **MATRIX** — multi-layer dual-channel watermarking, claims 99.2% detection accuracy with <0.14% functionality loss.
- **SWaRL** — RL-based, uses compiler feedback to guarantee the watermark doesn't break the build.

**The robustness problem (decisive):**
Research is equally clear that **watermarks are trivially erased.** The arXiv study *Is the Watermarking of LLM-Generated Code Robust?* shows that **simple semantic-preserving transformations — variable renaming, dead-code insertion, AST-level rewrites — drop true-positive detection below 50%.** Any founder who runs their code through `prettier`, an auto-refactor, or just asks a second LLM to "clean this up" will strip the watermark.

**Our verdict:** watermarking is a **weak, supplementary signal**. Useful as one input to a detection pipeline (see 4.3), useless as enforcement. Do not over-invest here. If implemented, use it only to *increase suspicion* on a flagged launch, never as the sole trigger.

### 4.2 License/contract clauses — see Section 5

### 4.3 Smart-contract / token-launch monitoring — cheap and high-signal

This is the **most technically mature detection tool we have**, because token launches are public, on-chain, and streamable in real time.

**Pump.fun / Solana (the primary defection target):**
- Subscribe to the pump.fun program ID `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` via **Yellowstone gRPC** (Helius, Triton, Shyft, Chainstack all offer this).
- Filter for the `create` / `create_v2` instruction discriminator (first 8 bytes).
- Decode the instruction to extract **name, symbol, mint address, creator wallet, URI**.
- Latency: **milliseconds** after the tx is confirmed — faster than any UI.

**EVM chains (Ethereum, Base, BSC):**
- Subscribe to standard ERC-20/ERC-7765 `Transfer`/`Deploy` events, or use Dune / Transpose / Alchemy webhooks for new-pair creation on Uniswap/PancakeSwap.

**Matching layer (the actual work):**
For each detected launch, run a fuzzy match against our **project registry** (project names, token symbols, descriptions, founder wallet history, declared tokenomics). Score on:
- Exact/normalized name or symbol collision
- Creator wallet linked to a verified founder
- Description/URI similarity (TF-IDF or embedding cosine)
- Tokenomics match (supply, decimals, tax config) flagged in their Founder OS project

**A high-score match → flag for review**, not auto-punishment. False positives (two unrelated projects named "AI" or "PEPE") will be common and must be human-triaged. This is the post-hoc detection loop described in Section 7.

### 4.4 Gradual resource unlock — the YC-equivalent of "earning your equity"

Instead of giving every founder full AI from day one, gate depth by commitment:

| Tier | Unlock trigger | What they get |
|------|---------------|---------------|
| **T0 Scout** | Verified email | Browse-only, 5 AI messages/day |
| **T1 Builder** | Doxxed + project created | Full Founder Brain, capped daily tokens |
| **T2 Committed** | DDollar staked OR community pledge milestone | Higher caps, Memory Engine persistence, advanced models (GLM 5.2, DeepSeek V4) |
| **T3 Launch-ready** | Raise Room pre-registration | Unlimited AI, deploy assistance, liquidity tier preview |

This mirrors how YC unlocks Demo Day only for companies that stay the batch. The cost of defection rises with tier because **they've staked real DDollar and accumulated real context** — leaving forfeits both.

### 4.5 Reputation staking — the slashing analog

Crypto PoS gives us a clean, well-understood mechanism: **stake capital, forfeit on misbehavior.** (Ethereum slashes up to 1 ETH + correlation penalty; Gitcoin uses staking/slashing for grants integrity.)

**Founder OS version:**
- Founders optionally **stake DDollar** (earned, not purchased) against a public pledge: *"This project will launch through Raise Room."*
- If they launch elsewhere and we detect it (Section 7), the stake is **slashed** — DDollar burned, reputation tier reset, verified badge revoked.
- Because DDollar is non-transferable and non-withdrawable (per `DDOLLAR_ECONOMY.md`), this is **reputational, not financial** — which is exactly the right threat level. We are not trying to seize anyone's money; we are enforcing a community norm with community currency.

This is the **enforceable, on-platform, jurisdiction-free** version of a liquidated-damages clause. It works because it never goes to court.

---

## 5. Contractual approaches

### 5.1 What a clause would say

A ToS + doxxing-agreement clause along the lines of:

> *"Free AI compute is provided in exchange for the founder's commitment to launch their project's primary token through Founder OS Raise Room. Launching the same project on an external DEX without prior written agreement constitutes a material breach. Founder OS may, at its discretion, revoke verified status, reset reputation/DDollar, and/or invoice for the retail value of AI compute used (capped at the published per-founder rate × months of usage)."*

### 5.2 Is it enforceable? (honest assessment)

The research on **liquidated damages** is sobering:
- Courts enforce liquidated damages **only if they are a genuine pre-estimate of loss, not a penalty.** (Makdessi/ParkingEye, UK Supreme Court; Lake River v. Carborundum, US 7th Cir.)
- A clause that produces a **windfall** to us, or whose stipulated sum bears no reasonable relationship to actual harm, is struck down as an unenforceable penalty.
- Delaware courts in particular have recently cast doubt on liquidated damages in tech-service agreements where the contract value is "readily ascertainable."

**Practical enforceability scorecard:**

| Lever | Enforceable? | Cost to enforce | Verdict |
|-------|-------------|-----------------|---------|
| Revoke verified status / badge | ✅ Yes, clearly | ~$0 | **Do this.** Pure platform right. |
| Reset DDollar / reputation | ✅ Yes (per ToS) | ~$0 | **Do this.** Non-transferable by design. |
| Invoice for AI compute used ($8/mo) | ⚠️ Probably, as genuine pre-estimate | $500–$5k legal + collections | **Threat only.** Sue only repeat/high-value. |
| Claim lost DEX fees ($1k–$15k) | ❌ Weak — speculative/contingent | $10k+ litigation | **Do not rely on this.** |
| Injunction forcing re-launch via us | ❌ Almost never granted | Very high | **Do not attempt.** |

### 5.3 Where the contract actually adds value

The clause is worth writing for four reasons, none of which require winning in court:

1. **Sets expectations clearly** — founders can't claim they didn't know.
2. **Legitimizes platform-side sanctions** (badge revoke, DDollar slash) that *are* enforceable.
3. **Deters the rational majority** — most founders won't breach a written agreement even if the penalty is soft.
4. **Preserves optionality** for the rare high-value, clear-cut case where sending a demand letter is worth it.

The contractual layer is **posture, not a primary weapon.** Spend an afternoon writing it; don't build a legal team around it.

---

## 6. The sticky approach (recommended) — positive lock-in

This is the core recommendation. **Instead of preventing departure, make staying the obviously better choice.** Every successful platform in our research set does this; none rely primarily on prevention.

Founder OS has unusually strong natural lock-in assets. The five pillars:

### Pillar 1 — Memory Engine (the strongest)

All project context — the conversation history, the decided architecture, the abandoned approaches, the tokenomics debates, the founder's stated mission — lives with us. **Leaving means explaining the entire project to a new tool from scratch.** This is the SaaS "data gravity" effect (BCG 2025: 77% of firms cite data model lock-in as the dominant switching cost) applied to project knowledge.

- Switching cost to re-explain a 3-month project to a fresh AI ≈ **40–80 hours of re-prompting** (per the Lovable/Bolt build-time benchmarks). That is real, painful, and obvious.
- Invest in making Memory Engine **deeply useful and visibly accumulated** (e.g., a "project knowledge graph" the founder can see growing). The more visible the asset, the more it hurts to leave.

### Pillar 2 — Reputation (DDollar + verified status)

Per `DDOLLAR_ECONOMY.md`, DDollar is the platform's memory of who did the work. It is **non-transferable, non-withdrawable, and earned, not bought.** This is the perfect lock-in asset: valuable to the user, worthless off-platform, zero financial liability for us.

- A founder who has accumulated 50k DDollar and Tier-3 status walks away with **$0 of transferable value** but loses **months of demonstrated credibility**.
- Make reputation **visible and portable-within-platform** (badges, tiers, pledger trust signals) so its loss is felt.

### Pillar 3 — Community (pledgers, followers, contributors)

The founder's supporters — people who pledged DDollar, followed the project, signed up for the raise — are **on our platform.** Launching elsewhere means:
- Re-recruiting every pledger manually
- Losing the public signal of community conviction (which *is* the Raise Room pitch)
- Forfeiting the network effect where pledgers → liquidity → launch success

This is the YC "Demo Day / Bookface network" analog. The community *is* the product.

### Pillar 4 — Liquidity (Raise Room network effect)

Our Raise Room should have the **deepest liquidity** for Founder-OS-launched tokens, because:
- Pledgers pre-committed → seeded LP
- Cross-token routing pools volume
- We can offer better fee tiers to ecosystem tokens

A founder who launches on pump.fun gets pump.fun's liquidity. A founder who launches on Raise Room gets *ours*. **If ours is meaningfully better for their specific token, leaving is economically irrational** even ignoring every other pillar. This is the single highest-leverage investment: a launchpad with thin liquidity loses to pump.fun regardless of any other lock-in.

### Pillar 5 — Launch economics (make staying cheaper)

Pump.fun and competitors charge **2–5% presale fees** + DEX commissions. Raise Room can be structured so that:
- Launching through us is **free or near-free** for verified founders (the 0.1% DEX fee is the cost, and it's far below competitor take rates)
- External launches lose the founder **creator fee-sharing** (a share of ongoing volume routed back to them — the pump.fun model, but only available if they stay)

The math: a founder who expects $5M lifetime volume on their token pays ~$5k in our 0.1% DEX fee but earns back creator fees that can exceed that. Leaving for pump.fun to "save" 0.1% is often a *net loss*.

---

## 7. Detection methods (post-hoc monitoring)

Even with strong lock-in, a small percentage will defect. We should detect them — not to punish, but to (a) learn why they left, (b) enforce soft sanctions (badge, DDollar), and (c) occasionally recover them.

### 7.1 On-chain launch monitoring (primary)

- **Solana/pump.fun:** Yellowstone gRPC stream on program `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`, parse `create`/`create_v2` instructions for {name, symbol, mint, creator, URI}.
- **EVM:** Webhook on new Uniswap/Base/BSC pair creation (Alchemy/Transpose/Dune).
- **Run a fuzzy matcher** against our project registry every detection. Score → triage queue.

Cost: a few hundred dollars/month in RPC/gRPC + a part-time moderator. This is **cheap and scales** regardless of founder count.

### 7.2 Wallet linkage

- At doxxing, capture the founder's primary Solana/EVM wallet.
- If that wallet (or a known-linked one) deploys a token whose name/symbol matches their Founder OS project → high-confidence flag.

### 7.3 Smart-contract fingerprinting (weak/supplementary)

- If our AI consistently emits certain import patterns, comment styles, or function-ordering, a deployed contract can be checked for those fingerprints.
- **Treat as a weak signal only** — trivially defeated by any refactor or second-LLM cleanup.

### 7.4 Community reporting (free and effective)

- Every founder has **pledgers**. Pledgers are financially and reputationally motivated to notice if "their" founder launches elsewhere.
- Add a one-click "report external launch" affordance on each project page. Reward accurate reports with DDollar (inverted reward principle — rewards attention-receivers).
- This is the YC "Bookface norms enforced by the community" mechanism, scaled.

### 7.5 Project-name/symbol watchlist

- Maintain an automatic watchlist of every project's declared name/symbol.
- Daily diff against newly launched tokens on the top 3 chains.
- Human-review the top matches each morning.

---

## 8. Recommended strategy — the balanced approach

### The 70 / 20 / 10 split

| Bucket | % of anti-abuse effort | Activities |
|--------|------------------------|------------|
| **Positive lock-in (stickiness)** | **70%** | Memory Engine depth, DDollar tiers, community features, Raise Room liquidity, creator fee-sharing, launch-through-us-is-cheaper |
| **Detection & monitoring** | **20%** | gRPC launch stream, fuzzy matcher, wallet linkage, community reporting, watchlist |
| **Contractual posture** | **10%** | ToS + doxxing clause, badge/DDollar sanctions (the *enforceable* parts), demand letters for rare high-value cases |

### Why this split

- **Lock-in is the only thing that scales.** It works whether we have 50 or 50,000 founders, costs nothing per-founder, and *also improves the product* — every stickiness feature is a feature users want anyway.
- **Detection scales linearly** but is cheap (RPC + one moderator) and feeds both the sanctions layer and product learning ("why did these 5 founders leave?").
- **Contracts do not scale.** Litigation cost is fixed-or-rising per case while DEX-fee loss per case is bounded. Reserve contracts for clear, high-value, repeat offenders.

### What NOT to do

- ❌ Do not try to DRM or prevent code export. It will fail, hurt adoption, and signal distrust.
- ❌ Do not rely on code watermarking as enforcement. It is a weak signal at best.
- ❌ Do not sue founders for lost DEX fees. Cost > recovery in ~95% of cases.
- ❌ Do not publicly shame defectors. Reputational sanctions (badge, DDollar) are enough; public attacks alienate the community.
- ❌ Do not gate AI so aggressively that earnest founders can't evaluate us. T0/T1 must be genuinely useful or we lose the top of the funnel.

---

## 9. What to implement — concrete features

Ordered by leverage (highest first). Each item is also a product improvement, not pure defense.

1. **Raise Room liquidity depth** — the #1 lever. If our book is thin, lock-in fails. Seed LP from pledger commitments; route ecosystem volume to ecosystem tokens.
2. **Memory Engine as a visible asset** — render the accumulated project graph so founders *feel* what they'd lose. Portability-export of context is explicitly *not* offered.
3. **Gradual resource unlock (T0→T3 tiers)** — gate GLM 5.2 / DeepSeek V4 + uncapped tokens behind DDollar stake or pledge milestone. Mirrors YC's batch-gated Demo Day.
4. **Reputation staking on launch pledge** — founders optionally stake DDollar against "will launch via Raise Room." Slashed on detected defection. Enforceable because DDollar is platform-internal.
5. **Creator fee-sharing for Raise Room launches** — founders earn a share of ongoing volume *only if* they launch with us. Makes leaving a measurable income loss.
6. **On-chain launch monitor (gRPC + fuzzy matcher)** — stream pump.fun/EVM launches, match to project registry, triage queue. Cheap, scalable, feeds sanctions + product learning.
7. **Community "report external launch" affordance** — one-click, DDollar-rewarded. Free, effective, community-norm-reinforcing.
8. **ToS + doxxing-agreement clause** — write it once (Section 5.1). Legitimizes sanctions, deters the rational majority.
9. **Sanctions automation** — on confirmed defection: revoke verified badge, slash staked DDollar, reset reputation tier, revoke AI access. All platform-internal, zero legal cost.
10. **Defection exit interview (lightweight)** — when a defection is detected, a neutral DM asking why. Data is worth more than the lost fees.

---

## 10. The math — when does enforcement exceed the loss?

### Per-defection economics

| Line item | Low | Mid | High |
|-----------|-----|-----|------|
| AI cost sunk (3 months) | $24 | $24 | $24 |
| Lost DEX fees (one-time + 30d volume) | $1,000 | $5,000 | $15,000 |
| Legal cost to pursue (demand letter → small claims) | $500 | $2,500 | $10,000+ |
| Expected recovery (probability × amount) | $50 | $500 | $2,000 |

**Decision rule:** pursue legally **only when** expected recovery > legal cost **AND** lost fees > $5,000. In practice this is the top ~5% of cases. The other 95% are handled by **platform-internal sanctions** (badge, DDollar, access) which cost ~$0 and have non-zero deterrent effect.

### Portfolio economics (why stickiness wins at scale)

Assume 1,000 active founders, 10% defect rate without any intervention:

- Lost DEX fees/year: 100 founders × $5,000 avg = **$500,000/yr**
- If stickiness cuts defection from 10% → 3% (a 7-point drop, very achievable): savings ≈ **$350,000/yr**
- Cost of the stickiness features: largely product investment we'd make anyway (Memory Engine, liquidity, community).
- Cost of a legal-enforcement-first program capable of handling 100 cases/yr: easily **$250k–$500k/yr** in legal + ops, with low recovery and community alienation risk.

**Stickiness has a higher ROI at every scale.** Enforcement has a role at the long tail of high-value cases; it is not the program.

### The YC reality check

YC does not sue companies that "waste" the batch. They absorb the loss because **the equity in the winners dwarfs the sunk cost on the defectors.** Founder OS should adopt the same posture: optimize for the founders who *stay and launch big*, accept single-digit-percent leakage, and never let anti-abuse efforts visibly degrade the founder experience.

---

## Appendix A — Research sources consulted

- **AI code portability:** Vibe Coder Blog (export comparison), Tessellate Labs 2026 benchmark, maketocreate 2026 tests, mcstarters comparison, alatirok 2026 review.
- **Copilot/code ownership:** GitHub Terms of Service, GitHub Copilot Product Specific Terms (2026-03-05), terms.law analysis, O'Reilly "Who Owns the Code Claude Wrote?" (Doe v. GitHub, 9th Cir. 2026).
- **YC model:** ycombinator.com/deal, YC Safe documents, Dench "After YC," TechCrunch "Does what happens at YC stay at YC?" (founder ethics policy, Bookface norms).
- **Code watermarking:** STONE (EACL 2026 Findings), "Is the Watermarking of LLM-Generated Code Robust?" (arXiv 2403.17983), RoSeMary (arXiv 2502.02068), MATRIX (arXiv 2604.16001), SWaRL.
- **Launch monitoring:** Solana Tracker / Shyft / Chainstack Yellowstone gRPC guides for pump.fun program `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`.
- **Staking/slashing:** ethereum.org PoS rewards & penalties, Chainlink slashing, Gitcoin staking/slashing, Consensys slashing explainer, Dauphine slashing paper.
- **Liquidated damages enforceability:** American Bar Assoc. (Delaware DecisivEdge), pactdraft.ai, LexisNexis (Makdessi/ParkingEye), Aaron Hall (SaaS LDs), DLA Piper.
- **Platform lock-in:** BCG "Managing Digital Platform Lock-In" (2025), SaaSTrueCost switching costs, Jeffrey Towson switching-costs, Product Philosophy "Switching Cost Engineering," InfoQ.
- **Launchpad economics:** Miracuves (IDO launchpad, PinkSale, BSCPad), MetaLamp launchpad guide, capwolf creator-fee explainer.

---

## Appendix B — One-paragraph version for the parent

**We cannot technically prevent a founder from copying AI-generated code and launching elsewhere — no platform can, and none try. The honest, industry-validated strategy is positive lock-in: invest ~70% of anti-abuse effort into making staying obviously better than leaving (Memory Engine depth, DDollar reputation tiers, pledger community, deepest Raise Room liquidity, creator fee-sharing, free launch for verified founders). Spend ~20% on cheap, scalable post-hoc detection (gRPC token-launch monitoring + fuzzy matching to our project registry + community reporting) that feeds platform-internal sanctions (badge revoke, DDollar slash) which are fully enforceable at ~$0. Reserve ~10% for a one-time contractual clause in ToS/doxxing agreement that deters the rational majority and preserves optionality for rare high-value cases. Do not sue for lost DEX fees except in the top ~5% of cases — enforcement cost almost always exceeds recovery, and YC's own model shows that optimizing for the founders who stay and launch big beats chasing the few who leak. The #1 most effective single strategy is deep Raise Room liquidity: if launching with us is meaningfully better for a founder's specific token, every other pillar holds without coercion.**
