import { BadRequestException, Injectable, NotFoundException } from '@nestjs/common';
import {
  POINTS,
  REFERRAL_REWARD_BLUE_VERIFIED,
  REFERRAL_REWARD_STANDARD,
  REFERRAL_RULES,
  buildReferralCodeSeed,
  referralSharePath,
  userHasTwitterConnected,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { PointsService } from '../points/points.service';

const ADMIN_PLATFORM_HANDLE = 'Founder · Platform';

export type ReferralSummary = {
  code: string;
  sharePath: string;
  referredCount: number;
  pendingCount: number;
  earnedDdollar: number;
  rules: typeof REFERRAL_RULES;
  rewardStandard: number;
  rewardBlueVerified: number;
  refereeBlueBonus: number;
};

@Injectable()
export class ReferralService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly points: PointsService,
  ) {}

  async ensureReferralCode(userId: string): Promise<string> {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: { referralCode: true },
    });
    if (user?.referralCode) return user.referralCode;

    for (let attempt = 0; attempt < 40; attempt++) {
      const candidate = buildReferralCodeSeed(userId, attempt);
      const taken = await this.prisma.user.findUnique({
        where: { referralCode: candidate },
        select: { id: true },
      });
      if (taken) continue;
      const updated = await this.prisma.user.update({
        where: { id: userId },
        data: { referralCode: candidate },
        select: { referralCode: true },
      });
      return updated.referralCode!;
    }

    const fallback = buildReferralCodeSeed(`${userId}-${Date.now()}`, 99);
    const updated = await this.prisma.user.update({
      where: { id: userId },
      data: { referralCode: fallback },
      select: { referralCode: true },
    });
    return updated.referralCode!;
  }

  async getSummary(userId: string): Promise<ReferralSummary> {
    const code = await this.ensureReferralCode(userId);

    const [referrals, awards] = await Promise.all([
      this.prisma.user.findMany({
        where: { referredByUserId: userId },
        select: {
          id: true,
          twitterHandle: true,
          oauthAccounts: { select: { provider: true } },
        },
      }),
      this.prisma.reputationAward.findMany({
        where: {
          userId,
          OR: [
            { actionKey: { startsWith: 'REFERRAL:' } },
            { actionKey: { startsWith: 'REFERRAL_BLUE:' } },
          ],
        },
        select: { actionKey: true, amount: true },
      }),
    ]);

    let pendingCount = 0;
    for (const ref of referrals) {
      const paid = awards.some((a) => a.actionKey.endsWith(`:${ref.id}`));
      if (!paid && !userHasTwitterConnected(ref)) pendingCount += 1;
    }

    const earnedDdollar = awards.reduce((sum, row) => sum + row.amount, 0);

    return {
      code,
      sharePath: referralSharePath(code),
      referredCount: referrals.length,
      pendingCount,
      earnedDdollar,
      rules: REFERRAL_RULES,
      rewardStandard: REFERRAL_REWARD_STANDARD,
      rewardBlueVerified: REFERRAL_REWARD_BLUE_VERIFIED,
      refereeBlueBonus: POINTS.X_BLUE_VERIFIED,
    };
  }

  /** Attach referral code to a new or existing user (idempotent). */
  async attachReferralCode(refereeId: string, rawCode?: string | null) {
    const code = rawCode?.trim().toUpperCase();
    if (!code) return;

    const referee = await this.prisma.user.findUnique({
      where: { id: refereeId },
      select: { referredByUserId: true },
    });
    if (!referee || referee.referredByUserId) return;

    const referrer = await this.prisma.user.findUnique({
      where: { referralCode: code },
      select: { id: true },
    });
    if (!referrer || referrer.id === refereeId) return;

    await this.prisma.user.update({
      where: { id: refereeId },
      data: { referredByUserId: referrer.id },
    });
  }

  /** Award referrer + referee bonuses when X login criteria are met. */
  async tryCompleteReferralRewards(
    refereeId: string,
    options?: { xVerified?: boolean; isNewSignup?: boolean },
  ) {
    const referee = await this.prisma.user.findUnique({
      where: { id: refereeId },
      select: {
        referredByUserId: true,
        xVerified: true,
        twitterHandle: true,
        oauthAccounts: { select: { provider: true } },
      },
    });
    if (!referee) return;

    const xVerified = options?.xVerified ?? referee.xVerified;
    if (options?.xVerified && !referee.xVerified) {
      await this.prisma.user.update({
        where: { id: refereeId },
        data: { xVerified: true, xVerifiedAt: new Date() },
      });
    }

    const hasTwitter = userHasTwitterConnected(referee);
    if (!hasTwitter) return;

    if (xVerified && options?.isNewSignup === true) {
      await this.points.awardOnce(refereeId, 'X_BLUE_VERIFIED', POINTS.X_BLUE_VERIFIED);
    }

    if (!referee.referredByUserId) return;

    const referrerId = referee.referredByUserId;
    const amount = xVerified ? REFERRAL_REWARD_BLUE_VERIFIED : REFERRAL_REWARD_STANDARD;
    const actionKey = xVerified ? 'REFERRAL_BLUE' : 'REFERRAL';
    await this.points.awardOnce(referrerId, `${actionKey}:${refereeId}`, amount);
  }

  async claimReferralCode(userId: string, rawCode: string) {
    const code = rawCode?.trim().toUpperCase();
    if (!code || code.length < 6) {
      throw new BadRequestException('Enter a valid referral code');
    }

    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: { referredByUserId: true, createdAt: true },
    });
    if (!user) throw new NotFoundException('User not found');
    if (user.referredByUserId) {
      throw new BadRequestException('You already used a referral code');
    }

    const ageMs = Date.now() - user.createdAt.getTime();
    if (ageMs > 7 * 24 * 60 * 60 * 1000) {
      throw new BadRequestException('Referral codes can only be applied within 7 days of signup');
    }

    await this.attachReferralCode(userId, code);
    await this.tryCompleteReferralRewards(userId);
    return this.getSummary(userId);
  }
}

export { ADMIN_PLATFORM_HANDLE };
