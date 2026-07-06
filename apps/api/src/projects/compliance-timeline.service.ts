import { Injectable, NotFoundException } from '@nestjs/common';
import { InvestigationStatus, SimulatedRaiseStatus } from '@prisma/client';
import {
  buildProgressiveUnlockProgress,
  meetsLaunchStage,
  passesLaunchQualification,
  type LaunchStageKey,
} from '@dcf/utils';
import { isPhase15TrustLayerEnabled, isRegulatoryClassCleared } from '../phase15/phase15.constants';
import { LaunchQualificationService } from '../launch-qualification/launch-qualification.service';
import { PrismaService } from '../prisma/prisma.service';

export type ComplianceTimelineStep = {
  key: string;
  label: string;
  status: 'pending' | 'active' | 'complete' | 'blocked';
  date: string | null;
  blockerReason: string | null;
  remediationLink: string | null;
};

@Injectable()
export class ComplianceTimelineService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly launchQualification: LaunchQualificationService,
  ) {}

  async getTimeline(slug: string) {
    const project = await this.prisma.project.findUnique({
      where: { slug },
      include: {
        trustReports: true,
        simulatedRaises: {
          where: { status: { in: [SimulatedRaiseStatus.ACTIVE, SimulatedRaiseStatus.COMPLETED] } },
          orderBy: { createdAt: 'desc' },
          take: 1,
        },
        investigations: {
          where: { status: InvestigationStatus.ACTIVE },
          take: 1,
        },
        regulatoryQuestionnaire: true,
      },
    });
    if (!project) throw new NotFoundException('Project not found');

    const lq = await this.launchQualification.computeAndPersist(project.id);
    const unlock = buildProgressiveUnlockProgress(project.launchStage as LaunchStageKey);
    const activeRaise = project.simulatedRaises[0];

    const listedComplete = project.approved;
    const trustActive = project.trustReports.length > 0;
    const trustComplete = project.trustReports.length >= 3;
    const raiseActive = Boolean(activeRaise);
    const raiseComplete = Boolean(activeRaise && Number(activeRaise.totalBurnedUsd) >= 0);
    const lqComplete = passesLaunchQualification(lq.score);
    const regComplete = isRegulatoryClassCleared(project.regulatoryClass);
    const graduationComplete = Boolean(project.launchRequestedAt);
    const tradingComplete = project.isLiveToken;

    const investigationBlocked = project.investigations.length > 0;

    const steps: ComplianceTimelineStep[] = [
      this.step('listed', 'Listed on platform', listedComplete, project.createdAt, null, null),
      this.step(
        'trust',
        'Trust review',
        trustComplete,
        trustActive ? project.trustReports[0]?.createdAt ?? null : null,
        investigationBlocked ? 'Active investigation open' : trustActive ? null : 'Awaiting Trust Center signals',
        '/trust-center',
      ),
      this.step(
        'validation',
        'Community validation',
        trustComplete,
        trustComplete ? project.trustReports[project.trustReports.length - 1]?.createdAt ?? null : null,
        trustComplete ? null : 'Need weighted validation from scouts',
        `/project/${slug}#community`,
      ),
      this.step(
        'raise',
        'Raise Room paper conviction',
        raiseComplete,
        activeRaise?.startsAt ?? null,
        raiseActive ? null : 'Founder has not opened Proof Raise registration',
        `/project/${slug}#raise`,
      ),
      this.step(
        'qualification',
        'Launch Qualification',
        lqComplete && regComplete,
        project.launchQualificationAt,
        !regComplete
          ? 'Complete regulatory questionnaire'
          : !lqComplete
            ? `Launch score ${lq.score}/100 — need ≥ 70`
            : null,
        `/project/${slug}#raise`,
      ),
      this.step(
        'graduation',
        'Founder Graduation',
        graduationComplete,
        project.launchRequestedAt,
        !meetsLaunchStage(project.launchStage as LaunchStageKey, 'GRADUATION')
          ? unlock.nextHint
          : null,
        `/founder-den`,
      ),
      this.step(
        'trading',
        'Trading on Founder Exchange',
        tradingComplete,
        project.lastTradeAt,
        tradingComplete ? null : 'Graduate first — curated swap layer only',
        `/exchange`,
      ),
    ];

    for (const s of steps) {
      if (s.status === 'pending' && steps.some((x) => x.status === 'complete' && x.key !== s.key)) {
        const prev = steps[steps.indexOf(s) - 1];
        if (prev?.status === 'complete' && !s.blockerReason) s.status = 'active';
      }
    }

    return {
      enabled: isPhase15TrustLayerEnabled(),
      slug,
      launchStage: project.launchStage,
      progressiveUnlock: unlock,
      launchQualification: {
        score: lq.score,
        tier: lq.tier,
        passes: lq.passes,
      },
      regulatoryClass: project.regulatoryClass,
      steps,
    };
  }

  private step(
    key: string,
    label: string,
    complete: boolean,
    date: Date | null | undefined,
    blockerReason: string | null,
    remediationLink: string | null,
  ): ComplianceTimelineStep {
    let status: ComplianceTimelineStep['status'] = 'pending';
    if (blockerReason) status = 'blocked';
    else if (complete) status = 'complete';
    else status = 'active';

    return {
      key,
      label,
      status,
      date: date ? date.toISOString() : null,
      blockerReason,
      remediationLink,
    };
  }
}
