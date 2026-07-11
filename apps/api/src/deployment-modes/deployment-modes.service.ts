import {
  Injectable,
  Logger,
  NotFoundException,
} from '@nestjs/common';
import { DeploymentMode, Prisma, PublishJobStatus } from '@prisma/client';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { PrismaService } from '../prisma/prisma.service';
import {
  PUBLISH_STEP_LABELS,
  type PublishPlan,
  type PublishResult,
  type PublishStep,
  type RuntimeStatusReport,
  type UpdateDeploymentConfigInput,
} from './deployment-modes.types';

const execFileAsync = promisify(execFile);
const PUBLISH_STEPS_TOTAL = 4;

/** Max attempts for the health-verify poll (step 4). ~30s total. */
const HEALTH_VERIFY_ATTEMPTS = 30;
const HEALTH_VERIFY_INTERVAL_MS = 1000;

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
 * ledger. The publish flow runs real GitHub / Prisma-migrate / Vercel / health
 * steps — see `runPublishSteps`. See docs/DEPLOYMENT-MODES-UX.md §4–§5.
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

    // Fire-and-forget real publish execution. Each step calls out to a real
    // API (GitHub, Prisma migrate against Neon, Vercel deploy hook, health
    // poll) and updates the job ledger as it progresses. A failure in any
    // step marks the job FAILED with the error message.
    void this.runPublishSteps(job.id, projectId, plan, config.dbUrl).catch((err) => {
      this.logger.error(`Publish job ${job.id} crashed: ${err?.message ?? err}`);
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
   * Real publish step runner (spec §5). Executes the 4-step Hybrid → Public
   * publish flow against live APIs:
   *
   *   1. Git mirror   — create the GitHub repo via the REST API.
   *   2. DB migrate   — run `prisma migrate deploy` against the Neon DB URL.
   *   3. Vercel deploy — POST to the Vercel deploy hook.
   *   4. Health verify — poll the deployed URL until it responds 200.
   *
   * Each step updates the job ledger (currentStep + per-step status) as it
   * runs. If any step throws, the job is marked FAILED with the error message
   * and the failing step is recorded as 'failed'. On success the project is
   * flipped to PUBLIC and the hosting URL is persisted.
   *
   * Required env vars (per-project overrides land on ProjectDeploymentConfig):
   *   - GITHUB_TOKEN              — personal access token with repo scope.
   *   - VERCEL_DEPLOY_HOOK_URL    — Vercel deploy hook for the target project.
   *   - config.dbUrl              — Neon connection string (set on the config row).
   *
   * Missing credentials surface as a clear FAILED message rather than a silent
   * success — the founder sees exactly which step needs wiring.
   */
  private async runPublishSteps(
    jobId: string,
    projectId: string,
    plan: PublishPlan,
    dbUrl: string | null,
  ) {
    const steps = this.emptySteps();

    const markRunning = async (step: number) => {
      const s = steps[step - 1];
      s.status = 'running';
      s.startedAt = new Date().toISOString();
      await this.prisma.deploymentPublishJob.update({
        where: { id: jobId },
        data: { currentStep: s.step, steps: json(steps) },
      });
    };

    const markCompleted = async (step: number, detail: string) => {
      const s = steps[step - 1];
      s.status = 'completed';
      s.detail = detail;
      s.finishedAt = new Date().toISOString();
      await this.prisma.deploymentPublishJob.update({
        where: { id: jobId },
        data: { currentStep: s.step, steps: json(steps) },
      });
    };

    const fail = async (step: number, message: string) => {
      const s = steps[step - 1];
      s.status = 'failed';
      s.detail = message;
      s.finishedAt = new Date().toISOString();
      await this.prisma.deploymentPublishJob.update({
        where: { id: jobId },
        data: {
          status: PublishJobStatus.FAILED,
          currentStep: s.step,
          steps: json(steps),
          errorMessage: message,
          completedAt: new Date(),
        },
      });
      this.logger.warn(`Publish job ${jobId} failed at step ${step}: ${message}`);
    };

    try {
      // ── Step 1: Git mirror to GitHub ──────────────────────────────────────
      await markRunning(1);
      const githubToken = process.env.GITHUB_TOKEN?.trim();
      if (!githubToken) {
        await fail(1, 'GITHUB_TOKEN not set — cannot create GitHub repo. Set it on the API service.');
        return;
      }
      const [owner, repoName] = plan.targetGithubRepo.split('/');
      if (!owner || !repoName) {
        await fail(1, `Invalid targetGithubRepo "${plan.targetGithubRepo}" — expected "owner/repo".`);
        return;
      }
      const ghDetail = await this.createGithubRepo(githubToken, owner, repoName);
      await markCompleted(1, ghDetail);

      // ── Step 2: DB migrate to Neon ────────────────────────────────────────
      await markRunning(2);
      if (!dbUrl) {
        await fail(2, 'No Neon DB URL configured on the project deployment config (dbUrl is null).');
        return;
      }
      const migrateDetail = await this.runPrismaMigrateDeploy(dbUrl);
      await markCompleted(2, migrateDetail);

      // ── Step 3: Vercel deploy ─────────────────────────────────────────────
      await markRunning(3);
      const deployHookUrl = process.env.VERCEL_DEPLOY_HOOK_URL?.trim();
      if (!deployHookUrl) {
        await fail(3, 'VERCEL_DEPLOY_HOOK_URL not set — cannot trigger Vercel deploy.');
        return;
      }
      const vercelDetail = await this.triggerVercelDeployHook(deployHookUrl, plan);
      await markCompleted(3, vercelDetail);

      // ── Step 4: Health verify ─────────────────────────────────────────────
      await markRunning(4);
      const liveUrl = `https://${plan.targetDomain}`;
      const healthDetail = await this.verifyHealth(liveUrl);
      await markCompleted(4, healthDetail);

      // ── Finalize: flip project to PUBLIC + persist hosting URL ───────────
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
      this.logger.log(`Publish job ${jobId} completed — live at ${liveUrl}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      // The failing step was last marked running; record it as failed.
      const runningIdx = steps.findIndex((s) => s.status === 'running');
      if (runningIdx >= 0) {
        await fail(runningIdx + 1, message);
      } else {
        await this.prisma.deploymentPublishJob.update({
          where: { id: jobId },
          data: {
            status: PublishJobStatus.FAILED,
            steps: json(steps),
            errorMessage: message,
            completedAt: new Date(),
          },
        });
      }
    }
  }

  // ─── Publish step primitives ───────────────────────────────────────────────

  /**
   * Create a GitHub repo via the REST API. Uses the token owner as the actor;
   * if `owner` doesn't match the token user, this still works for orgs the
   * token can admin. Returns a human-readable detail line for the job ledger.
   */
  private async createGithubRepo(token: string, owner: string, repoName: string): Promise<string> {
    const res = await fetch('https://api.github.com/user/repos', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        name: repoName,
        private: false,
        auto_init: true,
        description: 'Founder OS public deployment',
      }),
    });
    if (res.status === 422) {
      // Repo already exists — that's fine for a re-publish.
      return `Repo ${owner}/${repoName} already exists — reusing it.`;
    }
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`GitHub create repo failed (${res.status}): ${body.slice(0, 200)}`);
    }
    const repo = (await res.json()) as { html_url?: string; clone_url?: string };
    return `Created GitHub repo ${owner}/${repoName} — ${repo.html_url ?? repo.clone_url ?? ''}`.trim();
  }

  /**
   * Run `prisma migrate deploy` against the Neon connection string. Uses the
   * repo's prisma/schema.prisma. Env override via DATABASE_URL so the child
   * process targets the right DB. Returns the trimmed stdout as the detail.
   */
  private async runPrismaMigrateDeploy(dbUrl: string): Promise<string> {
    const env = { ...process.env, DATABASE_URL: dbUrl };
    const { stdout, stderr } = await execFileAsync(
      'npx',
      ['prisma', 'migrate', 'deploy', '--schema', 'prisma/schema.prisma'],
      { env, cwd: process.cwd(), maxBuffer: 4 * 1024 * 1024, shell: process.platform === 'win32' },
    );
    const out = (stdout + stderr).trim();
    if (out.length > 300) {
      return `Migration applied:\n${out.slice(-300)}`;
    }
    return `Migration applied: ${out || 'no pending migrations'}`;
  }

  /**
   * POST to the Vercel deploy hook to trigger a production deploy. The hook is
   * project-scoped on Vercel's side; we just fire it and report the response.
   */
  private async triggerVercelDeployHook(hookUrl: string, plan: PublishPlan): Promise<string> {
    const res = await fetch(hookUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target: 'production',
        projectName: plan.targetVercelProject,
        ref: 'main',
      }),
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`Vercel deploy hook failed (${res.status}): ${body.slice(0, 200)}`);
    }
    return `Triggered Vercel deploy for ${plan.targetVercelProject} (hook ${res.status}).`;
  }

  /**
   * Poll the deployed URL's /api/health/live until it responds 200 or the
   * attempt budget is exhausted. Throws on timeout so the job is marked FAILED.
   */
  private async verifyHealth(liveUrl: string): Promise<string> {
    const healthUrl = `${liveUrl.replace(/\/$/, '')}/api/health/live`;
    for (let attempt = 1; attempt <= HEALTH_VERIFY_ATTEMPTS; attempt++) {
      try {
        const res = await fetch(healthUrl, { method: 'GET' });
        if (res.ok) {
          return `Health check passed on ${healthUrl} (attempt ${attempt}, ${res.status}).`;
        }
      } catch {
        // network error / not up yet — retry
      }
      await new Promise((r) => setTimeout(r, HEALTH_VERIFY_INTERVAL_MS));
    }
    throw new Error(`Health check timed out after ${HEALTH_VERIFY_ATTEMPTS} attempts on ${healthUrl}.`);
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
