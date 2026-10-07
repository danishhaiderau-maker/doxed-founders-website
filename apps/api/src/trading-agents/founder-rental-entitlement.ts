import { FounderPresenceLevel, UserRole } from '@prisma/client';
import type { PrismaService } from '../prisma/prisma.service';

/**
 * Founder/operator free-rental entitlement.
 *
 * The platform owner (the account configured as `DDOLLAR_GATE_OPERATOR_USER_ID`)
 * and any verified founder are entitled to run Bitfinex live-copy on their own
 * trading agent WITHOUT the weekly DDollar rental, permanently (no expiry),
 * so they can arm and test live copy. This is a first-class,
 * role-derived entitlement — not a per-account fee bypass and not a global
 * monetization toggle. Every grant writes a zero-DDollar `PointLedger` entry
 * (actionKey `FOUNDER_RENTAL_GRANT`) so the exemption is auditable.
 *
 * The normal `$2,000 DDollar / week` hire + renew path is unchanged for every
 * other account.
 */

/**
 * Founder/operator free rental is permanent (no expiry). `freeRentalExpiresAt`
 * returns `null` to mean "never expires": the instance `expiresAt` stays NULL
 * and the execution worker treats NULL as non-expiring
 * (`hireExpiryBlocksNewLiveEntries(null)` returns false).
 */

/** Presence levels that qualify a founder as "verified" for free rental. */
const VERIFIED_FOUNDER_PRESENCE_LEVELS: readonly FounderPresenceLevel[] = [
  FounderPresenceLevel.VERIFIED_BUILDER,
  FounderPresenceLevel.TRANSPARENT_FOUNDER,
  FounderPresenceLevel.PROVEN_FOUNDER,
];

export type FounderRentalEntitlementReason = 'operator' | 'admin' | 'verified_founder';
export type FounderRentalEntitlement = {
  entitled: boolean;
  reason: FounderRentalEntitlementReason | null;
};

type EntitlementPrisma = Pick<PrismaService, 'user' | 'founder' | 'pointLedger'>;

export function freeRentalExpiresAt(): null {
  return null;
}

/**
 * Resolve whether a user is entitled to free founder rental. Ordered,
 * self-documenting checks:
 *   1. operator  — the configured `DDOLLAR_GATE_OPERATOR_USER_ID` (platform owner)
 *   2. admin     — `UserRole.ADMIN`
 *   3. verified founder — a `Founder` row in a verified presence tier
 */
export async function resolveFounderRentalEntitlement(
  prisma: EntitlementPrisma,
  userId: string,
): Promise<FounderRentalEntitlement> {
  const operatorId = (process.env.DDOLLAR_GATE_OPERATOR_USER_ID ?? '').trim();
  if (operatorId && operatorId === userId) {
    return { entitled: true, reason: 'operator' };
  }

  const [user, founder] = await Promise.all([
    prisma.user.findUnique({ where: { id: userId }, select: { role: true } }),
    prisma.founder.findUnique({ where: { userId }, select: { presenceLevel: true } }),
  ]);

  if (user?.role === UserRole.ADMIN) {
    return { entitled: true, reason: 'admin' };
  }
  if (founder && VERIFIED_FOUNDER_PRESENCE_LEVELS.includes(founder.presenceLevel)) {
    return { entitled: true, reason: 'verified_founder' };
  }
  return { entitled: false, reason: null };
}

/**
 * Record an auditable, zero-DDollar grant in the point ledger. Does not touch
 * any balance. `amount: 0` guarantees no DDollar is ever moved by this path.
 */
export async function recordFounderRentalGrant(
  prisma: Pick<PrismaService, 'pointLedger'>,
  params: {
    userId: string;
    agentSlug: string;
    agentName: string;
    reason: FounderRentalEntitlementReason;
  },
): Promise<void> {
  await prisma.pointLedger.create({
    data: {
      userId: params.userId,
      amount: 0,
      actionKey: 'FOUNDER_RENTAL_GRANT',
      label: `Founder free rental — ${params.agentName} (${params.agentSlug}) · ${params.reason} · no DDollar deducted`,
    },
  });
}
