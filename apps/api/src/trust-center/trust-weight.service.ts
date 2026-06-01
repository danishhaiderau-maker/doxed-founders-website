import { Injectable } from '@nestjs/common';
import { computeTrustWeight } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class TrustWeightService {
  constructor(private readonly prisma: PrismaService) {}

  async forUser(userId: string): Promise<number> {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      include: { oauthAccounts: { select: { provider: true } } },
    });
    if (!user) return 1;

    const verifiedAccount = Boolean(
      user.emailVerified ||
        user.twitterHandle?.trim() ||
        user.oauthAccounts.some((a) => a.provider === 'google' || a.provider === 'twitter'),
    );

    const accountAgeDays = Math.floor(
      (Date.now() - user.createdAt.getTime()) / (24 * 60 * 60 * 1000),
    );

    return computeTrustWeight({
      verifiedAccount,
      contributorLevel: user.contributorLevel,
      reputationPoints: user.reputationPoints,
      accountAgeDays,
    });
  }
}
