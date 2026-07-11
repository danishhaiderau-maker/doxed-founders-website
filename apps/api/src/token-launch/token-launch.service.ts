import {
  BadRequestException,
  Injectable,
  Logger,
  NotFoundException,
} from '@nestjs/common';
import { TokenLaunchStatus } from '@prisma/client';
import { DDOLLAR_ACTIVITY_SPECS } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { DdollarEngineService } from '../founder-economics/ddollar-engine.service';
import { SolanaMintService } from './solana-mint.service';

/**
 * Token Launch orchestration — the Phase 8 flagship revenue flow.
 *
 * Flow (see docs/RAISE_ROOM_LAUNCH_FLOW.md):
 *   1. Founder creates a project → a TokenLaunch row is auto-created in
 *      PLEDGING status.
 *   2. Community pledges DDollar (escrowed via SpendingEngine). When total
 *      ≥ 100K, `pledgeThresholdMet` flips true and the founder can release.
 *   3. Founder clicks "Release Token" → Solana devnet mint executes,
 *      windowClosesAt = now + 15 days, status → WINDOW_OPEN.
 *   4. Community can commit (pledge more) during the 15-day window.
 *   5. Daily cron finalizes launches whose window expired → allocations
 *      computed pro-rata from the 5% pledge pool, status → LIVE.
 *   6. DEX stub opens for swaps. 0.1% fee accrues to PlatformTreasury.
 *
 * Anti-rig: a founder cannot unilaterally release the token. The 100K
 * community threshold must be met first.
 */
@Injectable()
export class TokenLaunchService {
  private readonly logger = new Logger(TokenLaunchService.name);

  /** DDollar pledge threshold to unlock token release. */
  static readonly PLEDGE_THRESHOLD = 100_000;
  /** Length of the community commitment window after release. */
  static readonly WINDOW_DAYS = 15;
  /** % of token supply reserved for pledgers (pro-rata). */
  static readonly PLEDGE_POOL_PERCENT = 5;
  /** Days of inactivity before pledges auto-refund (spec §5). */
  static readonly REFUND_INACTIVITY_DAYS = 90;
  /** Standard 1B supply. */
  static readonly DEFAULT_SUPPLY = 1_000_000_000;

  constructor(
    private readonly prisma: PrismaService,
    private readonly solanaMint: SolanaMintService,
    private readonly ddollarEngine: DdollarEngineService,
  ) {}

  // ─── Read paths ──────────────────────────────────────────────────────────

  /**
   * Get or create the TokenLaunch row for a project. Every project gets one
   * so the pledge leaderboard / progress surfaces work from day one.
   */
  async getOrCreateForProject(projectId: string) {
    const existing = await this.prisma.tokenLaunch.findUnique({
      where: { projectId },
      include: { pledges: { orderBy: { amount: 'desc' } } },
    });
    if (existing) return existing;

    const project = await this.prisma.project.findUnique({
      where: { id: projectId },
      select: { id: true, name: true },
    });
    if (!project) throw new NotFoundException(`Project ${projectId} not found`);

    return this.prisma.tokenLaunch.create({
      include: { pledges: { orderBy: { amount: 'desc' } } },
      data: {
        projectId,
        supply: BigInt(TokenLaunchService.DEFAULT_SUPPLY),
        pledgePoolPercent: TokenLaunchService.PLEDGE_POOL_PERCENT,
        status: TokenLaunchStatus.PLEDGING,
      },
    });
  }

  /**
   * Launch eligibility check — the surface that powers the frontend card.
   * Verifies the 100K DDollar pledge threshold + the founder-side pre-launch
   * checklist (Doxxed Builder, shipped build post, project record complete).
   */
  async checkLaunchEligibility(projectId: string) {
    const project = await this.prisma.project.findUnique({
      where: { id: projectId },
      include: {
        founder: {
          select: {
            id: true,
            name: true,
            presenceLevel: true,
            githubUsername: true,
            verifications: { where: { verified: true }, select: { type: true } },
          },
        },
        _count: { select: { buildPosts: true, followers: true } },
      },
    });
    if (!project) throw new NotFoundException(`Project ${projectId} not found`);

    const launch = await this.getOrCreateForProject(projectId);
    const pledged = await this.totalPledges(launch.id);
    const thresholdMet = pledged >= TokenLaunchService.PLEDGE_THRESHOLD;

    // Update the launch row if the threshold just flipped.
    if (thresholdMet && !launch.pledgeThresholdMet) {
      await this.prisma.tokenLaunch.update({
        where: { id: launch.id },
        data: { pledgeThresholdMet: true },
      });
    }

    // Pre-launch checklist (spec §3).
    const founderDoxxed =
      (project.founder?.verifications?.length ?? 0) > 0 ||
      Boolean(project.founder?.githubUsername);
    const hasBuildPost = project._count.buildPosts >= 1;
    const projectComplete = Boolean(
      project.summary && project.founder?.name,
    );
    const checklist = {
      founderDoxxed,
      hasBuildPost,
      projectComplete,
      twitterHandle: Boolean(project.founder?.name),
    };
    const checklistComplete = Object.values(checklist).every(Boolean);

    const eligible = thresholdMet && checklistComplete;
    const needed = Math.max(
      0,
      TokenLaunchService.PLEDGE_THRESHOLD - pledged,
    );

    return {
      projectId,
      launchId: launch.id,
      status: launch.status,
      pledged,
      threshold: TokenLaunchService.PLEDGE_THRESHOLD,
      needed,
      thresholdMet,
      checklist,
      checklistComplete,
      eligible,
      pledgePoolPercent: launch.pledgePoolPercent,
    };
  }

  /**
   * Full launch status — for the launch progress panel.
   */
  async getLaunchStatus(launchId: string) {
    const launch = await this.prisma.tokenLaunch.findUnique({
      where: { id: launchId },
      include: {
        project: {
          select: {
            id: true,
            slug: true,
            name: true,
            ticker: true,
            summary: true,
            logoUrl: true,
          },
        },
        pledges: {
          include: {
            user: {
              select: { id: true, name: true, platformHandle: true },
            },
          },
          orderBy: { amount: 'desc' },
        },
        _count: { select: { swaps: true, pledges: true } },
      },
    });
    if (!launch) throw new NotFoundException(`Launch ${launchId} not found`);

    const pledged = launch.pledges.reduce((s, p) => s + p.amount, 0);
    const now = Date.now();
    const daysRemaining = launch.windowClosesAt
      ? Math.max(
          0,
          Math.ceil(
            (new Date(launch.windowClosesAt).getTime() - now) / 86400000,
          ),
        )
      : null;

    const totalAllocated = launch.pledges
      .filter((p) => p.allocatedTokens !== null)
      .reduce((s, p) => s + Number(p.allocatedTokens ?? 0), 0);

    return {
      launchId: launch.id,
      projectId: launch.projectId,
      project: launch.project,
      status: launch.status,
      pledged,
      threshold: TokenLaunchService.PLEDGE_THRESHOLD,
      pledgeThresholdMet: launch.pledgeThresholdMet,
      pledgePoolPercent: launch.pledgePoolPercent,
      supply: Number(launch.supply),
      initialPrice: Number(launch.initialPrice),
      solanaMint: launch.solanaMint,
      solanaExplorerUrl: launch.solanaMint
        ? this.solanaMint.explorerUrl(launch.solanaMint)
        : null,
      launchDate: launch.launchDate?.toISOString() ?? null,
      windowClosesAt: launch.windowClosesAt?.toISOString() ?? null,
      daysRemaining,
      finalizedAt: launch.finalizedAt?.toISOString() ?? null,
      closedAt: launch.closedAt?.toISOString() ?? null,
      closeReason: launch.closeReason,
      totalPledgers: launch._count.pledges,
      totalSwaps: launch._count.swaps,
      totalAllocatedTokens: totalAllocated,
      pledges: launch.pledges.map((p) => ({
        id: p.id,
        userId: p.userId,
        userName: p.user.name,
        userHandle: p.user.platformHandle,
        amount: p.amount,
        allocatedTokens:
          p.allocatedTokens !== null ? Number(p.allocatedTokens) : null,
        refunded: p.refunded,
        createdAt: p.createdAt.toISOString(),
      })),
    };
  }

  /**
   * Leaderboard of pledgers for a project (the GET /:projectId/pledges surface).
   */
  async getPledgeLeaderboard(projectId: string, limit = 25) {
    const launch = await this.getOrCreateForProject(projectId);
    const pledges = await this.prisma.tokenPledge.findMany({
      where: { launchId: launch.id, amount: { gt: 0 } },
      include: {
        user: { select: { name: true, platformHandle: true } },
      },
      orderBy: { amount: 'desc' },
      take: Math.min(Math.max(limit, 1), 100),
    });

    const totalPoolTokens =
      (Number(launch.supply) * launch.pledgePoolPercent) / 100;
    const totalPledged = pledges.reduce((s, p) => s + p.amount, 0) || 1;

    return {
      projectId,
      launchId: launch.id,
      pledgePoolPercent: launch.pledgePoolPercent,
      totalPoolTokens,
      totalPledged,
      leaderboard: pledges.map((p, i) => ({
        rank: i + 1,
        userId: p.userId,
        userName: p.user.name,
        userHandle: p.user.platformHandle,
        amount: p.amount,
        sharePct: totalPledged > 0 ? (p.amount / totalPledged) * 100 : 0,
        projectedTokens:
          totalPledged > 0 ? (p.amount / totalPledged) * totalPoolTokens : 0,
        allocatedTokens:
          p.allocatedTokens !== null ? Number(p.allocatedTokens) : null,
      })),
    };
  }

  // ─── Write paths ─────────────────────────────────────────────────────────

  /**
   * Release the token. Requires threshold met + checklist complete.
   * Mints the SPL token on Solana devnet, opens the 15-day commitment window.
   */
  async initiateLaunch(projectId: string) {
    const eligibility = await this.checkLaunchEligibility(projectId);
    if (!eligibility.thresholdMet) {
      throw new BadRequestException(
        `Pledge threshold not met — ${eligibility.needed.toLocaleString()} more DDollar needed.`,
      );
    }
    if (!eligibility.checklistComplete) {
      throw new BadRequestException(
        'Pre-launch checklist incomplete — finish doxxing, ship a build post, and complete the project record.',
      );
    }

    const launch = await this.prisma.tokenLaunch.findUnique({
      where: { projectId },
    });
    if (!launch) throw new NotFoundException(`Launch for ${projectId} not found`);
    if (launch.status !== TokenLaunchStatus.PLEDGING) {
      throw new BadRequestException(
        `Launch already ${launch.status} — cannot re-initiate.`,
      );
    }

    // Mint on devnet.
    const project = await this.prisma.project.findUnique({
      where: { id: projectId },
      select: { name: true, ticker: true },
    });
    const mint = await this.solanaMint.mintLaunchToken(
      `${project?.name ?? projectId} (${project?.ticker ?? 'TOKEN'})`,
      Number(launch.supply),
    );

    const windowClosesAt = new Date(
      Date.now() + TokenLaunchService.WINDOW_DAYS * 86400000,
    );

    const updated = await this.prisma.tokenLaunch.update({
      where: { id: launch.id },
      data: {
        status: TokenLaunchStatus.WINDOW_OPEN,
        launchDate: new Date(),
        solanaMint: mint.mintAddress,
        windowClosesAt,
        pledgeThresholdMet: true,
      },
    });

    this.logger.log(
      `launch initiated project=${projectId} mint=${mint.mintAddress} windowCloses=${windowClosesAt.toISOString()}`,
    );

    return {
      launchId: updated.id,
      projectId,
      status: updated.status,
      solanaMint: updated.solanaMint,
      solanaExplorerUrl: mint.explorerUrl,
      launchDate: updated.launchDate!.toISOString(),
      windowClosesAt: updated.windowClosesAt!.toISOString(),
    };
  }

  /**
   * Community commitment during the 15-day window (or during PLEDGING —
   * pledges accrue toward the threshold). `amount` is DDollar, escrowed via
   * the user's reputationPoints balance (the same ledger SpendingEngine uses).
   *
   * We deliberately do NOT call SpendingEngine.spend() here to avoid a cross-
   * module DI dependency in the thin slice. Instead we atomically decrement
   * reputationPoints with a guard clause, mirroring SpendingEngine's pattern,
   * and write a PointLedger row with actionKey TOKEN_PLEDGE. A later refactor
   * can route through SpendingEngine once escrow semantics are finalized.
   */
  async commitToLaunch(launchId: string, userId: string, amount: number) {
    if (amount <= 0) {
      throw new BadRequestException('Commitment amount must be positive');
    }

    const launch = await this.prisma.tokenLaunch.findUnique({
      where: { id: launchId },
    });
    if (!launch) throw new NotFoundException(`Launch ${launchId} not found`);

    if (
      launch.status !== TokenLaunchStatus.PLEDGING &&
      launch.status !== TokenLaunchStatus.WINDOW_OPEN
    ) {
      throw new BadRequestException(
        `Launch is ${launch.status} — commitments closed.`,
      );
    }

    if (launch.status === TokenLaunchStatus.WINDOW_OPEN) {
      const expired =
        launch.windowClosesAt && new Date(launch.windowClosesAt) < new Date();
      if (expired) {
        throw new BadRequestException(
          'Commitment window has closed — wait for finalization.',
        );
      }
    }

    // Escrow the DDollar: atomic decrement with guard, mirroring SpendingEngine.
    const debit = await this.prisma.user.updateMany({
      where: { id: userId, reputationPoints: { gte: amount } },
      data: { reputationPoints: { decrement: amount } },
    });
    if (debit.count === 0) {
      throw new BadRequestException(
        `Need ${amount.toLocaleString()} DDollar to commit — earn more by scouting, trading, and validating.`,
      );
    }

    await this.prisma.pointLedger.create({
      data: {
        userId,
        amount: -amount,
        actionKey: 'TOKEN_PLEDGE',
        label: 'Token launch pledge (escrow)',
      },
    });

    // Upsert the pledge row.
    const pledge = await this.prisma.tokenPledge.upsert({
      where: { launchId_userId: { launchId, userId } },
      create: { launchId, userId, amount },
      update: { amount: { increment: amount } },
    });

    // Re-check threshold.
    const total = await this.totalPledges(launchId);
    if (
      total >= TokenLaunchService.PLEDGE_THRESHOLD &&
      !launch.pledgeThresholdMet
    ) {
      await this.prisma.tokenLaunch.update({
        where: { id: launchId },
        data: { pledgeThresholdMet: true },
      });
    }

    this.logger.log(
      `commit launch=${launchId} user=${userId} amount=${amount} total=${total}`,
    );

    return {
      launchId,
      userId,
      pledged: pledge.amount,
      totalPledged: total,
      threshold: TokenLaunchService.PLEDGE_THRESHOLD,
      thresholdMet: total >= TokenLaunchService.PLEDGE_THRESHOLD,
    };
  }

  // ─── Cron-driven finalization ───────────────────────────────────────────

  /**
   * Close launches whose 15-day window expired. Computes pro-rata token
   * allocations from the 5% pledge pool and moves status → LIVE.
   * Called daily by TokenLaunchCron.
   */
  async finalizeExpiredWindows(): Promise<number> {
    const now = new Date();
    const expired = await this.prisma.tokenLaunch.findMany({
      where: {
        status: TokenLaunchStatus.WINDOW_OPEN,
        windowClosesAt: { lt: now },
      },
      include: { pledges: true },
    });

    let closed = 0;
    for (const launch of expired) {
      await this.finalizeLaunch(launch);
      closed += 1;
    }

    if (closed > 0) {
      this.logger.log(`finalized ${closed} launch window(s)`);
    }
    return closed;
  }

  /**
   * Finalize one launch — compute allocations + move to LIVE.
   * Public so the cron and an admin endpoint can call it.
   */
  async finalizeLaunch(launch: {
    id: string;
    projectId?: string;
    supply: bigint;
    pledgePoolPercent: number;
    pledges: { id: string; userId: string; amount: number }[];
  }): Promise<void> {
    const totalPledged = launch.pledges.reduce((s, p) => s + p.amount, 0);
    const poolTokens =
      (Number(launch.supply) * launch.pledgePoolPercent) / 100;

    if (totalPledged <= 0) {
      // No pledges → straight to LIVE, empty allocation.
      await this.prisma.tokenLaunch.update({
        where: { id: launch.id },
        data: {
          status: TokenLaunchStatus.LIVE,
          finalizedAt: new Date(),
        },
      });
      await this.grantProductLaunchDdollar(launch.id, launch.projectId);
      return;
    }

    await this.prisma.$transaction(async (tx) => {
      for (const pledge of launch.pledges) {
        const share = pledge.amount / totalPledged;
        const tokens = poolTokens * share;
        await tx.tokenPledge.update({
          where: { id: pledge.id },
          data: { allocatedTokens: tokens },
        });
      }
      await tx.tokenLaunch.update({
        where: { id: launch.id },
        data: {
          status: TokenLaunchStatus.LIVE,
          finalizedAt: new Date(),
        },
      });
    });
    await this.grantProductLaunchDdollar(launch.id, launch.projectId);
  }

  /**
   * Award PRODUCT_LAUNCH_VIA_RAISE_ROOM when a launch goes LIVE.
   * Best-effort — grant failures must not roll back finalization.
   */
  private async grantProductLaunchDdollar(launchId: string, projectId?: string): Promise<void> {
    try {
      let resolvedProjectId = projectId;
      if (!resolvedProjectId) {
        const row = await this.prisma.tokenLaunch.findUnique({
          where: { id: launchId },
          select: { projectId: true },
        });
        resolvedProjectId = row?.projectId;
      }
      if (!resolvedProjectId) return;

      const project = await this.prisma.project.findUnique({
        where: { id: resolvedProjectId },
        select: {
          id: true,
          name: true,
          founder: { select: { userId: true } },
        },
      });
      const userId = project?.founder?.userId;
      if (!userId) {
        this.logger.warn(
          `PRODUCT_LAUNCH grant skipped — launch=${launchId} project=${resolvedProjectId} has no founder.userId`,
        );
        return;
      }

      const amount = DDOLLAR_ACTIVITY_SPECS.PRODUCT_LAUNCH_VIA_RAISE_ROOM.min;
      await this.ddollarEngine.grant(
        userId,
        'PRODUCT_LAUNCH_VIA_RAISE_ROOM',
        launchId,
        amount,
        {
          proofType: 'TOKEN_LAUNCH_LIVE',
          proofData: {
            launchId,
            projectId: resolvedProjectId,
            projectName: project?.name,
            status: 'LIVE',
          },
          label: `Product launched via Raise Room: ${project?.name ?? resolvedProjectId}`,
        },
      );
    } catch (err) {
      this.logger.warn(
        `PRODUCT_LAUNCH DDollar grant failed for launch=${launchId}: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  /**
   * Refund pledges for a launch that closed without going LIVE.
   * Called by the cron for launches abandoned >90 days.
   */
  async refundPledges(launchId: string, reason: string): Promise<number> {
    const launch = await this.prisma.tokenLaunch.findUnique({
      where: { id: launchId },
      include: { pledges: true },
    });
    if (!launch) return 0;
    if (launch.status === TokenLaunchStatus.LIVE) return 0;

    let refunded = 0;
    await this.prisma.$transaction(async (tx) => {
      for (const pledge of launch.pledges) {
        if (pledge.refunded || pledge.amount <= 0) continue;
        await tx.user.update({
          where: { id: pledge.userId },
          data: { reputationPoints: { increment: pledge.amount } },
        });
        await tx.pointLedger.create({
          data: {
            userId: pledge.userId,
            amount: pledge.amount,
            actionKey: 'TOKEN_PLEDGE_REFUND',
            label: `Pledge refund — ${reason}`,
          },
        });
        await tx.tokenPledge.update({
          where: { id: pledge.id },
          data: { refunded: true },
        });
        refunded += 1;
      }
      await tx.tokenLaunch.update({
        where: { id: launchId },
        data: {
          status: TokenLaunchStatus.CLOSED,
          closedAt: new Date(),
          closeReason: reason,
        },
      });
    });

    this.logger.log(`refunded ${refunded} pledge(s) on launch=${launchId} (${reason})`);
    return refunded;
  }

  // ─── Helpers ─────────────────────────────────────────────────────────────

  private async totalPledges(launchId: string): Promise<number> {
    const rows = await this.prisma.tokenPledge.findMany({
      where: { launchId, refunded: false },
      select: { amount: true },
    });
    return rows.reduce((s, r) => s + r.amount, 0);
  }
}
