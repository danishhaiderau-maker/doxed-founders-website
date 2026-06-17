import { BadRequestException, Injectable } from '@nestjs/common';
import { ComputePlaneMode, FounderNodeSyncJobKind, Prisma } from '@prisma/client';
import {
  createImportJob,
  formatImportSummary,
  importJobComplete,
  normalizePlatformConnections,
  parseFounderCloudState,
  resolveUnifiedPublishPlan,
  type FounderCloudState,
  type FounderImportJob,
  type ImportWizardStep,
  type LocalStackState,
  type UnifiedPublishPlan,
} from '@dcf/utils';
import { randomBytes } from 'crypto';
import { PrismaService } from '../prisma/prisma.service';
import { FounderOsMemoryService } from '../github/founder-os-memory.service';
import { PlatformConnectionsService } from './platform-connections.service';

@Injectable()
export class FounderCloudService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly memory: FounderOsMemoryService,
    private readonly platformConnections: PlatformConnectionsService,
  ) {}

  async getState(userId: string): Promise<FounderCloudState> {
    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { founderCloudState: true, computePlaneMode: true, onboardingPath: true },
    });
    const parsed = parseFounderCloudState(settings?.founderCloudState) ?? {
      import: null,
      localStack: null,
    };
    return parsed;
  }

  private async saveState(userId: string, state: FounderCloudState) {
    await this.prisma.founderBuilderSettings.upsert({
      where: { userId },
      create: {
        userId,
        founderCloudState: state as unknown as Prisma.InputJsonValue,
      },
      update: {
        founderCloudState: state as unknown as Prisma.InputJsonValue,
      },
    });
    return state;
  }

  async getPublishPlan(userId: string): Promise<UnifiedPublishPlan & { importComplete: boolean }> {
    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { platformConnections: true },
    });
    const toggles = normalizePlatformConnections(settings?.platformConnections);
    const state = await this.getState(userId);
    const plan = resolveUnifiedPublishPlan(toggles);
    return {
      ...plan,
      importComplete: importJobComplete(state.import),
    };
  }

  async startImport(userId: string): Promise<FounderImportJob> {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) throw new BadRequestException('Founder profile required');

    const gh = await this.prisma.gitHubConnection.findUnique({ where: { userId } });
    const repo =
      gh?.repoFullName && !gh.repoFullName.endsWith('/pending-setup')
        ? gh.repoFullName
        : founder.githubRepoFullName;

    const jobId = `imp_${randomBytes(6).toString('hex')}`;
    const job = createImportJob(jobId, repo);
    job.status = 'running';
    job.startedAt = new Date().toISOString();

    const state = await this.getState(userId);
    state.import = job;
    await this.saveState(userId, state);

    return this.runImportSteps(userId, job, repo);
  }

  async getImportStatus(userId: string) {
    const state = await this.getState(userId);
    return {
      job: state.import,
      complete: importJobComplete(state.import),
      summary: state.import ? formatImportSummary(state.import) : null,
    };
  }

  private patchStep(job: FounderImportJob, stepId: ImportWizardStep['id'], patch: Partial<ImportWizardStep>) {
    const step = job.steps.find((s) => s.id === stepId);
    if (step) Object.assign(step, patch, { at: new Date().toISOString() });
  }

  private async runImportSteps(
    userId: string,
    job: FounderImportJob,
    repo: string | null,
  ): Promise<FounderImportJob> {
    const state = await this.getState(userId);
    const creds = await this.prisma.integrationCredential.findMany({ where: { userId } });
    const hostProviders = ['railway', 'vercel', 'render', 'neon', 'supabase'];
    const connectedHosts = creds
      .filter((c) => hostProviders.includes(c.provider) && c.verifiedAt)
      .map((c) => c.provider);

    try {
      this.patchStep(job, 'validate_sources', { status: 'running' });
      if (!repo) {
        this.patchStep(job, 'validate_sources', {
          status: 'error',
          detail: 'Connect GitHub repo first',
        });
        job.status = 'failed';
        job.summary = 'GitHub repo required for import';
        state.import = job;
        await this.saveState(userId, state);
        return job;
      }
      this.patchStep(job, 'validate_sources', {
        status: 'done',
        detail: `GitHub ${repo}${connectedHosts.length ? ` · hosts: ${connectedHosts.join(', ')}` : ''}`,
      });

      this.patchStep(job, 'mirror_repo_memory', { status: 'running' });
      const githubMemory = await this.memory.readRepoMemory(userId, repo);
      const mirrored: string[] = [];
      if (githubMemory?.projectContext) mirrored.push('project-context');
      if (githubMemory?.roadmap) mirrored.push('roadmap');
      if (githubMemory?.openTasksFromRepo?.length) mirrored.push('tasks');
      this.patchStep(job, 'mirror_repo_memory', {
        status: mirrored.length ? 'done' : 'skipped',
        detail: mirrored.length
          ? `Mirrored ${mirrored.join(', ')} into platform memory`
          : 'No founder-os memory files in repo yet',
      });

      this.patchStep(job, 'env_manifest', { status: 'running' });
      job.providersMirrored = connectedHosts;
      this.patchStep(job, 'env_manifest', {
        status: 'done',
        detail:
          connectedHosts.length > 0
            ? `Manifest: ${connectedHosts.join(', ')} (names only — secrets stay encrypted)`
            : 'No host credentials connected — skip env mirror',
      });

      this.patchStep(job, 'vault_seed', { status: 'running' });
      const node = await this.prisma.founderNode.findFirst({
        where: { userId },
        orderBy: { lastSeenAt: 'desc' },
      });
      const nodeOnline =
        node?.lastSeenAt && Date.now() - node.lastSeenAt.getTime() < 300_000;
      if (nodeOnline && node) {
        await this.prisma.founderNodeSyncJob.create({
          data: {
            userId,
            nodeId: node.nodeId,
            kind: FounderNodeSyncJobKind.PULL_VAULT_MERGE,
            payload: { reason: 'import_wizard', repoFullName: repo },
            status: 'PENDING',
            expiresAt: new Date(Date.now() + 86400000),
          },
        });
        this.patchStep(job, 'vault_seed', {
          status: 'done',
          detail: 'Vault merge job queued on Founder Node',
        });
      } else {
        this.patchStep(job, 'vault_seed', {
          status: 'skipped',
          detail: 'Pair Founder Node to seed vault locally',
        });
      }

      this.patchStep(job, 'complete', { status: 'done', detail: 'Import wizard finished' });
      job.status = 'complete';
      job.completedAt = new Date().toISOString();
      job.summary = formatImportSummary(job);

      await this.prisma.founderBuilderSettings.updateMany({
        where: { userId },
        data: { computePlaneMode: ComputePlaneMode.HYBRID },
      });
    } catch (err) {
      job.status = 'failed';
      job.summary = err instanceof Error ? err.message : 'Import failed';
      const running = job.steps.find((s) => s.status === 'running');
      if (running) {
        running.status = 'error';
        running.detail = job.summary;
      }
    }

    state.import = job;
    await this.saveState(userId, state);
    return job;
  }

  async recordLocalStackFromHeartbeat(
    userId: string,
    input: LocalStackState & { label?: string },
  ) {
    const state = await this.getState(userId);
    state.localStack = {
      ...state.localStack,
      ...input,
      updatedAt: new Date().toISOString(),
    };
    if (input.enabled) {
      await this.prisma.founderBuilderSettings.updateMany({
        where: { userId },
        data: { computePlaneMode: ComputePlaneMode.LOCAL },
      });
    }
    return this.saveState(userId, state);
  }

  async getCloudStatus(userId: string) {
    const [state, settings, node] = await Promise.all([
      this.getState(userId),
      this.prisma.founderBuilderSettings.findUnique({
        where: { userId },
        select: { computePlaneMode: true, onboardingPath: true },
      }),
      this.prisma.founderNode.findFirst({
        where: { userId },
        orderBy: { lastSeenAt: 'desc' },
      }),
    ]);
    const nodeOnline =
      Boolean(node?.lastSeenAt) && Date.now() - (node!.lastSeenAt?.getTime() ?? 0) < 300_000;

    return {
      computePlaneMode: settings?.computePlaneMode ?? 'CLOUD',
      onboardingPath: settings?.onboardingPath ?? null,
      localStack: state.localStack,
      import: state.import,
      importComplete: importJobComplete(state.import),
      nodeOnline,
      missionControlUrl: state.localStack?.running
        ? state.localStack.webUrl ?? 'http://127.0.0.1:3000'
        : null,
    };
  }
}
