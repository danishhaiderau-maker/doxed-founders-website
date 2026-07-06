# DDollar Proof of Contribution — Two-Ledger Spec

DDollar (platform points) serves two distinct roles in Founder OS. Today the
schema partially supports this; Demo Mode illustrates the intended split.

## Two ledgers

| Ledger | Purpose | Mutability | User-facing label |
|--------|---------|------------|-------------------|
| **Spendable DDollar** | Pay for AI, Raise Room burns, platform actions | Decrements on spend | "Balance" / "DDollar" |
| **Lifetime Contribution Score** | Reputation, leaderboards, builder tier signals | Monotonic (never decreases) | "Contribution" / "Lifetime earned" |

**Proof of Contribution (PoC):** Lifetime score reflects durable value added to
the ecosystem (scouting, validation, build logs, helpful marks). Spendable
balance is a **withdrawable wallet** from that earned pool.

## Current schema mapping

| Concept | Field / table | Notes |
|---------|---------------|-------|
| Spendable balance | `User.reputationPoints` | Decremented by `PointsService.spend()` |
| Spend audit trail | `PointLedger` (negative rows) | `AI_SPEND`, etc. |
| Earn audit trail | `PointLedger` (positive rows) | `SCOUT_EARLY`, `FOUNDER_BUILD_POST`, … |
| Contributor level | `User.contributorLevel` | Derived from spendable balance today |
| Builder tier | `User.builderTier` | Separate gate for promo pool |

### Gap: no first-class lifetime field

There is **no** `User.lifetimeContributionEarned` column yet. Options:

1. **Recommended:** Add `lifetimeContributionEarned Int @default(0)` — increment
   on every `PointsService.award()`, never decrement on spend.
2. **Interim (Demo Mode):** Store in `User.notificationPrefs.lifetimeContributionEarned`
   for demo users only — not suitable for production.
3. **Derived:** `SUM(PointLedger.amount WHERE amount > 0)` — ignores pre-ledger
   history and manual admin adjustments.

## Demo Mode two-ledger pattern

When demo seed runs, each demo user gets:

- `reputationPoints` = spendable DDollar (after simulated AI spend)
- `notificationPrefs.lifetimeContributionEarned` = lifetime total (demo only)
- `PointLedger` rows showing earns (+) and a spend (-) entry

Example:

```
Spendable:  450 DDollar
Lifetime:  1847 contribution points
Ledger:    +739 scout, +646 validation, +462 build, -1397 AI spend
```

This matches the product story: users **earn** contribution, **spend** a subset.

## Required migration (future)

```prisma
model User {
  // ...
  reputationPoints            Int @default(0)  // spendable DDollar
  lifetimeContributionEarned  Int @default(0)  // PoC score — monotonic
}
```

### `PointsService` changes

```typescript
// award()
await prisma.user.update({
  data: {
    reputationPoints: { increment: amount },
    lifetimeContributionEarned: { increment: amount },
  },
});

// spend() — only decrement reputationPoints, NOT lifetime
```

### UI changes

- Account / wallet: show both numbers
- Leaderboards: rank by `lifetimeContributionEarned`
- Raise Room / AI gates: check `reputationPoints`

## PointLedger action keys (convention)

| actionKey | Ledger | Affects lifetime |
|-----------|--------|------------------|
| `SCOUT_EARLY` | earn | yes |
| `COMMUNITY_HELPFUL` | earn | yes |
| `FOUNDER_BUILD_POST` | earn | yes |
| `AI_SPEND` | spend | no (negative row, spendable only) |
| `RAISE_BURN` | spend | no |

## Verification

Demo smoke check `demo_user_ddollar_balance` asserts:

`lifetime >= spendable` for seeded demo users.

Full regression (future): unit tests on `award`/`spend` + migration backfill
from positive ledger sums.

## Related

- `docs/DEMO-MODE-AND-VERIFICATION.md`
- `apps/api/src/points/points.service.ts`
