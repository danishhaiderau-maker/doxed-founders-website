# ChatGPT Consultation Brief — Doxxed Crypto Founder Graduation Platform

**To:** ChatGPT (product / regulatory consultation)  
**From:** Cursor / dev team building [doxxedcrypto.digital](https://doxxedcrypto.digital)  
**Date:** July 2026  
**Safe to share:** No secrets, API keys, or credentials in this document.

---

## Context

We (the Cursor / dev team) have read your recommendations and our internal **PLATFORM-ARCHITECTURE-AUDIT** bundle (`docs/PLATFORM-ARCHITECTURE-AUDIT.md`, summary paste bundle, and **ADDENDUM-V2**). We also incorporated your feedback into **RAISE-ROOM-VALIDATION-VISION-SPEC** and a new **FOUNDER-GRADUATION-BUILD-PLAN**. ChatGPT sent **Architecture Review v2** on 6 July 2026; Cursor incorporated it in **[ARCHITECTURE-REVIEW-V2-RESPONSE.md](./ARCHITECTURE-REVIEW-V2-RESPONSE.md)**; **Phase 1.5** adds Regulatory Engine before token work.

This brief explains what we understood, what we agree with, what we will build in four phases, what we will deliberately *not* build yet, and **10 specific questions** we want you to review before we ship Phase 1–2.

---

## What we understood (your refined vision)

### Inverted stack — trust first, not launch first

The product stack flows:

**Community → Trust → Founder OS → Raise Room → Launch Qualification → Token Infrastructure → Liquidity → Trading**

We reject a launch-first or Pump.fun-style path. Founders earn the right to graduate; the platform is closer to **Product Hunt + GitHub + AngelList + Kickstarter + YC** than to anon memecoin deployers.

### Raise Room is a mandatory path

Pipeline:

**Founder → List Project → Trust Review → Community Validation → Raise Room → Launch Qualification → Launch**

Nobody launches immediately. Eligibility is earned through weighted community signals, paper conviction, and gate thresholds.

### Project Maturity stages

**IDEA → BUILDING → VALIDATED → COMMUNITY → READY → LAUNCHING → TRADING → GROWING**

These replace launchpad-centric UX. We map from legacy `ProjectLifecycleStage` in code until a schema migration.

### Rename: Token Launch → Founder Graduation

“Graduation” is an **achievement** unlocked when launch qualification gates pass. Composite **Founder Score** (Builder + Trust + Community + Scout + Build + Delivery) is shown at unlock — not a one-click mint button.

### DDollar reframe: Proof of Contribution

DDollar is earned through reviews, scouting, building, and validation — not only spent on credits. Paper Raise Room and allocation registration consume paper dollars / DDollar in simulation; real economic effect waits for counsel-gated phases.

### Community allocation buckets (weighted, not equal)

| Bucket | Weight |
|--------|--------|
| Validators | 25% |
| Scouts | 20% |
| Builders | 20% |
| Early Followers | 15% |
| Paper Raise | 10% |
| Bug Hunters | 10% |

We explicitly reject equal 10% airdrops to all active users (old TOKEN-LAUNCH proposal §4).

### Launch Qualification gates (example thresholds)

| Gate | Threshold |
|------|-----------|
| Min project followers | ≥ 25 |
| Paper conviction | ≥ $10,000 simulated |
| Community validation score | ≥ 70% weighted |
| AI / scam pass | No active investigation |
| Identity | X linked or verified email + wallet |
| Founder Launch Score | ≥ 65 composite |

### Founder Exchange (not “Doxxed DEX”)

Only **graduated** projects list. Trust metadata (validation history, founder identity, regulatory class) visible on pair pages. Jupiter-routed swaps in Phase 3 with tier-based platform fees.

### Regulatory Engine

Founder questionnaire classifies:

- Community Project  
- Utility Token  
- Governance Token  
- Capital Raise  
- Restricted  

Feature gating per class (e.g. Capital Raise path requires **consult AU counsel** — we will not ship real raises without that).

### Australian legal separation (engineering framing)

- **Company:** community / software platform  
- **Blockchain:** settlement for user-initiated txs  
- **Users:** self-custody  

We will flag “consult AU counsel” on Capital Raise — this brief is not legal advice.

---

## What we agree with / what we already have (~38% vision coverage)

Per **RAISE-ROOM-VALIDATION-VISION-SPEC** audit:

| Area | Shipped today |
|------|----------------|
| **Raise Room paper engine** | `SimulatedRaise`, `RaiseAllocation`, `/raise-room` heatmap, 1% paper burn on commit |
| **Trust Center** | Weighted validation categories, investigations, scout voting, Top Scouts |
| **List Project** | Listing application → vote → admin approve → `Project` |
| **Founder OS** | Copilot, build queue, CEO inbox, Agent Bus, Founder Node pairing |
| **DDollar / points** | `PointLedger`, `User.reputationPoints`, builder tiers, engagement rewards |
| **Paper trading** | Virtual USD portfolio, public portfolios |
| **Wallet linking** | Solana verify (no platform custody of user funds) |
| **Launch readiness (partial)** | `computeLaunchpadRequirements`, Startup Genome, build logs + video gates |
| **Scout primitives** | `EarlyScoutRecord`, trust weight labels, scout markets |

| Gap (your vision) | Status |
|-------------------|--------|
| Founder Launch Score composite | Not unified — planned Phase 1 |
| Validation signals on Raise tab | Trust Center separate — Phase 1 |
| Weighted allocation buckets + Founder Points ledger | Flat 10% today — Phase 1–2 |
| Launch eligibility bar (6 gates) | Partial — Phase 1 |
| Regulatory Engine questionnaire | Greenfield — Phase 2 |
| Founder Exchange / Jupiter | Greenfield — Phase 3 |
| On-chain raise / custody | Explicitly out of scope until Phase 4 + counsel |

---

## What we will build

### Phase 1 (weeks 1–4) — Validation layer only

**Regulatory layer:** Community / paper only — no tokens, no real money.

| Deliverable | Description |
|-------------|-------------|
| **RR-001** | Proof Raise + Founder Graduation rebrand; remove ICO / launchpad copy on Raise surfaces |
| **RR-002** | Founder Launch Score (read path) — 6 components, API + project/heatmap display |
| **RR-003** | Validation signals UI on Raise tab — weighted buttons, community score % |
| **RR-004** | Conviction score + enriched trending cards on `/raise-room` |
| **RR-005** | Launch eligibility bar — 6 gates; Proof Raise CTA disabled until `allPassed` |
| **RR-006** | `ProjectMaturity` enum + badge (IDEA → GROWING) |
| **RR-007** | DDollar copy pass — Proof of Contribution framing on `/ddollar` and Raise Room |
| **Copy / legal** | Allocation registration disclaimers; AU-safe language |

**Phase 1 guards:** API rejects real SOL/USDC raise endpoints; no wallet raise CTAs.

### Phase 2 (weeks 5–10) — Token metadata + regulatory

| Deliverable | Description |
|-------------|-------------|
| **RR-008** | Regulatory Engine v1 — questionnaire, classification, feature flags |
| **RR-009** | Founder Points ledger + weighted bucket export (25/20/20/15/10/10) |
| **RR-010** | Bronze / Silver / Gold tier choice at Proof Raise open |
| **Scout dashboard** | Discoveries, success rate, points by bucket |
| **Vesting schedule (off-chain)** | 25% immediate / 75% × 12mo documentation + schema |
| **Merkle export** | Snapshot CSV → claim-ready format (founder mints off-platform) |

### Phase 3 (weeks 10–16) — Founder Exchange

| Deliverable | Description |
|-------------|-------------|
| Jupiter swap UI | Project page + terminal; tier-based platform fee (0 / 5 / 10 bps) |
| Graduated-only listing | Trust metadata on pair pages |
| Paper trading mirror | Live wallet optional; simulation default |
| DexScreener ingest | Existing path extended for graduated projects |

### Phase 4 (counsel-gated) — Capital path

| Deliverable | Description |
|-------------|-------------|
| Real raise vault | SOL / stablecoin — **only after AU fintech counsel sign-off** |
| Geo / KYC | Jurisdiction filters per Regulatory Engine class |
| On-chain DDollar | Optional SPL — separate decision |

---

## What we will NOT build yet

- Solana token contracts or platform custodial mint  
- Holding or escrow of user funds (real SOL/USDC)  
- Real capital raises, presales, or ICO mechanics  
- Equal community airdrops  
- Pump.fun-style anon deploy  
- Investment advice or guaranteed allocation copy  
- Full iOS native app (PWA interim continues)  
- Legal opinions — we will recommend **consult AU counsel** for Capital Raise class  

---

## Questions for your review

1. **Mandatory pipeline UX:** Should Launch Qualification gates be visible to *scouts* on project pages before founders pass them (transparency vs. gaming)?

2. **Founder Graduation naming:** Is “Founder Graduation” clear to non-crypto founders, or should we pair it with a subtitle (“allocation registration unlock”)?

3. **Regulatory Engine UX:** Should classification be **founder self-attestation** with admin override, or **admin-only** after questionnaire — for AU liability?

4. **Capital Raise class:** What minimum questionnaire fields would you expect before we even *show* a “request legal review” CTA (token utility, revenue, investor geography, etc.)?

5. **Proof of Contribution:** We plan to award DDollar for Trust Center reviews, scout accuracy, build posts, and Raise Room validation — should **spending** DDollar on Raise Room escrow (Phase 2) reduce reputation score or use a separate “committed” balance?

6. **Weighted buckets:** Validators 25% vs Paper Raise 10% — does this under-weight capital conviction vs. Product Hunt-style “would you pay” signals?

7. **Founder Exchange listing:** Should non-graduated projects appear in Discover with a “not yet graduated” badge, or be hidden from trading-adjacent surfaces entirely?

8. **Australian framing:** For Phase 1 paper-only, is “allocation registration” + “simulated paper conviction” sufficient distance from CSF / managed investment scheme language?

9. **Competitive positioning:** We cite Product Hunt / GitHub / AngelList — any risk of implying securities crowdfunding when Raise Room shows dollar-denominated paper totals?

10. **Phase ordering:** You prioritized trust before token infrastructure — we kept Jupiter in Phase 3 *after* Regulatory Engine. Would you move Regulatory Engine before *any* public token metadata (name/ticker preview)?

---

## How to respond

Please critique Phase 1–2 scope, AU product framing (not legal advice), Regulatory Engine UX, and bucket weights. Flag anything that still sounds like launch-first or securities marketing. We will iterate the build plan and ship RR-001 through RR-005 in the next sprint.

**Repo docs:** `docs/FOUNDER-GRADUATION-BUILD-PLAN.md`, `docs/PLATFORM-ARCHITECTURE-AUDIT-ADDENDUM-V2.md`, `docs/RAISE-ROOM-VALIDATION-VISION-SPEC.md`

**Production (read-only):** https://doxxedcrypto.digital · Raise Room: `/raise-room`

---

*This document is product engineering context only. It is not legal, financial, or investment advice.*
