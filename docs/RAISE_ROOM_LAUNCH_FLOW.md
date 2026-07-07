# Founder OS — Phase 7+ Token Launch Flow

| Field | Value |
|-------|-------|
| **Status** | DRAFT SPEC — NOT FOR IMMEDIATE BUILD |
| **Authored** | 2026-07-07 |
| **Ships** | Phase 7+ |
| **Owners** | Founder |

> This is the flagship revenue flow captured from the founder's description on 2026-07-07. It is a decision-capture document, not a build ticket. The platform earns the right to build this only after the kernel matures — see [`PRODUCT.md`](./PRODUCT.md) §6 roadmap and §10 below. Pairs with [`BILLING.md`](./BILLING.md) §6 (revenue thesis) and [`DDOLLAR_ECONOMY.md`](./DDOLLAR_ECONOMY.md) §5 (DDollar spend options).

---

## 1. Overview

The flagship revenue flow. The platform is free for founders; revenue comes from this flow.

When a Doxxed Builder is ready, they click **"I'm Ready — Launch My Token"** in Founder OS → Raise Room opens → community commits DDollar → threshold met → Solana mint → DEX listing → 0.1% fee on every trade. Until the kernel is mature (Phase 7+), this button is **disabled and shows a waitlist**. The kernel earns the right to ask founders to launch; the launch flow never ships ahead of that.

---

## 2. Two CTA buttons in Founder OS shell

Both buttons appear in the Phase 2 thin Workspace shell. They activate at different times.

| Button | Visible from | Active from | Action when disabled |
|--------|--------------|-------------|----------------------|
| **Get Doxxed** | Phase 2 | Phase 2 | Submits GitHub + Twitter + 60s video to `/admin/applications` |
| **I'm Ready — Launch My Token** | Phase 2 | Phase 7+ | "Token launch unlocks at Phase 7. Join the waitlist." |

The Doxxing flow itself is owned by Auth + Vault and the existing `/admin/applications` inbox — see [`BILLING.md`](./BILLING.md) §4 for the verification chain (Twitter auth = cheap entry; founder video = the real trust signal).

---

## 3. Pre-launch checklist (Phase 7+ entry gate)

Before the launch button activates for a given Doxxed Builder, the founder must have:

- ✅ Doxxed Builder status (GitHub + Twitter + approved founder video)
- ✅ At least 1 shipped build post linked to a real commit
- ✅ A completed Founder OS project record (tagline, description, deck/video)
- ✅ Twitter handle entered
- ✅ Project category selected (DeFi / Infra / AI / Consumer / Other)

---

## 4. Raise Room — listing phase

The project enters Raise Room in a "listing" state.

- Project card appears in Raise Room feed alongside other listing/in-progress launches
- Founder can post updates (build posts count toward builder score)
- Community can pledge DDollar to signal conviction
- 100,000 DDollar pledged = the threshold to unlock token release

---

## 5. The 100K DDollar threshold

This is the critical anti-rug mechanic. The founder cannot unilaterally release the token. The community must pledge ≥100,000 DDollar to the project first.

- Pledges are not spendable while locked — they sit in escrow (`TokenLaunchPledge` table)
- Each unique Doxxed Builder can pledge (Visitors cannot)
- If the project fails to launch within 90 days, pledges auto-refund
- If the founder abandons the project, pledges auto-refund after 90 days of inactivity

This is the same escrow pattern spec'd in [`DDOLLAR_ECONOMY.md`](./DDOLLAR_ECONOMY.md) §5 for token launch whitelist commits — extended to the broader Raise Room pledge flow.

---

## 6. Token release

When the 100K DDollar threshold is met:

1. Founder clicks **"Release Token"** (was previously locked)
2. Solana token mint executes (platform-sponsored or founder-sponsored)
3. **5% of token supply is reserved for DDollar pledgers**, distributed pro-rata to their pledge amount
4. Remaining 95% split per founder's choices (liquidity pool, team, treasury, airdrop)
5. Liquidity pool created on chosen DEX (Meteora preferred, Pump.fun fallback)
6. Token listed, trading opens

---

## 7. DEX integration (separate page)

The DEX lives at `/dex` (separate from Raise Room):

- Everyone connects Solana wallet (Phantom — already in codebase via `WalletConnection` model)
- Anyone can swap (no Doxxing required to trade, only to launch)
- Fee structure:
  - **0.5%** of every trade goes to platform
  - (Optional) **5%** of token supply allocated to pledgers at launch, distributed pro-rata
- Active launches surface in a "Trending" panel
- DEXScreener picks up automatically once liquidity is on-chain

---

## 8. Active launch page

While a token is in its initial bonding curve / community-building phase:

- Real-time contribution counter (SOL raised)
- Founder updates feed (linked from Raise Room build posts)
- Community chat (DDollar-earning per the inverted reward principle — see [`DDOLLAR_ECONOMY.md`](./DDOLLAR_ECONOMY.md) §2)
- Countdown to next milestone (liquidity lock, listing, etc.)

---

## 9. Post-launch founder requirements

Within 48h of listing, the founder must post:

- **DEXScreener link** (proves the token is real and tradable)
- **Twitter handle** (already collected at project creation)

Failure to post within 48h flags the project in admin tools. Pattern of "launch and ghost" → loss of Doxxed Builder status.

---

## 10. Phase 7+ entry criteria

The platform gets the green light to build Phase 7 when:

- ≥1,000 Doxxed Builders active monthly
- ≥10 founders have graduated a project from IDEA → LAUNCH_READY through the kernel
- Trust Center scam-flag false positive rate <5%
- Phantom wallet connect is production-hardened
- Legal review of DEX fee structure and geofencing complete

Until all five are true, the **I'm Ready — Launch My Token** button stays disabled and routes to the waitlist. See [`BILLING.md`](./BILLING.md) §6 for the full tension writeup.

---

## 11. Open questions

| # | Question | Default proposal |
|---|----------|------------------|
| 1 | Solana program: Pump.fun bonding curve vs Meteora DLMM vs custom? | Pump.fun fallback, Meteora preferred |
| 2 | Token supply standard: 1B always, or founder-chosen? | Founder-chosen with min 100M |
| 3 | Pledger 5% distribution: pro-rata to pledge, or capped per pledger? | Pro-rata, no cap |
| 4 | Refund window for failed launches: 90 days, 180, longer? | 90 days |
| 5 | Geofencing: which jurisdictions blocked from DEX? | Needs legal input |
| 6 | Secondary trading fee: flat 0.5% or tiered? | Flat 0.5% for v1 |
| 7 | Founder "launch and ghost" detection threshold? | 48h no DexScreener link → flag |
| 8 | Airdrop to DDollar pledgers — when, how much? | 5% at launch, pro-rata |

---

## Change log

| Date | Change |
|------|--------|
| 2026-07-07 | Initial spec captured from founder description. |
