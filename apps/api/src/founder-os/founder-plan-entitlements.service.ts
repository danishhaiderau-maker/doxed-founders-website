import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { FOUNDER_FREE_MANAGED_TOKEN_CAP } from './founder-free.config';

export const FOUNDER_BUILDER_MONTHLY_PRICE_CENTS = 3_500;
export const FOUNDER_BUILDER_WEEKLY_WEIGHTED_UNITS = 5_000_000;
export const FOUNDER_BILLING_GRACE_DAYS = 3;

export type FounderPlanName = 'free' | 'builder' | 'team';

export type FounderPlanEntitlement = {
  plan: FounderPlanName;
  quotaOwnerKey: string;
  weeklyWeightedUnitCap: number;
  currentPeriodStart: string | null;
  currentPeriodEnd: string | null;
  priceCentsMonthly: number | null;
  teamId: string | null;
  teamName: string | null;
  teamRole: 'owner' | 'admin' | 'member' | null;
  coordination: boolean;
  remoteControl: boolean;
  rolesAndAudit: boolean;
  requiresXVerification: boolean;
};

type SubscriptionRow = {
  tier: string;
  status: string;
  currentPeriodStart: Date;
  currentPeriodEnd: Date;
};

function active(subscription: SubscriptionRow | null | undefined, now: Date): boolean {
  if (!subscription || subscription.currentPeriodStart > now) return false;
  if (subscription.status === 'ACTIVE') return subscription.currentPeriodEnd > now;
  if (subscription.status !== 'PAST_DUE') return false;
  const graceEndsAt = new Date(
    subscription.currentPeriodEnd.getTime() + FOUNDER_BILLING_GRACE_DAYS * 86_400_000,
  );
  return graceEndsAt > now;
}

@Injectable()
export class FounderPlanEntitlementsService {
  constructor(private readonly prisma: PrismaService) {}

  async resolve(userId: string, now = new Date()): Promise<FounderPlanEntitlement> {
    const direct = await this.prisma.founderPlanSubscription.findUnique({
      where: { userId },
    });
    if (active(direct, now) && direct?.tier === 'BUILDER') {
      return {
        plan: 'builder',
        quotaOwnerKey: `user:${userId}`,
        weeklyWeightedUnitCap: FOUNDER_BUILDER_WEEKLY_WEIGHTED_UNITS,
        currentPeriodStart: direct.currentPeriodStart.toISOString(),
        currentPeriodEnd: direct.currentPeriodEnd.toISOString(),
        priceCentsMonthly: FOUNDER_BUILDER_MONTHLY_PRICE_CENTS,
        teamId: null,
        teamName: null,
        teamRole: null,
        coordination: true,
        remoteControl: true,
        rolesAndAudit: false,
        requiresXVerification: false,
      };
    }

    const membership = await this.prisma.founderPlanTeamMember.findFirst({
      where: {
        userId,
        team: {
          subscription: {
            is: {
              status: 'ACTIVE',
              tier: 'TEAM',
              currentPeriodStart: { lte: now },
              currentPeriodEnd: { gt: now },
            },
          },
        },
      },
      include: { team: { include: { subscription: true } } },
      orderBy: { createdAt: 'asc' },
    });
    const teamSubscription = membership?.team.subscription;
    if (membership && active(teamSubscription, now) && teamSubscription?.tier === 'TEAM') {
      return {
        plan: 'team',
        quotaOwnerKey: `team:${membership.team.id}`,
        weeklyWeightedUnitCap: membership.team.weeklyWeightedUnitCap,
        currentPeriodStart: teamSubscription.currentPeriodStart.toISOString(),
        currentPeriodEnd: teamSubscription.currentPeriodEnd.toISOString(),
        priceCentsMonthly: null,
        teamId: membership.team.id,
        teamName: membership.team.name,
        teamRole: String(membership.role).toLowerCase() as 'owner' | 'admin' | 'member',
        coordination: true,
        remoteControl: true,
        rolesAndAudit: true,
        requiresXVerification: false,
      };
    }

    return {
      plan: 'free',
      quotaOwnerKey: `user:${userId}`,
      weeklyWeightedUnitCap: FOUNDER_FREE_MANAGED_TOKEN_CAP,
      currentPeriodStart: null,
      currentPeriodEnd: null,
      priceCentsMonthly: 0,
      teamId: null,
      teamName: null,
      teamRole: null,
      coordination: false,
      remoteControl: false,
      rolesAndAudit: false,
      requiresXVerification: true,
    };
  }
}
