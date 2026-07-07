# DDollar Economy

| Field | Value |
|-------|-------|
| **Status** | DRAFT — rates are proposals for founder tuning, not decrees |
| **Authored** | 2026-07-07 |
| **Owners** | Founder · Head of Product · Anti-Abuse |
| **Pairs with** | [`BILLING.md`](./BILLING.md) (strategy), [`DDOLLAR-POC-TWO-LEDGER-SPEC.md`](./DDOLLAR-POC-TWO-LEDGER-SPEC.md) (schema), [`DDOLLAR-LAUNCH-ALLOCATION-PROPOSAL.md`](./DDOLLAR-LAUNCH-ALLOCATION-PROPOSAL.md) (launch escrow) |
| **Reads against code** | `apps/api/src/ddollar/*`, `packages/utils/src/reputation-points.ts`, `packages/utils/src/ai-proxy.ts`, `prisma/schema.prisma` |

---

## 1. Purpose

DDollar exists for two jobs:

1. **Record founder contribution** so that, if and when the platform does an airdrop or releases a token, there's a clean ledger dividing rewards fairly. This is the snapshot primitive.
2. **Power platform utility** — paper trading conviction commits, scout market stakes, marketplace purchases, AI overage, project boosts. This is the spend primitive.

It is **not**:

- A security, token, or investment contract.
- Pegged to USD or any fiat.
- Withdrawable, transferable for cash, or redeemable outside the platform.
- A speculative asset. Supply is unlimited and scales with users.

DDollar is the platform's memory of who did the work. Spend it on toys; keep lifetime contribution as the reputation that matters.

---

## 2. The Inverted Reward Principle

> **Reward attention-receivers, not noise-producers.**

This is the single most important design choice in the DDollar economy. Get it wrong and the platform drowns in spam within a week of launch. Get it right and spam is economically irrational.

### Why inverting works

Patreon / Reddit-karma / post-count economies reward the **speaker** for the act of speaking. The moment the reward per post exceeds the cost of posting, someone writes a bot. The platform becomes a noise firehose and the currency inflates to zero.

The inverted model rewards the **recipient of attention**:

- Founder posts on their own wall → **0 DDollar** (otherwise every founder spams their own wall)
- Verified user replies on a founder's wall → **small reward to the user** (5–10 DDollar, capped per day)
- Founder's wall post attracts 5+ verified replies → **founder earns** (50–100 DDollar per qualifying post)
- Verified user follows the project → **founder earns** (25 DDollar per follow, one-time)
- Verified user casts a validated scout vote → **both voter and founder earn** (10 DDollar each)

Notice the direction: rewards flow **inward** to whoever earned real attention. To farm DDollar you'd have to convince dozens of verified humans to engage with you. That's not farming — that's just being a successful founder.

### The principle as a one-liner for the codebase

```
// DDollar is paid for ATTENTION RECEIVED, never for CONTENT PRODUCED.
// The speaker gets nothing by default. The listened-to gets paid.
```

This principle must be enforced everywhere `RewardEngine.award()` is called. Any new reward path that pays the producer for producing needs explicit anti-spam gating.

---

## 3. Earning caps by tier

| Tier | Auth | Daily earn cap (proposal) | Daily AI cap (proposal) | Launch rights | Project wall posting | Scout vote weight |
|------|------|---------------------------|-------------------------|---------------|----------------------|-------------------|
| **Visitor** (Tier 1) | Twitter account | **100 DDollar/day** | 50,000 tokens | ❌ | ❌ | 0.5× |
| **Doxxed Builder** (Tier 2) | Twitter + GitHub + 60–90s founder video (reviewed personally) | **2,000 DDollar/day** | Unlimited (fair-use throttle) + Founder Node Ollama local | ✅ | ✅ | 1.0× |

Notes:

- The current env-var defaults (`PARASITE_DAILY_TOKEN_CAP=25000`, `BUILDER_DAILY_TOKEN_CAP=500000` in `apps/api/src/ddollar/spending-engine.service.ts:152-153`) need updating to match this table. Suggested new defaults: `25000 → 50000`, `500000 → unlimited` (enforced via fair-use throttle, not hard cap).
- The current `AntiAbuseService` global caps (`DDOLLAR_MAX_SINGLE_AWARD=50000`, `DDOLLAR_MAX_DAILY_AWARD=100000`) override the per-tier caps. Either lower the globals to enforce the tier cap or remove them in favor of the tier-specific check. Recommendation: keep `MAX_SINGLE_AWARD=50000` as a safety, replace `MAX_DAILY_AWARD` with the tier-specific value.
- Founder Node (local Ollama) usage does **not** count against the Tier 2 cap. Local is unlimited and off-platform.
- Hitting the 50K Tier 1 token cap is the **conversion trigger**: the platform surfaces "Become a Doxxed Builder (GitHub + Twitter + 60-sec video, founder-reviewed) to unlock unlimited AI." This is the single most important funnel mechanic in the platform.

---

## 4. Earning rates table

> All numbers are **proposals** for founder tuning. The existing constant map lives in `packages/utils/src/reputation-points.ts` (`POINTS`) — the keys below are matched to that file where they exist.

### Onboarding

| Action | Earner | Amount | Cooldown / Cap | Notes |
|--------|--------|--------|----------------|-------|
| Sign up with Twitter | User | 100 DDollar | One-time, requires Twitter auth | Replaces current `WELCOME_DDOLLAR_GRANT = 10_000` (too generous for unverified) |
| X blue-verified at signup | User | +500 DDollar | One-time, on top of signup | Existing `X_BLUE_VERIFIED = 15_000` is too generous — blue is paid and gameable |
| Daily login | User | 5 DDollar | Once/day, streak decays after 3 days broken | Existing `DAILY_LOGIN = 5` — keep |
| Referral: invitee becomes Verified Builder | Referrer | 250 DDollar | Per invitee, paid on inviter verification | Replaces `REFERRAL = 5_000` / `REFERRAL_BLUE = 15_000` — too generous, paid too early |

### Project engagement (attention-inward rewards)

| Action | Earner | Amount | Cooldown / Cap | Notes |
|--------|--------|--------|----------------|-------|
| Verified user posts on project wall | User | 5 DDollar | Max 10/day per user | Small, capped — rewards participation but not volume |
| Project wall post attracts 5+ verified replies | Project founder | 50 DDollar | Per post per day | **Core inverted reward** — pays founder for attention received |
| Verified user follows a project | Project founder | 25 DDollar | One-time per follow | Real audience growth — founder earns |
| Reply marked "helpful" by asker | Replier | 15 DDollar | Per helpful mark | Existing `HELPFUL_MARK = 75` is too high; lower to 15 |
| Founder ships a build post (commit-linked) | Founder | 100 DDollar | Max 5/day | Existing `FOUNDER_BUILD_POST = 50` — raise to 100, gate on commit link |
| Founder publishes release/update | Founder | 200 DDollar | Per update | Existing `FOUNDER_VIDEO = 150`, `FOUNDER_COMMUNITY_POST = 75` — consolidate |

### Scout / validation (existing in `POINTS`)

| Action | Earner | Amount | Cooldown / Cap | Notes |
|--------|--------|--------|----------------|-------|
| Submit listing for community vote | User | 50 DDollar | Per submission | Keep `LISTING_SUBMIT = 50` |
| Cast community validation vote (LOOKS_LEGIT etc.) | Voter + founder | 10 DDollar each | Per project per voter | Raise `LISTING_VOTE = 10` (keep); add founder-side award |
| Scout correctly predicts launch graduation | Scout | 500 DDollar | Paid on graduation | Existing `EARLY_SCOUT = 200`; raise to 500, pay on outcome not on stake |
| Approved listing application | Founder | 500 DDollar | One-time per project | Existing `FOUNDER_PROJECT_LAUNCH = 500` — keep |

### Explicitly **not** rewarded

| Action | Earner | Amount | Why |
|--------|--------|--------|-----|
| Founder posts on their own wall (no engagement) | — | **0 DDollar** | Inverted principle: producers don't earn, attention-receivers do |
| Plain reply with no helpful mark | — | **0 DDollar** | Existing `COMMUNITY_COMMENT` already defaults to 0 — keep |
| Daily AI proxy usage up to 200K tokens | — | **0 DDollar** | AI is free; spending it doesn't earn anything |
| Watchlist add | — | **0 DDollar** | Existing `WATCHLIST_ADD = 2` — too easy to farm, set to 0 |
| Feed comment (unmarked) | — | **0 DDollar** | Existing `FEED_COMMENT = 2` — too easy to farm, set to 0 |

### Existing rates that need to come down

The current `POINTS` map has several values calibrated for an older "reward the speaker" model. Recommended changes:

| Key | Current | Proposed | Reason |
|-----|---------|----------|--------|
| `WELCOME_DDOLLAR_GRANT` | 10,000 | 100 | Unverified signup shouldn't get 10K — invites immediate DDollar-for-account farming |
| `X_BLUE_VERIFIED` | 15,000 | 500 | Blue is $8 and gameable; don't pay 15K for it |
| `REFERRAL` | 5,000 | 250 | Pay on verified-builder conversion, not on signup |
| `REFERRAL_BLUE` | 15,000 | 0 (remove) | Folded into the verified-builder referral payout |
| `HELPFUL_MARK` | 75 | 15 | Still the largest comment-side reward but less exploitable at 15 |
| `FOUNDER_VIDEO` | 150 | 200 (consolidate with community post) | Cleaner |
| `FOUNDER_COMMUNITY_POST` | 75 | 0 (fold into build post) | Founder-side rewards should be tied to engagement outcome, not post count |

---

## 5. Spending options

DDollar buys platform utility, never fiat.

| Spend | Cost (proposal) | Where it lives | Status |
|-------|-----------------|----------------|--------|
| AI proxy usage beyond daily free cap (auto-throttle to slow tier) | 1–3 DDollar/request (existing `DDOLLAR_COST_PER_REQUEST`) | `apps/api/src/ai-proxy/ai-proxy.constants.ts` | ✅ Shipped |
| Paper trading conviction commit (Raise Room) | 500 DDollar / commit | `SimulatedRaise` + `RaiseAllocation` (paper) | ✅ Schema ready |
| Scout market stake (prediction markets) | 100–1,000 DDollar / stake | Phase 7+ — `SCOUT_STAKE` action key reserved | 🚧 Phase 7+ |
| Marketplace purchase | listing-set DDollar price + 10% treasury fee | `MarketplaceLedgerEntry` + `MARKETPLACE_TREASURY_FEE_BPS = 1000` | ✅ Schema ready |
| Project boost (founder pays to amplify in Discover) | 1,000 DDollar / boost / 24h | New — Phase 6+ | 🚧 Proposed |
| Founder splits DDollar with their supporters manually | variable | New `FounderCreditLedger` already exists in schema | 🚧 Proposed |
| Token launch whitelist commit (escrow, refunded if not selected) | 500 / 2,500 / 10,000 by tier | `TokenLaunch` + `LaunchInterestCommit` (per `DDOLLAR-LAUNCH-ALLOCATION-PROPOSAL.md`) | 🚧 Phase 7+ |

### Treasury fee

Marketplace purchases already incur a 10% platform fee (`MARKETPLACE_TREASURY_FEE_BPS = 1000`) routed to `FounderTreasuryLedgerEntry`. This is the only "burn" mechanism today. Keep it. Consider whether AI overage should also carry a small treasury fee once founders hit the daily cap.

---

## 6. Anti-abuse rules

The existing `AntiAbuseService` is minimal — single-award cap (`MAX_SINGLE_AWARD=50000`) and 24h rolling daily total (`MAX_DAILY_AWARD=100000`). It needs to grow up.

### Tier 1: Account-graph defenses

| Defense | Implementation | Status |
|---------|----------------|--------|
| Single-IP rate limit | Existing `RateLimit` table; extend to flag N signups/hour from one IP | 🚧 |
| Device fingerprint collision | Existing `ProjectMemoryDeviceSync.payload` pattern; extend to auth | 🚧 |
| Email domain blacklist | Block 10-minute-mail and similar disposable providers | 🚧 |
| Twitter account age check | Reject <30-day-old accounts at signup | 🚧 |
| Twitter follower count threshold | Soft gate: <10 followers → Tier 1 only, no follow-rewards paid out | 🚧 |

### Tier 2: Behavior-pattern defenses

| Defense | Implementation | Status |
|---------|----------------|--------|
| Cooldown enforcement | Per-user per-action cooldowns in Redis (e.g. wall-reply: 30s) | 🚧 |
| Velocity anomalies | >X DDollar earned in 1h from same action → manual review flag | 🚧 |
| Reciprocal-engagement detection | A replies on B's wall, B replies on A's wall, repeat → zero both accounts' awards | 🚧 |
| Cluster detection | Connected-component analysis on UserFollow + ProjectFollow + wall-reply graph; flag dense clusters of mutual engagement | 🚧 Phase 7+ |

### Tier 3: Replay attack prevention

| Defense | Implementation | Status |
|---------|----------------|--------|
| Idempotency keys on all reward paths | Existing `awardOnce(userId, actionKey, amount)` pattern in `PointsService` for one-time rewards | ✅ Partial — extend to repeatable-with-cap |
| Content hash dedupe | Same body posted twice in 24h → no reward on second | 🚧 |
| Delete-and-redo protection | Once an action has earned DDollar, deleting the underlying entity does **not** refund; the ledger row persists | 🚧 |
| Re-validation on edit | Editing a build post within 24h of posting resets its reward eligibility | 🚧 |

### Tier 4: Sybil resistance

Twitter alone is not enough (Tension 4 in [`BILLING.md`](./BILLING.md)). The full chain:

1. **Twitter auth** — cheap, lets you in the door.
2. **GitHub commit history** — 14-day commit window feeds the existing `BuilderScoreService` score (+1 per commit, cap +20).
3. **Cursor connection** — `IntegrationCredential` with verifiedAt adds +25 to builder score.
4. **Founder video** — `FounderApplication.videoUrl`, manual review for Tier 2.
5. **Cross-platform name/face consistency** — same person across GitHub/LinkedIn/X/video.

A sybil has to fake all five layers. The video layer alone costs real time per fake identity.

---

## 7. Future: airdrop snapshot

When the platform launches its own token (Phase 7+, contingent on legal review), DDollar lifetime balances become the snapshot primitive.

### Illustrative formula

```
airdrop_share = user_lifetime_ddollar
              / total_lifetime_ddollar_all_users
              × airdrop_pool
```

Worked example:

| User | Lifetime DDollar | Share of pool | Tokens received (10M pool) |
|------|------------------|---------------|----------------------------|
| Founder A | 250,000 | 5.0% | 500,000 |
| Founder B | 80,000 | 1.6% | 160,000 |
| Scout C | 25,000 | 0.5% | 50,000 |
| User D | 5,000 | 0.1% | 10,000 |
| Total (1000 users) | 5,000,000 | 100% | 10,000,000 |

### Important caveats

- **Founder decides the formula at launch.** This is illustrative, not a promise.
- **Snapshot is one-time, not continuous.** Lifetime balances are monotonic (existing invariant: `applyAward` increments, `applySpend` does not decrement — see `apps/api/src/ddollar/ddollar-ledger.logic.ts`). One snapshot, then the formula is set.
- **Legal review required** before any public commitment of formula or pool size. Treating DDollar as a forward contract for tokens is exactly the regulatory trap to avoid.
- **Anti-abuse lookback.** Before snapshot, run cluster detection (§6 Tier 2) and zero out accounts flagged as sybil farms.

---

## 8. Reference rate

Restated from [`BILLING.md`](./BILLING.md) §3:

> **1 DDollar ≈ $0.001 of API cost at GLM 5.2 blended rates.**

Not a peg. Not a redemption promise. A psychological anchor.

### Conversion examples for product copy

| Found in product | DDollar | Rough API value |
|------------------|---------|-----------------|
| Daily Tier 1 cap | 100 DDollar earn / 50K tokens | ~$0.10–0.15 of inference |
| Tier 2 monthly cap | 60,000 DDollar earn (2,000 × 30) | ~$60 of inference earning potential |
| Project boost | 1,000 DDollar | ~$1 of inference equivalent |
| Whitelist commit (anchor tier) | 10,000 DDollar | ~$10 of inference equivalent |
| Founder OS welcome grant (proposed) | 100 DDollar | ~$0.10 |

When the founder writes copy like "Earn 500 DDollar for shipping a build post", the user reads it as "about 50 cents of AI value for doing real work" — grounded, not arbitrary. That's the goal.

---

## 9. Implementation map (existing → proposed)

Where each piece of the economy lives in the codebase today, and what's missing.

| Concern | File today | Status | Proposed change |
|---------|-----------|--------|-----------------|
| Award (earn) | `apps/api/src/ddollar/reward-engine.service.ts` | ✅ Shipped | Add tier-specific daily cap lookup |
| Spend | `apps/api/src/ddollar/spending-engine.service.ts` | ✅ Shipped | Add cache-hit measurement to AI spend |
| Two-ledger invariants | `apps/api/src/ddollar/ddollar-ledger.logic.ts` | ✅ Shipped | None — clean |
| Anti-abuse | `apps/api/src/ddollar/anti-abuse.service.ts` | 🚧 Minimal | Add IP / device / cluster detection (§6) |
| Action keys | `apps/api/src/ddollar/ddollar.constants.ts` | ✅ Partial | Add `WALL_REPLY`, `WALL_ENGAGEMENT_BONUS`, `PROJECT_FOLLOW_REWARD`, `VALIDATION_BOTH` |
| Tier check | `apps/api/src/founder-os/builder-score.service.ts` | ✅ Shipped | Rename `PARASITE` → `VISITOR` in user-facing copy only; keep enum value for now |
| Daily token cap (Tier 1) | env `PARASITE_DAILY_TOKEN_CAP=25000` | ⚠ Too low | Raise to 50000 (matches $0.15/day/DAU budget) |
| Daily token cap (Tier 2) | env `BUILDER_DAILY_TOKEN_CAP=500000` | ⚠ Becomes unlimited | Replace with fair-use throttle; Doxxed Builders have no hard cap |
| Doxxing application inbox | `/admin/applications` (existing scout inbox) | ✅ Shipped | Add `FOUNDER_DOXXING` application type alongside existing `LISTING_APPROVAL` |
| Per-request DDollar cost | `apps/api/src/ai-proxy/ai-proxy.constants.ts` (`DDOLLAR_COST_PER_REQUEST`) | ✅ Reasonable | No change |
| Points constant map | `packages/utils/src/reputation-points.ts` (`POINTS`) | ⚠ Miscalibrated | Apply §4 "Existing rates that need to come down" |
| Marketplace fee | `MARKETPLACE_TREASURY_FEE_BPS = 1000` (10%) | ✅ Reasonable | No change |
| Airdrop snapshot | — | 🚧 None | Phase 7+ — separate spec |
| Token launch whitelist | `docs/DDOLLAR-LAUNCH-ALLOCATION-PROPOSAL.md` | 🚧 Spec'd | Build at Phase 7+ |

---

## 10. Open questions

| # | Question | Default proposal | Needs founder input |
|---|----------|------------------|---------------------|
| 1 | DDollar decay for inactive users? | No — lifetime is permanent | ✅ |
| 2 | Should marketplace purchases reward the **seller** in lifetime DDollar (revenue = contribution)? | Yes | ✅ |
| 3 | Should AI overage beyond the daily cap auto-throttle (slow tier) or hard-stop? | Auto-throttle to cheap model | ✅ |
| 4 | Referral payout timing: on invitee signup, or on invitee becoming Verified Builder? | On Verified Builder conversion | ✅ |
| 5 | Founder-set supporter splits at launch: what's the UI? Founder specifies % per supporter? Top-N automatic? | Founder specifies top-N with % | ✅ |
| 6 | Cluster detection threshold: how aggressive? | Conservative — flag for review, don't auto-zero | ✅ |
| 7 | Should Tier 1 Visitors be able to earn the project-follow reward (25 DDollar to founder) or only Tier 2? | Both tiers — follow is cheap, real signal | ✅ |
| 8 | Daily login streak: linear bonus or just preservation? | Just preservation (no bonus, just no decay) | ✅ |
| 9 | Founder video: minimum length? minimum words spoken? AI-transcribed for content check? | 30s min, AI transcription for content-vs-project consistency | ✅ |
| 10 | Airdrop formula: linear (DDollar = share) or weighted (e.g. square-root to cap whales)? | Linear for v1, revisit at launch | ✅ |

---

## 11. Change log

| Date | Author | Change |
|------|--------|--------|
| 2026-07-07 | Strategy doc | Initial draft synthesizing founder billing Q&A + four tensions |
| 2026-07-07 | Strategy doc | Refined after founder follow-up: Tier 1 cap 200K → **50K**, tier names → **Visitor / Doxxed Builder**, Tier 2 cap → unlimited with fair-use throttle, doxxing flow routed through existing `/admin/applications` inbox. |
