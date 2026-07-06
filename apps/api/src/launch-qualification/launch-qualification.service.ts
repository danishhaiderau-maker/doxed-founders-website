import { BadRequestException, Injectable, NotFoundException } from '@nestjs/common';
import {
  InvestigationStatus,
  LaunchStage,
  RegulatoryClass,
  SimulatedRaiseStatus,
} from '@prisma/client';
import {
  LAUNCH_QUALIFICATION_MIN_SCORE,
  buildProgressiveUnlockProgress,
  computeLaunchQualificationScore,
  getLaunchQualificationTier,
  meetsLaunchStage,
  passesLaunchQualification,
  tallyWeightedVotes,
  validationCategoryToVote,
  type LaunchQualificationComponents,
  type LaunchStageKey,
} from '@dcf/utils';
import {
  isPhase15TrustLayerEnabled,
  isRegulatoryClassBlocked,
  isRegulatoryClassCleared,
} from '../phase15/phase15.constants';
import { PrismaService } from '../prisma/prisma.service';

export type Phase15GateResult = {
  enabled: boolean;
  allowed: boolean;
  blockers: string[];
  regulatoryClass: RegulatoryClass | null;
  launchQualificationScore: number;
  launchStage: LaunchStage;
};

@Injectable()
export class Phase15GatesService {
  constructor(private readonly prisma: PrismaService) {}

  isEnabled(): boolean {
    return isPhase15TrustLayerEnabled();
  }

  evaluate(project: {
    regulatoryClass: RegulatoryClass;
    launchQualificationScore: number;
    launchStage: LaunchStage;
  }): Phase15GateResult {
    if (!this.isEnabled()) {
      return {
        enabled: false,
        allowed: true,
        blockers: [],
        regulatoryClass: project.regulatoryClass,
        launchQualificationScore: project.launchQualificationScore,
        launchStage: project.launchStage,
      };
    }

    const blockers: string[] = [];

    if (
      project.regulatoryClass === RegulatoryClass.PENDING ||
      isRegulatoryClassBlocked(project.regulatoryClass)
    ) {
      blockers.push('Complete regulatory questionnaire with a cleared classification');
    }

    if (!passesLaunchQualification(project.launchQualificationScore)) {
      blockers.push(`Launch Qualification score must be ≥ ${LAUNCH_QUALIFICATION_MIN_SCORE}`);
    }

    if (!meetsLaunchStage(project.launchStage as LaunchStageKey, 'GRADUATION')) {
      blockers.push('Progressive unlock stage must reach Graduation (5+)');
    }

    return {
      enabled: true,
      allowed: blockers.length === 0,
      blockers,
      regulatoryClass: project.regulatoryClass,
      launchQualificationScore: project.launchQualificationScore,
      launchStage: project.launchStage,
    };
  }

  async assertProofRaiseAllowed(projectId: string): Promise<Phase15GateResult> {
    const project = await this.prisma.project.findUnique({
      where: { id: projectId },
      select: {
        regulatoryClass: true,
        launchQualificationScore: true,
        launchStage: true,
      },
    });
    if (!project) throw new NotFoundException('Project not found');

    const gate = this.evaluate(project);
    if (!gate.allowed) {
      throw new BadRequestException(
        `Phase 1.5 gate blocked: ${gate.blockers.join('; ')}`,
      );
    }
    return gate;
  }
}

@Injectable()
export class LaunchQualificationService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly gates: Phase15GatesService,
  ) {}

  isEnabled(): boolean {
    return isPhase15TrustLayerEnabled();
  }

  async computeAndPersist(projectId: string) {
    const project = await this.prisma.project.findUnique({
      where: { id: projectId },
      include: {
        founder: { include: { videos: true, verifications: true } },
        buildPosts: true,
        trustReports: true,
        investigations: {
          where: {
            status: { in: [InvestigationStatus.ACTIVE, InvestigationStatus.ADMIN_REVIEW] },
          },
          take: 1,
        },
        simulatedRaises: {
          where: { status: SimulatedRaiseStatus.ACTIVE },
          include: { allocations: true },
        },
        _count: { select: { followers: true, buildPosts: true, trustReports: true } },
      },
    });
    if (!project) throw new NotFoundException('Project not found');

    const activeRaise = project.simulatedRaises[0];
    const totalAllocated = activeRaise
      ? activeRaise.allocations.reduce(
          (s, a) =>
            s +
            Number(
              (a as { effectivePaperUsd?: { toString(): string } | null }).effectivePaperUsd ??
                a.amountUsd,
            ),
          0,
        )
      : 0;
    const goalUsd = activeRaise ? Number(activeRaise.goalUsd) : 0;
    const fillRatio = goalUsd > 0 ? Math.min(100, (totalAllocated / goalUsd) * 100) : 0;

    const tally = tallyWeightedVotes(
      project.trustReports.map((r) => ({
        vote: validationCategoryToVote(r.category),
        weight: r.voteWeight,
      })),
      3,
    );

    const hasActiveInvestigation = project.investigations.length > 0;
    const founderVerified = Boolean(
      project.founder?.verifications?.some((v) => v.verified) ||
        project.founder?.githubUsername,
    );

    const components: LaunchQualificationComponents = {
      communityTrust: Math.min(100, tally.yesPercent),
      paperConviction: Math.round(fillRatio),
      founderLaunchScore: Math.min(100, project.launchReadiness),
      founderIntegrity: hasActiveInvestigation ? 35 : founderVerified ? 75 : 50,
      buildDelivery: Math.min(
        100,
        project._count.buildPosts * 15 + (project.founder?.videos.length ?? 0) * 20,
      ),
      regulatoryClearance: isRegulatoryClassCleared(project.regulatoryClass)
        ? 100
        : project.regulatoryClass === RegulatoryClass.PENDING
          ? 40
          : 0,
    };

    const score = computeLaunchQualificationScore(components);
    const tier = getLaunchQualificationTier(score);
    const now = new Date();

    const launchStage = await this.recomputeLaunchStage(projectId, {
      ...project,
      launchQualificationScore: score,
    });

    await this.prisma.project.update({
      where: { id: projectId },
      data: {
        launchQualificationScore: score,
        launchQualificationTier: tier,
        launchQualificationMeta: components as object,
        launchQualificationAt: now,
        launchStage,
      },
    });

    return {
      score,
      tier,
      components,
      passes: passesLaunchQualification(score),
      launchStage,
      progressiveUnlock: buildProgressiveUnlockProgress(launchStage as LaunchStageKey),
      gate: this.gates.evaluate({
        regulatoryClass: project.regulatoryClass,
        launchQualificationScore: score,
        launchStage,
      }),
    };
  }

  async recomputeLaunchStage(
    projectId: string,
    snapshot?: {
      approved: boolean;
      launchQualificationScore: number;
      regulatoryClass: RegulatoryClass;
      launchRequestedAt: Date | null;
      _count: { buildPosts: number; trustReports: number; followers: number };
      founder?: { verifications?: { verified: boolean }[] } | null;
    },
  ): Promise<LaunchStage> {
    const project =
      snapshot ??
      (await this.prisma.project.findUnique({
        where: { id: projectId },
        include: {
          founder: { include: { verifications: true } },
          _count: { select: { buildPosts: true, trustReports: true, followers: true } },
        },
      }));
    if (!project) throw new NotFoundException('Project not found');

    let stage: LaunchStage = LaunchStage.BUILDER;

    if (project.approved) stage = LaunchStage.WORKSPACE;

    const founderVerified = Boolean(
      project.founder?.verifications?.some((v) => v.verified),
    );
    if (project.approved && project._count.buildPosts >= 1 && founderVerified) {
      stage = LaunchStage.PROJECT;
    }

    if (project._count.trustReports >= 3 || project._count.followers >= 5) {
      stage = LaunchStage.RAISE_ROOM;
    }

    if (
      passesLaunchQualification(project.launchQualificationScore) &&
      isRegulatoryClassCleared(project.regulatoryClass)
    ) {
      stage = LaunchStage.GRADUATION;
    }

    if (project.launchRequestedAt) {
      stage = LaunchStage.FOUNDER_EXCHANGE;
    }

    return stage;
  }

  async getBySlug(slug: string) {
    const project = await this.prisma.project.findUnique({ where: { slug } });
    if (!project) throw new NotFoundException('Project not found');
    return this.computeAndPersist(project.id);
  }

  async getMetadataPreviewGate(slug: string) {
    const project = await this.prisma.project.findUnique({ where: { slug } });
    if (!project) throw new NotFoundException('Project not found');

    const lq = await this.computeAndPersist(project.id);
    const gate = this.gates.evaluate({
      regulatoryClass: project.regulatoryClass,
      launchQualificationScore: lq.score,
      launchStage: lq.launchStage,
    });

    return {
      slug,
      previewAllowed: gate.allowed,
      gate,
      sampleMetadata: gate.allowed
        ? {
            name: project.name,
            ticker: project.ticker,
            disclaimer: 'Preview only — not published on-chain',
          }
        : null,
    };
  }
}
