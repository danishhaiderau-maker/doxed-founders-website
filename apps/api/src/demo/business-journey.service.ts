import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { DdollarRuntimeService } from '../ddollar/ddollar-runtime.service';
import { isDdollarRuntimeEnabled } from '../ddollar/ddollar.constants';
import { AirdropService } from '../airdrop/airdrop.service';
import { demoUserEmail, demoUserWhere } from './demo.constants';

export type BusinessJourneyResult = {
  passed: boolean;
  detail: string;
};

@Injectable()
export class BusinessJourneyService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly ddollarRuntime: DdollarRuntimeService,
    private readonly airdrop: AirdropService,
  ) {}

  async runGoldenDdollarJourney(): Promise<BusinessJourneyResult> {
    const founderUser = await this.prisma.user.findFirst({
      where: { email: demoUserEmail(1, 'founder') },
      select: { id: true, reputationPoints: true, lifetimeContributionEarned: true },
    });
    if (!founderUser) {
      return { passed: false, detail: 'Demo founder user missing — run seed first' };
    }

    const before = { ...founderUser };
    const awardAmount = 25;

    if (isDdollarRuntimeEnabled()) {
      await this.ddollarRuntime.award(founderUser.id, awardAmount, 'SCOUT_EARLY');
      await this.ddollarRuntime.purchaseMarketplace(
        founderUser.id,
        10,
        'demo-golden-journey-hire',
        '[Smoke] Golden journey marketplace spend',
      );
    } else {
      await this.prisma.user.update({
        where: { id: founderUser.id },
        data: {
          reputationPoints: { increment: awardAmount - 10 },
          lifetimeContributionEarned: { increment: awardAmount },
        },
      });
      await this.prisma.marketplaceLedgerEntry.create({
        data: {
          userId: founderUser.id,
          listingKey: 'demo-golden-journey-hire',
          amountDdollar: -10,
          label: '[Smoke] Golden journey marketplace spend',
        },
      });
      await this.prisma.founderTreasuryLedgerEntry.create({
        data: {
          userId: founderUser.id,
          amountDdollar: 1,
          actionKey: 'TREASURY_FEE',
          label: '[Smoke] Golden journey treasury fee',
        },
      });
    }

    const after = await this.prisma.user.findUnique({
      where: { id: founderUser.id },
      select: { reputationPoints: true, lifetimeContributionEarned: true },
    });
    if (!after) return { passed: false, detail: 'User missing after journey' };

    const marketplaceOk =
      (await this.prisma.marketplaceLedgerEntry.count({
        where: { userId: founderUser.id, listingKey: 'demo-golden-journey-hire' },
      })) >= 1;
    const treasuryOk =
      (await this.prisma.founderTreasuryLedgerEntry.count({
        where: { userId: founderUser.id, actionKey: 'TREASURY_FEE' },
      })) >= 1;

    const board = await this.airdrop.getMe(founderUser.id);
    const scored = board.builderScore > 0 || board.reputationPoints > 0;

    const lifetimeDelta = after.lifetimeContributionEarned - before.lifetimeContributionEarned;
    const lifetimeOk =
      isDdollarRuntimeEnabled() ? lifetimeDelta === awardAmount : lifetimeDelta >= awardAmount;
    const spendableOk = after.reputationPoints >= before.reputationPoints + awardAmount - 15;

    const passed =
      lifetimeOk &&
      spendableOk &&
      after.lifetimeContributionEarned >= after.reputationPoints &&
      marketplaceOk &&
      treasuryOk &&
      scored;

    return {
      passed,
      detail: passed
        ? `Journey OK — earn +${awardAmount}, spend 10, lifetime=${after.lifetimeContributionEarned}, builderScore=${board.builderScore}`
        : `Journey failed — lifetimeΔ=${lifetimeDelta}, marketplace=${marketplaceOk}, treasury=${treasuryOk}, scored=${scored}`,
    };
  }

  async assertDemoUsersExist(): Promise<boolean> {
    const count = await this.prisma.user.count({ where: demoUserWhere() });
    return count >= 10;
  }
}
