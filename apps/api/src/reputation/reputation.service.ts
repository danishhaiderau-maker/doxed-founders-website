import { Injectable, NotFoundException } from '@nestjs/common';
import {
  computeAirdropAllocation,
  contributorLevelFromPoints,
  formatPublicAccountLabel,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';

export type ReputationLeaderboardEntry = {
  rank: number;
  userId: string;
  displayName: string;
  twitterHandle: string | null;
  reputationPoints: number;
  contributorLevel: number;
  airdropPoolPercent: number;
  supplyPercent: number;
  estimatedTokens: number;
  estimatedUsd: number;
};

export type ReputationMe = {
  userId: string;
  displayName: string;
  twitterHandle: string | null;
  reputationPoints: number;
  contributorLevel: number;
  rank: number | null;
  totalParticipants: number;
  totalPoints: number;
  airdropPoolPercent: number;
  supplyPercent: number;
  estimatedTokens: number;
  estimatedUsd: number;
};

@Injectable()
export class ReputationService {
  constructor(private readonly prisma: PrismaService) {}

  private async eligibleUsers() {
    return this.prisma.user.findMany({
      where: { banned: false, reputationPoints: { gt: 0 } },
      select: {
        id: true,
        name: true,
        email: true,
        twitterHandle: true,
        reputationPoints: true,
        contributorLevel: true,
      },
      orderBy: [{ reputationPoints: 'desc' }, { createdAt: 'asc' }],
    });
  }

  async getLeaderboard(limit = 50) {
    const users = await this.eligibleUsers();
    const totalPoints = users.reduce((sum, u) => sum + u.reputationPoints, 0);

    const entries = users.slice(0, limit).map((user, index) => {
      const allocation = computeAirdropAllocation(user.reputationPoints, totalPoints);
      return {
        rank: index + 1,
        userId: user.id,
        displayName: formatPublicAccountLabel(user.name, user.email),
        twitterHandle: user.twitterHandle,
        reputationPoints: user.reputationPoints,
        contributorLevel: user.contributorLevel,
        airdropPoolPercent: allocation.airdropPoolPercent,
        supplyPercent: allocation.supplyPercent,
        estimatedTokens: allocation.estimatedTokens,
        estimatedUsd: allocation.estimatedUsd,
      };
    });

    return {
      entries,
      totalParticipants: users.length,
      totalPoints,
    };
  }

  async getMe(userId: string): Promise<ReputationMe> {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: {
        id: true,
        name: true,
        email: true,
        twitterHandle: true,
        reputationPoints: true,
        contributorLevel: true,
        banned: true,
      },
    });

    if (!user || user.banned) {
      throw new NotFoundException('User not found');
    }

    const users = await this.eligibleUsers();
    const totalPoints = users.reduce((sum, u) => sum + u.reputationPoints, 0);
    const rankIndex = users.findIndex((u) => u.id === userId);
    const allocation = computeAirdropAllocation(user.reputationPoints, totalPoints);

    return {
      userId: user.id,
      displayName: formatPublicAccountLabel(user.name, user.email),
      twitterHandle: user.twitterHandle,
      reputationPoints: user.reputationPoints,
      contributorLevel:
        user.reputationPoints > 0
          ? user.contributorLevel
          : contributorLevelFromPoints(user.reputationPoints),
      rank: rankIndex >= 0 ? rankIndex + 1 : null,
      totalParticipants: users.length,
      totalPoints,
      airdropPoolPercent: allocation.airdropPoolPercent,
      supplyPercent: allocation.supplyPercent,
      estimatedTokens: allocation.estimatedTokens,
      estimatedUsd: allocation.estimatedUsd,
    };
  }
}
