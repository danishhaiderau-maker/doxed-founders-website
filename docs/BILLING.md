# Founder OS — Billing & Revenue Model

| Field | Value |
|-------|-------|
| **Status** | DRAFT — pending founder review |
| **Authored** | 2026-07-07 |
| **Owners** | Founder · Head of Product |
| **Replaces** | Ad hoc notes in `BYO_AI.md`, `JATEVO_BYOK.md`, `DDOLLAR-POC-TWO-LEDGER-SPEC.md` (those still hold schema detail; this is the strategy) |
| **Reads against code** | `apps/api/src/ddollar/*`, `apps/api/src/founder-os/builder-score.service.ts`, `apps/api/src/ai-proxy/*`, `prisma/schema.prisma` (`BuilderTier`, `PointLedger`, `FounderTreasuryLedgerEntry`) |

---

## 1. Thesis

**Founder OS is free for founders. Founder OS eats AI cost as customer acquisition. Revenue comes from token launches via an integrated DEX (0.1% fee). DDollar is a virtual in-game currency that tracks founder contribution so that, if and when the platform ever does an airdrop or releases a token, DDollar balances make it easy to divide rewards fairly.**

There is no subscription, no markup on AI, no BYOK. The platform runs GLM 5.2 + DeepSeek with a caching layer and gives every verified founder a daily token allowance. Founders who outgrow the allowance either install Founder Node + Ollama locally (still free) or earn more DDollar by contributing.

Every dollar spent on inference is a marketing dollar. The platform converts that spend into founder equity in the DDollar economy, which converts to launch volume at Phase 7+, which converts to DEX fee revenue.

---

## 2. DDollar — definition

| Property | Value |
|----------|-------|
| Nature | Virtual in-game currency. Not a security, not a token, not a coupon. |
| Fiat peg | **None.** Not pegged to USD. |
| Outside value | **Zero.** Cannot be withdrawn, sold, transferred for cash, or redeemed for fiat. |
| Supply | Unlimited. Scales with user base. |
| Storage | `User.reputationPoints` (spendable) + `User.lifetimeContributionEarned` (monotonic). |
| Ledger | `PointLedger` (per-user) + `FounderTreasuryLedgerEntry` (platform fees). |
| Two purposes | (1) **Future airdrop snapshot** if/when platform launches a token. (2) **Platform utility** — AI overage, Raise Room conviction, scout markets, marketplace, boosts. |

Full mechanic in [`DDOLLAR_ECONOMY.md`](./DDOLLAR_ECONOMY.md).

---

## 3. Internal reference rate (proposal)

> **Proposal:** `1 DDollar ≈ $0.001` of API cost at GLM 5.2 blended rates.

This is **not** a peg, a redemption promise, or a forward contract. It is a psychological anchor that keeps reward numbers grounded in real economics so a future internal exchange (e.g. "spend DDollar to unlock a GLM 5.2 coding boost") feels honest.

### Why this rate

GLM 5.2 z.ai coding rates, July 2026 (assumption — verify against current z.ai pricing page before locking):

| Component | Rate | Source quality |
|-----------|------|----------------|
| Input | ~$0.002 / 1K tokens | Stated — verify |
| Output | ~$0.006 / 1K tokens | Stated — verify |
| Blended (75% input / 25% output) | ~$0.003 / 1K tokens | Derived |
| With 50% cache hit on prompt prefix | ~$0.002 / 1K tokens effective | Derived |

So **1,000 tokens of blended usage ≈ $0.002 ≈ 2 DDollar** under the proposal. This makes the existing constants in `apps/api/src/ai-proxy/ai-proxy.constants.ts` (`DDOLLAR_COST_PER_REQUEST = { fast: 1, code: 2, reasoning: 3 }`) sit roughly at break-even — each request costs the platform about what it charges in DDollar.

### Conversion examples

| DDollar amount | Rough API equivalent | Notes |
|----------------|----------------------|-------|
| 100 DDollar | ~50K blended tokens | ~1 large coding prompt |
| 1,000 DDollar | ~500K blended tokens | ~1 day of heavy Founder OS use |
| 10,000 DDollar | ~5M blended tokens | ~10 founder-days of AI |
| 100,000 DDollar | ~50M blended tokens | ~$100 of inference at retail |

Founders should treat the rate as **a way to keep numbers sane**, not as a commitment.

---

## 4. The two tiers

The site is **DoxxedCrypto**. Doxxing is not a "Tier 2 upgrade" bolted on top — it is the central product mechanic. Every capability flows from whether you are doxxed or not.

Existing code (`apps/api/src/founder-os/builder-score.service.ts`, `prisma/schema.prisma` `BuilderTier`) currently names these **PARASITE** and **VERIFIED_BUILDER**. Proposed rename:

| Tier | Internal name | User-facing name | Auth required | Trust required |
|------|---------------|------------------|---------------|----------------|
| **Tier 1** | `PARASITE` | **Visitor** | Twitter account | None |
| **Tier 2** | `VERIFIED_BUILDER` | **Doxxed Builder** | Twitter + GitHub | Founder video (60–90s) — reviewed personally by the founder |

The word "Doxxed" is intentional — it leans into the brand. The site name does the marketing.

### Tier matrix

| Capability | Visitor (Tier 1) | Doxxed Builder (Tier 2) |
|------------|------------------|-------------------------|
| Daily AI token cap (proposal) | **50,000** tokens (~$0.10–0.15/day) | **Unlimited within fair-use throttle** + Founder Node Ollama local supplement |
| Daily DDollar earn cap (proposal) | **100 DDollar/day** | **2,000 DDollar/day** |
| Project wall posting | ❌ No | ✅ Yes |
| Token launch rights | ❌ No | ✅ Yes |
| AI proxy IDE access | ✅ Yes (capped) | ✅ Yes (uncapped) |
| Paper trading | ✅ Yes | ✅ Yes |
| Marketplace purchase | ✅ Yes | ✅ Yes |
| Marketplace listing | ❌ No | ✅ Yes |
| Scout validation votes | ✅ Yes (capped weight) | ✅ Yes (full weight) |

> The 50K Tier 1 cap is a **raise** from the current `PARASITE_DAILY_TOKEN_CAP` default of `25000` (`apps/api/src/ddollar/spending-engine.service.ts:152`) — 25K is too low to even evaluate the platform. 50K is enough to do real exploration (30–50 Cursor turns, or 5–10 substantial feature asks) while staying inside the founder's $0.15/day/DAU budget. Hitting the cap is the **conversion trigger**: the platform surfaces "Become a Doxxed Builder (GitHub + Twitter + 60-sec video) to unlock unlimited AI."

### Why video is non-negotiable for Tier 2

Twitter Blue is $8/month and gameable. A paid checkmark proves nothing about personhood. KYC is fakeable ($5 stolen-ID scans). The only cheap, hard-to-fake signal at scale is a founder video — 60–90 seconds of a real human speaking about their project. The `FounderApplication.videoUrl` field already exists in the schema; the upgrade flow just needs to enforce it.

### Verification flow (reuses `/admin/applications` inbox)

The founder already has a listing-inbox at `https://doxxedcrypto.digital/admin/applications` for scout-submitted project approvals. Doxxing applications are the same shape (applicant → admin review → approve/reject), so no new infrastructure is required — only a new application type:

```
Founder clicks "Apply for Doxxed Builder" in Founder Node or web settings
   ↓
Submits three things:
   • GitHub account        (proves they code — empty GitHub = soft reject)
   • Twitter account       (already authed at signup, reconfirmed here)
   • 60–90s founder video  (who they are, what they're building, why)
   ↓
Application lands in /admin/applications next to scout-submitted projects
   ↓
**Founder (you) personally reviews** — no automated KYC, no third-party gate
   ↓
On approval → tier flips to Doxxed Builder, caps lift, launch button unlocks
```

This is a feature, not a limitation. At <1,000 Doxxed Builders you can review every application yourself. The platform's trust signal is "DoxxedCrypto approved by the founder personally" — and that signal is what differentiates the platform from generic launchpads.

---

## 5. AI cost model

| Principle | Setting |
|-----------|---------|
| Subscription fee | **None.** Free for founders. |
| Markup on AI | **None.** Platform pays retail. |
| BYOK | **Dead.** Founders never touch API keys. (Existing `BYO_AI.md` and `JATEVO_BYOK.md` are deprecated.) |
| Models provided | GLM 5.2 (code), DeepSeek (reasoning), fast cheap model (chat) |
| Caching | Prompt-prefix cache across requests (target 50% hit rate) |
| Metering | Per-request DDollar charge in `AI_PROXY_DDOLLAR_COST` (1/3/2 by tier). Real guard is the daily token cap in `SpendingEngine.enforceTierCap`. |
| Off-platform fallback | Founder Node + Ollama for founders who want unlimited local AI. Free, unsupported. |

### ⚠ Tension 1 — The token-cost math (and why 50K is the right Tier 1 cap)

The founder's original estimate was "200K tokens/day ≈ $0.15/day at GLM rates." The actual math says 200K blended tokens costs **$0.30–$0.60/day**, 3–4× more expensive than the estimate. At the founder's preferred $0.15/day/DAU budget, the correct Tier 1 cap is **50K tokens/day**:

| Component | Tokens | Rate ($/1K) | Cost |
|-----------|--------|-------------|------|
| Input (assumed 75% of blend) | 37,500 | $0.002 | $0.075 |
| Output (assumed 25% of blend) | 12,500 | $0.006 | $0.075 |
| **Gross daily cost per Visitor (50K cap)** | **50,000** | blended ~$0.003 | **~$0.15** |
| With 50% prompt-prefix cache hit (realistic) | 50,000 effective | ~$0.002 | **~$0.10** |
| With 70% cache hit (long coding sessions) | 50,000 effective | ~$0.0015 | **~$0.08** |

This sits exactly inside the founder's $0.10–$0.15/day/DAU budget. The earlier 200K proposal would have been $0.30–0.60/day — out of budget. 50K is enough to evaluate the platform (30–50 Cursor turns, or 5–10 substantial feature asks) and converts cleanly into the Doxxed Builder upgrade prompt.

### Tier 2 (Doxxed Builder) is uncapped — but realistic cost is bounded

Doxxed Builders have no token cap, but in practice their usage is bounded by the same thing that bounds any coder: how many hours/day they actually code. Realistic Doxxed Builder burn is **$0.80–$1.50/day/active-builder** (heavy Cursor users typically do 500K–1M tokens/day). The platform absorbs this as customer-acquisition cost — Doxxed Builders are the ones who eventually launch tokens.

### Cost tables

Tier 1 (Visitor) at 50K/day:

| DAUs | Cost/day | Cost/month (30d) | Cost/year |
|------|----------|------------------|-----------|
| 100 | $10–15 | $300–450 | $3.6K–5.4K |
| 1,000 | $100–150 | $3K–4.5K | $36–54K |
| 10,000 | $1K–1.5K | $30K–45K | $360–540K |
| 100,000 | $10K–15K | $300K–450K | $3.6–5.4M |

Tier 2 (Doxxed Builder) at ~$1/day blended (avg, not cap):

| Active builders | Cost/day | Cost/month (30d) | Notes |
|-----------------|----------|------------------|-------|
| 100 | $100 | $3K | Survivable on small grant/runway |
| 1,000 | $1,000 | $30K | Inflection — DEX revenue must start covering |
| 10,000 | $10,000 | $300K | Requires category-defining launch volume |

**Most real users will be Tier 1.** Conversion to Tier 2 is the funnel. Tier 1 burn is the cost of customer acquisition; Tier 2 burn is the cost of customer success.

The 10K Tier-1 DAU row (or equivalently 1K active Doxxed Builders) is the inflection. Above that, the platform either (a) graduates enough founders to DEX revenue to cover the burn, (b) tightens Tier 1 caps, or (c) raises the cache hit rate via better prompt engineering. Plan for (a) and (b), invest in (c).

**Cache hit measurement:** Add a `cache_level` (already present as `cacheLevel` on `AiTokenUsageLog`) reporting row per request — `hit`, `partial`, `miss`. Report weekly cache savings in admin dashboard. Target ≥50% by Q4 2026.

---

## 6. Revenue model — token launch + DEX (Phase 7+)

### The flow

```
Founder OS → [I'm ready to launch my token] button
           ↓
         Raise Room (commitment window opens)
           ↓
         Solana token mint (platform-sponsored or founder-sponsored)
           ↓
         15-day community commitment window (DDollar commit / paper SOL signal)
           ↓
         DEX listing (Raydium / Meteora / Pump — platform choice)
           ↓
         0.1% fee on every trade, platform takes it
```

The 0.1% DEX fee is the **only monetization**. No listing fee, no token tax, no premium tier.

### ⚠ Tension 3 — This is Phase 7+, not Phase 1

This is the flagship revenue thesis, but it is **not the first thing to build**. Reasoning:

1. **Scope.** Solana program selection, RPC infra, DEX frontend, Phantom wallet UX (already in codebase), compliance/geofencing, refund/escrow logic (`docs/DDOLLAR-LAUNCH-ALLOCATION-PROPOSAL.md` spec'd 4–6 weeks for MVP).
2. **Reputation risk.** Shipping token-launch on a half-built kernel = scammers launch on a weak platform = the platform becomes a launchpad for rugs. The platform's one durable asset is curation. Lose it once, lose it forever.
3. **Sequencing.** The kernel needs to earn the right to ask founders to launch. That means: Founder OS command center → Raise Room paper trading → scout markets → community validation → **then** real launches.

The platform gets there when:

- ≥1,000 Verified Builders active monthly
- ≥10 founders have graduated a project from IDEA → LAUNCH_READY through the kernel
- Trust Center scam-flag false positive rate is <5%
- Phantom wallet connect is production-hardened in the existing codebase

Until then, `docs/DDOLLAR-LAUNCH-ALLOCATION-PROPOSAL.md` is the spec, this doc is the thesis.

---

## 7. Break-even math

Assumptions (clearly labeled — founder to challenge):

| Assumption | Value | Basis |
|------------|-------|-------|
| Per-DAU AI cost — Tier 1 Visitor (majority) | $0.10–0.15/day | See §5 |
| Per-active-Doxxed-Builder AI cost | $0.80–1.50/day | See §5 |
| DEX fee | 0.10% of trade volume | Founder-stated |
| Net platform share of DEX fee | 100% (assume platform operates the DEX frontend) | Proposal — may share with liquidity providers |
| Token launch survival rate | 30% of launches produce sustained volume | Industry assumption — verify post-launch |

### Break-even table

Blended assumption: 90% of users are Tier 1 Visitors, 10% are Doxxed Builders.

| Total DAUs | Tier 1 cost/mo | Tier 2 cost/mo | Total AI burn/mo | Monthly DEX volume needed to break even |
|------------|----------------|----------------|------------------|------------------------------------------|
| 100 | $270–405 | $360–675 | ~$0.6–1.1K | $600K–1.1M (one hot launch covers it) |
| 1,000 | $2.7K–4K | $3.6K–6.8K | ~$6–11K | $6–11M (one mid-size launch or 3–5 active) |
| 10,000 | $27K–40K | $36K–68K | ~$60–110K | $60–110M (3–5 sustaining launches w/ active market-making) |
| 100,000 | $270K–400K | $360K–680K | ~$0.6–1.1M | $0.6–1.1B (requires category-defining launch volume) |

**Read this as:** the platform doesn't need a Cambrian explosion of launches. It needs **a small number of high-quality launches with sustained secondary volume**. One launch doing $50M/month in DEX volume covers ~5,000 active DAUs of AI burn.

That is the entire business model in one sentence: **curate hard, doxx everyone, launch few, fee the volume.**

---

## 8. ⚠ Tension 2 — The anti-spam mechanic must be inverted

The founder asked "how do we identify good chat vs spam?" The answer is structural, not algorithmic: **don't reward the speaker, reward the attention-gainer.**

| Naive model (Patreon-of-spam) | Inverted model (attention economy) |
|--------------------------------|------------------------------------|
| Pay per post | Pay per attention received |
| Founder posts on own wall → earns | Founder posts on own wall → earns **0** |
| User replies fast → earns | User replies → earns small (5–10 DDollar) capped per day |
| Spam is profitable | Spam is economically irrational — it produces noise that earns the *recipient*, not the speaker |

The full reward schedule is in [`DDOLLAR_ECONOMY.md` §4](./DDOLLAR_ECONOMY.md#4-earning-rates-table). The principle encoded there:

- Rewards flow **inward** (to attention-receivers: founders whose wall attracts replies, projects that attract follows)
- Not **outward** (to noise-producers: post-count, reply-count, login streaks alone)

This is the only design that survives at scale. Post-count rewards die the day a user writes a bot.

---

## 9. ⚠ Tension 4 — Twitter is auth, video is trust

| Layer | What it proves | What it costs an attacker |
|-------|----------------|---------------------------|
| **Twitter auth** | An X account exists | $0 — burner accounts are free |
| **Twitter Blue** | Someone paid $8 | $8 — gameable, **not a personhood signal** |
| **GitHub connection** | A dev profile exists | Low — stolen or bought accounts |
| **LinkedIn connection** | A professional identity exists | Medium — harder to fake at scale |
| **Founder video** | A real human can speak about this project for 30s+ | **High** — requires face, voice, project fluency |
| **Cross-platform consistency** | Same name/face across GitHub/LinkedIn/X/video | **Very high** — multi-account farming collapses |

### Two-tier distinction in plain English

- **Tier 1 = Twitter exists.** Visitors — cheap auth, low earning cap, no project wall posting, no launch rights, 50K token/day AI cap. The platform is letting you look around. Hitting the cap surfaces the Doxxing prompt.
- **Tier 2 = video + GitHub + cross-platform identity, personally reviewed by the founder.** You're a real person with reputation you'd lose by abusing the platform. You get launch rights, wall posting, full DDollar earning, uncapped AI.

Twitter is the **AUTH** layer. Video + GitHub is the **TRUST** layer. The doc and the product both need to make this distinction explicit — never call Twitter Blue "verification" in user-facing copy. Only the founder's personal review counts as Doxxing.

---

## 10. Open questions (founder input requested)

1. **DDollar decay?** Should lifetime DDollar decay if a user is inactive for 6+ months, or stay permanent? (Default proposal: permanent — it's their work record.)
2. **Burn mechanism?** Beyond Raise Room conviction commits, should there be a periodic burn (e.g. marketplace listing fee in DDollar) to keep supply from inflating infinitely?
3. **Founder-set supporter splits?** When a project launches, should the founder be able to specify "X% of my launch DDollar credit splits to my top-N scouts"? (Proposal: yes — aligns with attention-receivers-earn principle.)
4. **Daily earning cap shape?** Hard cap vs. diminishing-returns curve past N DDollar/day? (Proposal: hard cap for v1, revisit at 10K DAUs.)
5. **Replay attack prevention?** How do we stop a user from deleting and re-doing an action to farm the reward? (Existing `awardOnce` helper handles one-time actions; needs extension for repeatable-with-cap.)
6. **Sybil resistance beyond Twitter?** What's the multi-account detection strategy at scale? IP + device fingerprint + behavioral similarity? (Existing `AntiAbuseService` only does single-award and daily-total caps.)
7. **Geofencing for token launch?** At Phase 7+, which jurisdictions are blocked from the DEX flow? Needs legal input, not engineering.
8. **Founder Node usage accounting?** When a founder uses local Ollama for AI, does that count against their Tier 2 daily cap? (Proposal: no — local is unlimited and off-platform.)

---

## 11. Change log

| Date | Author | Change |
|------|--------|--------|
| 2026-07-07 | Strategy doc | Initial draft synthesizing founder billing Q&A |
| 2026-07-07 | Strategy doc | Refined after founder follow-up: Tier 1 cap 200K → **50K**, tier names Explorer/Verified Builder → **Visitor/Doxxed Builder**, video review routed through existing `/admin/applications` inbox, cost tables recomputed. |
