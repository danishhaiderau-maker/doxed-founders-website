import { ForbiddenException, Inject, Injectable, forwardRef } from '@nestjs/common';
import { SuggestedUpdateStatus } from '@prisma/client';
import {
  buildControlPlaneReadiness,
  memoryStoragePrivacyLabel,
  type ControlPlaneModeKey,
  type PlatformSyncItem,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
import { BuilderService } from '../builder/builder.service';
import { FounderOsService } from '../founder-os/founder-os.service';
import { FounderCopilotService } from './founder-copilot.service';

type AutopilotStep = { step: string; ok: boolean; detail: string };

@Injectable()
export class FounderAutopilotService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly crypto: CredentialsCryptoService,
    private readonly builder: BuilderService,
    private readonly founderOs: FounderOsService,
    @Inject(forwardRef(() => FounderCopilotService))
    private readonly copilot: FounderCopilotService,
  ) {}

  async getPlatformSyncStatus(userId: string) {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const [settings, builderSettings, connectedApps, worker] = await Promise.all([
      this.prisma.founderBuilderSettings.findUnique({ where: { userId } }),
      this.builder.getSettings(userId),
      this.founderOs.getConnectedApps(userId, founder),
      this.builder.getWorkerStatus(userId),
    ]);

    const gh = connectedApps.find((a) => a.provider === 'github');
    const vercel = connectedApps.find((a) => a.provider === 'vercel');
    const railway = connectedApps.find((a) => a.provider === 'railway');
    const neon = connectedApps.find((a) => a.provider === 'neon');

    const chatProviders = builderSettings.providers.filter(
      (p) =>
        p.connected &&
        p.key !== 'RULE_BASED' &&
        (p.connectMode === 'api_key' || p.connectMode === 'founder_node'),
    );

    const platforms: PlatformSyncItem[] = [
      {
        key: 'github',
        label: 'GitHub',
        connected: gh?.connected ?? false,
        detail: founder.githubRepoFullName ?? undefined,
        action: gh?.connected ? 'sync' : 'connect',
      },
      {
        key: 'neon',
        label: 'Neon',
        connected: neon?.connected ?? false,
        detail: neon?.accountName ?? undefined,
        action: neon?.connected ? 'ready' : 'connect',
      },
      {
        key: 'vercel',
        label: 'Vercel',
        connected: vercel?.connected ?? false,
        detail: vercel?.accountName ?? undefined,
        action: vercel?.connected ? 'ready' : 'connect',
      },
      {
        key: 'railway',
        label: 'Railway',
        connected: railway?.connected ?? false,
        detail: railway?.accountName ?? undefined,
        action: railway?.connected ? 'ready' : 'connect',
      },
      {
        key: 'memory',
        label: 'Memory',
        connected: true,
        detail: settings?.memoryStorageMode ?? 'PLATFORM',
        action: 'ready',
      },
      {
        key: 'llm',
        label: 'Chat AI',
        connected: chatProviders.length > 0,
        detail: builderSettings.defaultProvider,
        action: chatProviders.length > 0 ? 'ready' : 'connect',
      },
      {
        key: 'cursor',
        label: 'Code agent',
        connected: worker.connections.cursor || worker.connections.openHands,
        detail: worker.buildWorker,
        action: worker.buildWorker !== 'NONE' ? 'ready' : 'connect',
      },
    ];

    const pendingCount = await this.prisma.suggestedBuildUpdate.count({
      where: { founderId: founder.id, status: SuggestedUpdateStatus.PENDING },
    });

    const mode = (settings?.controlPlaneMode ?? 'FULL_STACK') as ControlPlaneModeKey;
    const controlPlane = buildControlPlaneReadiness({
      mode,
      githubConnected: gh?.connected ?? false,
      repoFullName: founder.githubRepoFullName,
      neonConnected: neon?.connected ?? false,
      vercelConnected: vercel?.connected ?? false,
      railwayConnected: railway?.connected ?? false,
      chatConnected: chatProviders.length > 0,
      chatProvider: builderSettings.defaultProvider,
      buildWorker: worker.buildWorker,
      autopilotEnabled: settings?.autopilotEnabled ?? false,
    });

    return {
      platforms,
      controlPlaneMode: mode,
      controlPlane,
      memoryStorageMode: settings?.memoryStorageMode ?? 'PLATFORM',
      memoryPrivacyNote: memoryStoragePrivacyLabel(settings?.memoryStorageMode ?? 'PLATFORM'),
      autopilotEnabled: settings?.autopilotEnabled ?? false,
      autopilotRedeployHosts: settings?.autopilotRedeployHosts ?? false,
      autoPublishOnEvent: settings?.autoPublishOnEvent ?? false,
      defaultProvider: builderSettings.defaultProvider,
      chatProviders: chatProviders.map((p) => ({ key: p.key, label: p.label, connected: p.connected })),
      buildWorker: worker.buildWorker,
      pendingPublishCount: pendingCount,
      repoFullName: founder.githubRepoFullName,
    };
  }

  async runAutopilot(userId: string, prompt?: string) {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    const forceFull =
      /\b(take full control|full autopilot|sync everything|push all updates?|push all)\b/i.test(
        prompt ?? '',
      );
    const redeploy =
      forceFull ||
      settings?.autopilotRedeployHosts ||
      /\b(deploy|vercel|railway|production|neon)\b/i.test(prompt ?? '');

    const steps: AutopilotStep[] = [];

    try {
      const gh = await this.founderOs.syncGitHubCommits(userId);
      steps.push({
        step: 'github_sync',
        ok: Boolean(gh?.synced),
        detail: gh?.synced
          ? gh.unchanged
            ? `GitHub up to date (${gh.commits?.length ?? 0} commits checked)`
            : `Synced ${gh.commits?.length ?? 0} commit(s) from repo`
          : gh?.reason ?? 'GitHub sync skipped',
      });
    } catch (err) {
      steps.push({
        step: 'github_sync',
        ok: false,
        detail: err instanceof Error ? err.message : 'GitHub sync failed',
      });
    }

    try {
      await this.founderOs.syncProjectMemory(userId);
      steps.push({
        step: 'memory_sync',
        ok: true,
        detail: `Memory refreshed (${settings?.memoryStorageMode ?? 'PLATFORM'}) → platform DB (Neon via API)`,
      });
    } catch (err) {
      steps.push({
        step: 'memory_sync',
        ok: false,
        detail: err instanceof Error ? err.message : 'Memory sync failed',
      });
    }

    steps.push(await this.tryNeonPlatform(userId));

    const pending = await this.prisma.suggestedBuildUpdate.findMany({
      where: { founderId: founder.id, status: SuggestedUpdateStatus.PENDING },
      orderBy: { createdAt: 'asc' },
    });

    let published = 0;
    const shouldPublish =
      forceFull ||
      settings?.autopilotEnabled ||
      settings?.autoPublishOnEvent ||
      /\b(publish|push updates|commit.*everywhere)\b/i.test(prompt ?? '');

    if (shouldPublish && pending.length > 0) {
      for (const s of pending) {
        try {
          await this.founderOs.publishSuggestedUpdate(userId, s.id, {
            buildFeed: true,
            x: true,
            community: true,
          });
          published += 1;
        } catch {
          /* continue other suggestions */
        }
      }
      steps.push({
        step: 'publish',
        ok: published > 0,
        detail:
          published > 0
            ? `Published ${published} build update(s) to feed, X, and community`
            : pending.length > 0
              ? 'Had pending updates but publish failed — check X/GitHub connections'
              : 'No pending updates to publish',
      });
    } else {
      steps.push({
        step: 'publish',
        ok: true,
        detail:
          pending.length > 0
            ? `${pending.length} update(s) waiting — enable Autopilot or say "publish all" to ship`
            : 'No pending build updates',
      });
    }

    if (redeploy) {
      const vercelStep = await this.tryRedeployVercel(userId);
      steps.push(vercelStep);
      const railwayStep = await this.tryRedeployRailway(userId);
      steps.push(railwayStep);
    }

    const memory = await this.copilot.getProjectMemory(userId);
    let builderDispatch: string | null = null;
    const worker = await this.builder.resolveBuildWorker(userId);
    if (
      (worker === 'CURSOR' || worker === 'OPENHANDS') &&
      (forceFull || settings?.autopilotEnabled || /\b(code|implement|cursor|full control)\b/i.test(prompt ?? ''))
    ) {
      const next = memory.suggestedNextStep?.trim();
      if (next) {
        const result = await this.builder.executeBuildTask(userId, {
          spec: next,
          cursorPrompt: next,
          repository: memory.repoFullName ?? undefined,
        });
        if (result.status === 'dispatched' && result.agentUrl) {
          builderDispatch = result.agentUrl;
          steps.push({
            step: 'builder_dispatch',
            ok: true,
            detail: `Dispatched ${result.worker} — ${result.agentUrl}`,
          });
        } else if (result.error) {
          steps.push({ step: 'builder_dispatch', ok: false, detail: result.error });
        }
      }
    }

    const status = await this.getPlatformSyncStatus(userId);
    const lines = [
      '**Hybrid control plane — sync complete**',
      '',
      `Mode: ${status.controlPlaneMode === 'FULL_STACK' ? 'Full stack' : 'Cursor-first'}`,
      ...status.controlPlane.legs.map(
        (l) => `${l.connected ? '✓' : '○'} **${l.label}** (${l.subtitle})${l.provider ? ` — ${l.provider}` : ''}`,
      ),
      '',
      '**Infrastructure**',
      ...steps.map((s) => `${s.ok ? '✓' : '✗'} ${s.step}: ${s.detail}`),
      '',
      `Memory: ${status.memoryPrivacyNote}`,
      builderDispatch ? `\nTrack code agent: ${builderDispatch}` : '',
      !settings?.autopilotEnabled && !forceFull
        ? '\nTip: Enable **Autopilot** in AI Stack for hands-free publish + redeploy.'
        : '',
    ].filter(Boolean);

    return {
      answer: lines.join('\n'),
      answerProvider: 'FOUNDER_OS',
      steps,
      published,
      builderDispatch,
      syncStatus: status,
    };
  }

  /** Read-only infrastructure snapshot for Social Hub drafts (no redeploy / publish). */
  async buildDraftInfrastructureSnapshot(userId: string) {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    const status = await this.getPlatformSyncStatus(userId);
    const gh = status.platforms.find((p) => p.key === 'github');

    const steps: AutopilotStep[] = [
      {
        step: 'github_sync',
        ok: gh?.connected ?? false,
        detail: gh?.connected
          ? status.repoFullName
            ? `Repo linked: ${status.repoFullName}`
            : 'GitHub connected — set default repo in Stack'
          : 'GitHub not connected — link in AI Stack',
      },
      {
        step: 'memory_sync',
        ok: true,
        detail: `Memory mode ${status.memoryStorageMode} — ${memoryStoragePrivacyLabel(status.memoryStorageMode)}`,
      },
    ];

    steps.push(await this.tryNeonPlatform(userId));
    steps.push(await this.checkVercelLinked(userId));
    steps.push(await this.checkRailwayLinked(userId));

    const pending = await this.prisma.suggestedBuildUpdate.count({
      where: { founderId: founder.id, status: SuggestedUpdateStatus.PENDING },
    });
    steps.push({
      step: 'publish',
      ok: true,
      detail:
        pending > 0
          ? `${pending} build update(s) queued — Autopilot ${settings?.autopilotEnabled ? 'can publish' : 'off; publish manually or enable Autopilot'}`
          : 'No pending build updates in queue',
    });

    return { steps, syncStatus: status };
  }

  private async checkVercelLinked(userId: string): Promise<AutopilotStep> {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'vercel' } },
    });
    if (!cred?.token) {
      return { step: 'vercel_deploy', ok: false, detail: 'Vercel not connected — add token in Stack hub' };
    }
    const token = this.crypto.decrypt(cred.token);
    if (!token) {
      return { step: 'vercel_deploy', ok: false, detail: 'Vercel token invalid — reconnect' };
    }
    const meta = (cred.metadata as { projectName?: string }) ?? {};
    const projectName = meta.projectName?.trim();
    return {
      step: 'vercel_deploy',
      ok: true,
      detail: projectName
        ? `Vercel linked (project ${projectName}) — production also deploys on Git push`
        : 'Vercel connected — production deploys follow Git push (add project name in Stack for API redeploy)',
    };
  }

  private async checkRailwayLinked(userId: string): Promise<AutopilotStep> {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'railway' } },
    });
    if (!cred?.token) {
      return { step: 'railway_deploy', ok: false, detail: 'Railway not connected — add token in Stack hub' };
    }
    const token = this.crypto.decrypt(cred.token);
    if (!token) {
      return { step: 'railway_deploy', ok: false, detail: 'Railway token invalid — reconnect' };
    }
    try {
      const query = `query { projects { edges { node { id name } } } }`;
      const res = await fetch('https://backboard.railway.app/graphql/v2', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ query }),
      });
      if (!res.ok) {
        return { step: 'railway_deploy', ok: false, detail: `Railway API error (${res.status})` };
      }
      const data = (await res.json()) as {
        data?: { projects?: { edges?: Array<{ node: { name: string } }> } };
      };
      const names = data.data?.projects?.edges?.map((e) => e.node.name) ?? [];
      return {
        step: 'railway_deploy',
        ok: names.length > 0,
        detail:
          names.length > 0
            ? `Railway linked (${names.slice(0, 3).join(', ')}${names.length > 3 ? '…' : ''}) — API reachable`
            : 'Railway token valid but no projects returned',
      };
    } catch (err) {
      return {
        step: 'railway_deploy',
        ok: false,
        detail: err instanceof Error ? err.message : 'Railway check failed',
      };
    }
  }

  private async tryNeonPlatform(userId: string): Promise<AutopilotStep> {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'neon' } },
    });
    if (!cred?.token) {
      return {
        step: 'neon_platform',
        ok: false,
        detail: 'Neon not linked in Stack — connect API key (app data still syncs if Railway DATABASE_URL is set)',
      };
    }
    const token = this.crypto.decrypt(cred.token);
    if (!token) {
      return { step: 'neon_platform', ok: false, detail: 'Neon token invalid — reconnect in Stack hub' };
    }
    try {
      const res = await fetch('https://console.neon.tech/api/v2/projects?limit=1', {
        headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' },
      });
      if (!res.ok) {
        return { step: 'neon_platform', ok: false, detail: `Neon API check failed (${res.status})` };
      }
      let dbOk = false;
      try {
        await this.prisma.$queryRaw`SELECT 1`;
        dbOk = true;
      } catch {
        dbOk = false;
      }
      const meta = (cred.metadata as { projectName?: string }) ?? {};
      return {
        step: 'neon_platform',
        ok: true,
        detail: `Neon project verified (${meta.projectName ?? 'linked'}) — platform memory ${dbOk ? 'written to Postgres' : 'API DB unreachable (check Railway DATABASE_URL)'}`,
      };
    } catch (err) {
      return {
        step: 'neon_platform',
        ok: false,
        detail: err instanceof Error ? err.message : 'Neon check failed',
      };
    }
  }

  private async tryRedeployVercel(userId: string): Promise<AutopilotStep> {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'vercel' } },
    });
    if (!cred?.token) {
      return { step: 'vercel_deploy', ok: false, detail: 'Vercel not connected — add token in Stack hub' };
    }
    const token = this.crypto.decrypt(cred.token);
    if (!token) {
      return { step: 'vercel_deploy', ok: false, detail: 'Vercel token invalid — reconnect' };
    }
    const meta = (cred.metadata as { projectName?: string }) ?? {};
    const projectName = meta.projectName?.trim();
    if (!projectName) {
      return {
        step: 'vercel_deploy',
        ok: true,
        detail:
          'Vercel connected — production deploys follow Git push (add project name in Stack for API redeploy)',
      };
    }
    try {
      const res = await fetch('https://api.vercel.com/v13/deployments', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name: projectName, target: 'production' }),
      });
      if (!res.ok) {
        const errText = await res.text().catch(() => '');
        return {
          step: 'vercel_deploy',
          ok: false,
          detail: `Vercel redeploy failed (${res.status}) — push to GitHub or redeploy in Vercel dashboard. ${errText.slice(0, 120)}`,
        };
      }
      const data = (await res.json()) as { url?: string; id?: string };
      return {
        step: 'vercel_deploy',
        ok: true,
        detail: `Vercel production deploy triggered${data.url ? ` — ${data.url}` : ''}`,
      };
    } catch (err) {
      return {
        step: 'vercel_deploy',
        ok: false,
        detail: err instanceof Error ? err.message : 'Vercel redeploy failed',
      };
    }
  }

  private async tryRedeployRailway(userId: string): Promise<AutopilotStep> {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'railway' } },
    });
    if (!cred?.token) {
      return { step: 'railway_deploy', ok: false, detail: 'Railway not connected — add token in Stack hub' };
    }
    const token = this.crypto.decrypt(cred.token);
    if (!token) {
      return { step: 'railway_deploy', ok: false, detail: 'Railway token invalid — reconnect' };
    }
    const meta = (cred.metadata as { projectName?: string }) ?? {};
    const preferredName = meta.projectName?.trim();

    try {
      const query = `query {
        projects { edges { node {
          id name
          environments { edges { node { id name } } }
          services { edges { node { id name } } }
        } } }
      }`;
      const res = await fetch('https://backboard.railway.app/graphql/v2', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ query }),
      });
      if (!res.ok) {
        return { step: 'railway_deploy', ok: false, detail: `Railway API error (${res.status})` };
      }
      const data = (await res.json()) as {
        data?: {
          projects?: {
            edges?: Array<{
              node: {
                id: string;
                name: string;
                environments: { edges: Array<{ node: { id: string; name: string } }> };
                services: { edges: Array<{ node: { id: string; name: string } }> };
              };
            }>;
          };
        };
      };
      const projects = data.data?.projects?.edges?.map((e) => e.node) ?? [];
      const project =
        projects.find((p) =>
          p.services.edges.some((s) => s.node.name === (preferredName ?? 'doxed-founders-website')),
        ) ?? projects[0];
      if (!project) {
        return { step: 'railway_deploy', ok: false, detail: 'No Railway project found for this token' };
      }
      const env =
        project.environments.edges.find((e) => e.node.name === 'production')?.node ??
        project.environments.edges[0]?.node;
      const service =
        project.services.edges.find(
          (s) => s.node.name === (preferredName ?? 'doxed-founders-website'),
        )?.node ?? project.services.edges[0]?.node;
      if (!env || !service) {
        return { step: 'railway_deploy', ok: false, detail: 'Railway env/service not found' };
      }

      const redeployRes = await fetch('https://backboard.railway.app/graphql/v2', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          query: `mutation($serviceId: String!, $environmentId: String!) {
            serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
          }`,
          variables: { serviceId: service.id, environmentId: env.id },
        }),
      });
      if (!redeployRes.ok) {
        return { step: 'railway_deploy', ok: false, detail: `Railway redeploy failed (${redeployRes.status})` };
      }
      return {
        step: 'railway_deploy',
        ok: true,
        detail: `Railway redeploy triggered on ${project.name} / ${service.name}`,
      };
    } catch (err) {
      return {
        step: 'railway_deploy',
        ok: false,
        detail: err instanceof Error ? err.message : 'Railway redeploy failed',
      };
    }
  }
}
