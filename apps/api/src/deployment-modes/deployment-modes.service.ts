import {
  Injectable,
  Logger,
  NotFoundException,
} from '@nestjs/common';
import { DeploymentMode, Prisma, PublishJobStatus } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import {
  PUBLISH_STEP_LABELS,
  type PublishPlan,
  type PublishResult,
  type PublishStep,
  type RuntimeStatusReport,
  type UpdateDeploymentConfigInput,
} from './deployment-modes.types';

const PUBLISH_STEPS_TOTAL = 4;

/**
 * Prisma's InputJsonValue requires an index signature that typed interfaces
 * (PublishPlan) and arrays of interfaces (PublishStep[]) don't have, so every
 * time we hand one of those to a JSONB column we route through `unknown`.
 * Centralising the cast keeps the call sites readable.
 */
const json = <T>(value: T): Prisma.InputJsonValue => value as unknown as Prisma.InputJsonValue;

/**
 * Phase 7 — Deployment Modes service.
 *
 * Owns the per-project DeploymentMode flag, the ProjectDeploymentConfig row
 * (git/db/hosting/phone/ai + Hybrid publish plan), and the publish flow job
 * ledger. The actual GitHub / Vercel / Neon wiring is a stub for now — real
 * orchestration is slice 7.3 (Founder Node) + a follow-up. See
 * docs/DEPLOYMENT-MODES-UX.md §4–§5.
 */
@Injectable()
export class DeploymentModesService {
  private readonly logger = new Logger(DeploymentModesService.name);

  constructor(private readonly prisma: PrismaService) {}

  // ─── Read ────────────────────────────────────────────────────────────────

  /** Full view the mode panel renders: project mode + config + latest publish job. */
  async getProjectDeployment(projectId: string) {
    const project = await this.prisma.project.findUnique({
      where: { id: projectId },
      select: {
        id: true,
        slug: true,
        name: true,
        deploymentMode: true,
        deploymentConfig: true,
      },
    });
    if (!project) throw new NotFoundException('Project not found');

    const latestPublishJob = await this.prisma.deploymentPublishJob.findFirst({
      where: { projectId },
      orderBy: { createdAt: 'desc' },
    });

    return {
      projectId: project.id,
      name: project.name,
      slug: project.slug,
      deploymentMode: project.deploymentMode,
      config: project.deploymentConfig ?? null,
      latestPublishJob,
    };
  }

  /**
   * Read the per-project config row, lazily seeding it from the current mode
   * the first time it is requested. Seeding applies the per-mode defaults from
   * docs/DEPLOYMENT-MODES-UX.md §4 so the panel shows coherent values even
   * before the founder has touched anything.
   */
  async getOrSeedConfig(projectId: string) {
    const project = await this.prisma.project.findUnique({
      where: { id: projectId },
      select: { deploymentMode: true },
    });
    if (!project) throw new NotFoundException('Project not found');

    const existing = await this.prisma.projectDeploymentConfig.findUnique({
      where: { projectId },
    });
    if (existing) return existing;

    const seeded = this.deriveDefaults(project.deploymentMode);
    return this.prisma.projectDeploymentConfig.create({
      data: { projectId, ...seeded },
    });
  }

  // ─── Update ──────────────────────────────────────────────────────────────

  /**
   * PATCH the config + optionally flip the mode atomically. When the mode
   * flips, we re-derive any field the founder did not explicitly set so the
   * config stays internally consistent with the new mode's defaults.
   */
  async updateConfig(projectId: string, input: UpdateDeploymentConfigInput) {
    const project = await this.prisma.project.findUnique({
      where: { id: projectId },
      select: { deploymentMode: true },
    });
    if (!project) throw new NotFoundException('Project not found');

    const modeChanged =
      input.deploymentMode && input.deploymentMode !== project.deploymentMode;

    return this.prisma.$transaction(async (tx) => {
      // Ensure a config row exists (lazily seeded).
      let config = await tx.projectDeploymentConfig.findUnique({
        where: { projectId },
      });
      if (!config) {
        config = await tx.projectDeploymentConfig.create({
          data: { projectId, ...this.deriveDefaults(project.deploymentMode) },
        });
      }

      const patch: Prisma.ProjectDeploymentConfigUncheckedUpdateInput = {};
      if (input.gitBackend !== undefined) patch.gitBackend = input.gitBackend;
      if (input.gitUrl !== undefined) patch.gitUrl = input.gitUrl;
      if (input.dbProvider !== undefined) patch.dbProvider = input.dbProvider;
      if (input.dbUrl !== undefined) patch.dbUrl = input.dbUrl;
      if (input.hostingType !== undefined) patch.hostingType = input.hostingType;
      if (input.hostingUrl !== undefined) patch.hostingUrl = input.hostingUrl;
      if (input.phoneRoute !== undefined) patch.phoneRoute = input.phoneRoute;
      if (input.aiGateway !== undefined) patch.aiGateway = input.aiGateway;
      if (input.publishPlan !== undefined) {
        patch.publishPlan = input.publishPlan
          ? json(input.publishPlan)
          : Prisma.DbNull;
      }

      if (modeChanged) {
        // Re-derive defaults for the new mode, but keep anything the founder
        // explicitly set in this same PATCH (their values win over the default).
        const newDefaults = this.deriveDefaults(input.deploymentMode!);
        const explicit: Partial<UpdateDeploymentConfigInput> = { ...input };
        delete explicit.deploymentMode;
        patch.gitBackend = explicit.gitBackend ?? newDefaults.gitBackend;
        patch.gitUrl = explicit.gitUrl ?? newDefaults.gitUrl ?? null;
        patch.dbProvider = explicit.dbProvider ?? newDefaults.dbProvider;
        patch.hostingType = explicit.hostingType ?? newDefaults.hostingType;
        patch.hostingUrl = explicit.hostingUrl ?? newDefaults.hostingUrl ?? null;
        patch.phoneRoute = explicit.phoneRoute ?? newDefaults.phoneRoute;
        // When flipping TO public, ensure a publish plan exists so Publish is one click.
        if (
          input.deploymentMode === DeploymentMode.PUBLIC &&
          !explicit.publishPlan &&
          !config.publishPlan
        ) {
          patch.publishPlan = json(this.derivePublishPlan(projectId));
        }
        await tx.project.update({
          where: { id: projectId },
          data: { deploymentMode: input.deploymentMode! },
        });
      }

      return tx.projectDeploymentConfig.update({
        where: { projectId },
        data: patch,
      });
    });
  }

  /** Convenience wrapper for the "Switch mode" radio in the panel (spec §3). */
  async flipMode(projectId: string, mode: DeploymentMode) {
    return this.updateConfig(projectId, { deploymentMode: mode });
  }

  /**
   * Build a default publish plan for a Hybrid → Public promotion. The values
   * are placeholders the founder edits before publishing; they are intentionally
   * derived from the project slug so they read sensibly out of the box.
   */
  generatePublishPlan(projectId: string): PublishPlan {
    const slugBase = projectId.toLowerCase().replace(/[^a-z0-9]/g, '-').slice(0, 40);
    return {
      targetGithubRepo: `founder/${slugBase || 'my-project'}`,
      targetNeonProject: `${slugBase || 'my-project'}-prod`,
      targetVercelProject: slugBase || 'my-project',
      targetDomain: `${slugBase || 'my-project'}.foundersdomain.com`,
    };
  }

  // ─── Publish flow (stub) ─────────────────────────────────────────────────

  /**
   * Kick off the 4-step Hybrid → Public publish flow (spec §5).
   *
   * For now this creates a DeploymentPublishJob in RUNNING state and runs the
   * steps as fast no-ops that record progress. Real GitHub/Vercel/Neon wiring
   * lands in slice 7.3 + a follow-up; the structure + ledger is in place so
   * the frontend publish-progress UI has something to poll.
   */
  async startPublish(projectId: string): Promise<PublishResult> {
    const project = await this.prisma.project.findUnique({
      where: { id: projectId },
      select: { deploymentMode: true, deploymentConfig: true },
    });
    if (!project) throw new NotFoundException('Project not found');

    const config =
      project.deploymentConfig ??
      (await this.prisma.projectDeploymentConfig.create({
        data: { projectId, ...this.deriveDefaults(project.deploymentMode) },
      }));

    const plan = (config.publishPlan as PublishPlan | null) ?? this.generatePublishPlan(projectId);

    const job = await this.prisma.deploymentPublishJob.create({
      data: {
        projectId,
        planSnapshot: json(plan),
        status: PublishJobStatus.RUNNING,
        currentStep: 0,
        steps: json(this.emptySteps()),
        startedAt: new Date(),
      },
    });

    // Fire-and-forget stub execution. Real implementation will call out to
    // GitHub API, Vercel deploy hook, and prisma migrate against Neon.
    void this.runPublishStepsStub(job.id, projectId, plan).catch((err) => {
      this.logger.error(`Publish job ${job.id} failed: ${err?.message ?? err}`);
    });

    return this.toPublishResult(job);
  }

  /** Poll the latest (or specific) publish job for live progress. */
  async getPublishJob(projectId: string, jobId?: string) {
    const job = jobId
      ? await this.prisma.deploymentPublishJob.findFirst({
          where: { id: jobId, projectId },
        })
      : await this.prisma.deploymentPublishJob.findFirst({
          where: { projectId },
          orderBy: { createdAt: 'desc' },
        });
    if (!job) throw new NotFoundException('Publish job not found');
    return this.toPublishResult(job);
  }

  // ─── Founder Node runtime status relay ───────────────────────────────────

  /**
   * Called by Founder Node (slice 7.3) to surface "what's running" in the panel.
   * Pure presence-check flags — no orchestration here.
   */
  async reportRuntimeStatus(projectId: string, report: RuntimeStatusReport) {
    await this.getOrSeedConfig(projectId); // ensure row exists
    return this.prisma.projectDeploymentConfig.update({
      where: { projectId },
      data: {
        forgejoOnline: report.forgejoOnline,
        sqlitePresent: report.sqlitePresent,
        tunnelActive: report.tunnelActive,
        tailscaleReady: report.tailscaleReady,
        runtimeStatusAt: new Date(),
      },
    });
  }

  // ─── Internals ───────────────────────────────────────────────────────────

  /**
   * Per-mode default config (spec §4). Returned patch is merged into the
   * ProjectDeploymentConfig row. `gitUrl` / `hostingUrl` start null in every
   * mode — they are populated by Founder Node (Private) or Publish (Public).
   */
  private deriveDefaults(
    mode: DeploymentMode,
  ): Omit<Prisma.ProjectDeploymentConfigUncheckedCreateInput, 'projectId'> {
    if (mode === DeploymentMode.PUBLIC) {
      return {
        gitBackend: 'github',
        gitUrl: null,
        dbProvider: 'postgresql',
        dbUrl: null,
        hostingType: 'vercel',
        hostingUrl: null,
        phoneRoute: 'public-url',
        aiGateway: 'founder-os-cloud',
      };
    }
    // PRIVATE + HYBRID both start in private runtime. Hybrid additionally
    // carries a publish plan that Publish consumes unchanged.
    return {
      gitBackend: 'forgejo',
      gitUrl: null,
      dbProvider: 'sqlite',
      dbUrl: null,
      hostingType: 'tunnel-on-demand',
      hostingUrl: null,
      phoneRoute: 'tailscale',
      aiGateway: 'founder-os-cloud',
      publishPlan:
        mode === DeploymentMode.HYBRID
          ? json(this.generatePublishPlan('__seed__'))
          : undefined,
    };
  }

  private derivePublishPlan(projectId: string): PublishPlan {
    return this.generatePublishPlan(projectId);
  }

  private emptySteps(): PublishStep[] {
    return Array.from({ length: PUBLISH_STEPS_TOTAL }, (_, i) => ({
      step: i + 1,
      label: PUBLISH_STEP_LABELS[i + 1] ?? `Step ${i + 1}`,
      status: 'pending' as const,
    }));
  }

  /**
   * Stub publish step runner. Advances each of the 4 steps with a short pause
   * and a believable detail line, then flips the project to PUBLIC and marks
   * the job COMPLETED. Real implementation replaces the in-loop body with
   * GitHub/Vercel/Neon API calls — the progress + ledger contract stays.
   */
  private async runPublishStepsStub(jobId: string, projectId: string, plan: PublishPlan) {
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
    const detailFor = (step: number): string => {
      switch (step) {
        case 1:
          return `Pushed history to ${plan.targetGithubRepo}`;
        case 2:
          return 'Converted SQLite → Postgres, applied migration, loaded data';
        case 3:
          return `Building → ${plan.targetVercelProject} (Vercel)`;
        case 4:
          return `Health check on https://${plan.targetDomain}/api/health/live`;
        default:
          return '';
      }
    };

    const steps = this.emptySteps();
    for (let i = 0; i < steps.length; i++) {
      steps[i].status = 'running';
      steps[i].startedAt = new Date().toISOString();
      await this.prisma.deploymentPublishJob.update({
        where: { id: jobId },
        data: {
          currentStep: steps[i].step,
          steps: json(steps),
        },
      });
      await sleep(800); // simulate work
      steps[i].status = 'completed';
      steps[i].detail = detailFor(steps[i].step);
      steps[i].finishedAt = new Date().toISOString();
    }

    const liveUrl = `https://${plan.targetDomain}`;
    await this.prisma.$transaction([
      this.prisma.deploymentPublishJob.update({
        where: { id: jobId },
        data: {
          status: PublishJobStatus.COMPLETED,
          currentStep: PUBLISH_STEPS_TOTAL,
          steps: json(steps),
          liveUrl,
          completedAt: new Date(),
        },
      }),
      this.prisma.project.update({
        where: { id: projectId },
        data: { deploymentMode: DeploymentMode.PUBLIC },
      }),
      this.prisma.projectDeploymentConfig.upsert({
        where: { projectId },
        update: { hostingUrl: liveUrl, hostingType: 'vercel' },
        create: {
          projectId,
          hostingUrl: liveUrl,
          hostingType: 'vercel',
          ...this.deriveDefaults(DeploymentMode.PUBLIC),
        },
      }),
    ]);
  }

  private toPublishResult(job: {
    id: string;
    status: PublishJobStatus;
    currentStep: number;
    steps: Prisma.JsonValue;
    liveUrl: string | null;
    errorMessage: string | null;
  }): PublishResult {
    return {
      jobId: job.id,
      status: job.status,
      currentStep: job.currentStep,
      steps: (job.steps as unknown as PublishStep[]) ?? [],
    };
  }
}
