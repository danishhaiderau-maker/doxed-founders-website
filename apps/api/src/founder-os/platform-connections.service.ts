import { BadRequestException, Injectable } from '@nestjs/common';
import {
  getPlatformToggles,
  normalizePlatformConnections,
  patchPlatformConnections,
  PLATFORM_HUB_PROVIDERS,
  type PlatformConnectionToggles,
} from '@dcf/utils';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
import { PrismaService } from '../prisma/prisma.service';
import { FounderOsIntegrationService } from './founder-os-integration.service';

@Injectable()
export class PlatformConnectionsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly crypto: CredentialsCryptoService,
    private readonly integrations: FounderOsIntegrationService,
  ) {}

  async getHub(userId: string) {
    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    const toggles = normalizePlatformConnections(settings?.platformConnections);
    const creds = await this.prisma.integrationCredential.findMany({ where: { userId } });
    const gh = await this.prisma.gitHubConnection.findUnique({ where: { userId } });
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      select: { githubRepoFullName: true },
    });
    const node = await this.prisma.founderNode.findFirst({
      where: { userId },
      orderBy: { lastSeenAt: 'desc' },
    });

    const githubConnected = Boolean(
      (gh?.repoFullName ?? founder?.githubRepoFullName) &&
        !String(gh?.repoFullName ?? founder?.githubRepoFullName).endsWith('/pending-setup'),
    );
    const nodeOnline =
      Boolean(node?.lastSeenAt) && Date.now() - (node!.lastSeenAt?.getTime() ?? 0) < 300_000;

    const providers = PLATFORM_HUB_PROVIDERS.map((p) => {
      const cred = creds.find((c) => c.provider === p.credentialKey);
      const connected =
        p.key === 'github'
          ? githubConnected
          : p.key === 'founder_node'
            ? nodeOnline
            : Boolean(cred?.verifiedAt);
      const state = getPlatformToggles(toggles, p.key);
      const meta = (cred?.metadata as Record<string, unknown> | null) ?? null;
      return {
        key: p.key,
        label: p.label,
        description: p.description,
        connectType: p.connectType,
        connected,
        accountName:
          p.key === 'github'
            ? (gh?.repoFullName ?? founder?.githubRepoFullName ?? null)
            : p.key === 'founder_node'
              ? (node?.label ?? null)
              : ((meta?.accountName as string | undefined) ?? null),
        webhookUrl:
          p.key !== 'github' && p.key !== 'founder_node' && cred?.webhookSecret
            ? `/api/founder-os/webhooks/deploy/${cred.webhookSecret}`
            : null,
        toggles: {
          publish: state.publish,
          syncBack: state.syncBack,
          aiContext: state.aiContext,
        },
        health: {
          ok: state.healthOk,
          detail: state.healthDetail ?? null,
          checkedAt: state.lastHealthAt ?? null,
        },
      };
    });

    return {
      providers,
      onboardingPath: settings?.onboardingPath ?? null,
      computePlaneMode: settings?.computePlaneMode ?? 'CLOUD',
    };
  }

  async updateToggles(userId: string, provider: string, patch: Partial<PlatformConnectionToggles>) {
    const config = PLATFORM_HUB_PROVIDERS.find((p) => p.key === provider);
    if (!config) throw new BadRequestException('Unknown platform provider');

    const settings = await this.prisma.founderBuilderSettings.upsert({
      where: { userId },
      create: { userId },
      update: {},
    });
    const current = normalizePlatformConnections(settings.platformConnections);
    const next = patchPlatformConnections(current, provider, patch);

    await this.prisma.founderBuilderSettings.update({
      where: { userId },
      data: { platformConnections: next },
    });

    return { provider, toggles: getPlatformToggles(next, provider) };
  }

  async ensureDefaultsOnConnect(userId: string, provider: string) {
    const config = PLATFORM_HUB_PROVIDERS.find((p) => p.key === provider);
    if (!config) return;
    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    const current = normalizePlatformConnections(settings?.platformConnections);
    if (current[provider]) return;
    const next = patchPlatformConnections(current, provider, {
      publish: false,
      syncBack: false,
      aiContext: true,
    });
    await this.prisma.founderBuilderSettings.upsert({
      where: { userId },
      create: { userId, platformConnections: next },
      update: { platformConnections: next },
    });
  }

  async runHealthCheck(userId: string, provider: string) {
    const config = PLATFORM_HUB_PROVIDERS.find((p) => p.key === provider);
    if (!config) throw new BadRequestException('Unknown platform provider');

    let result: { ok: boolean; detail: string };

    if (provider === 'github') {
      const gh = await this.prisma.gitHubConnection.findUnique({ where: { userId } });
      const founder = await this.prisma.founder.findUnique({
        where: { userId },
        select: { githubRepoFullName: true },
      });
      const repo = gh?.repoFullName ?? founder?.githubRepoFullName;
      const ok = Boolean(repo && !String(repo).endsWith('/pending-setup'));
      result = { ok, detail: ok ? `Repo linked: ${repo}` : 'No GitHub repo connected' };
    } else if (provider === 'founder_node') {
      const node = await this.prisma.founderNode.findFirst({
        where: { userId },
        orderBy: { lastSeenAt: 'desc' },
      });
      const online =
        Boolean(node?.lastSeenAt) && Date.now() - (node!.lastSeenAt?.getTime() ?? 0) < 300_000;
      result = {
        ok: online,
        detail: online ? `${node!.label} online` : 'Founder Node not paired or offline',
      };
    } else {
      const cred = await this.prisma.integrationCredential.findUnique({
        where: { userId_provider: { userId, provider: config.credentialKey } },
      });
      if (!cred?.token) {
        result = { ok: false, detail: 'Not connected — add token in connect hub' };
      } else {
        const token = this.crypto.decrypt(cred.token);
        if (!token) {
          result = { ok: false, detail: 'Stored credential could not be decrypted' };
        } else {
          const meta = cred.metadata as Record<string, unknown> | null;
          result = await this.integrations.verifyProviderHealth(
            config.credentialKey,
            token,
            (meta?.projectName as string | undefined) ?? undefined,
          );
        }
      }
    }

    const settings = await this.prisma.founderBuilderSettings.upsert({
      where: { userId },
      create: { userId },
      update: {},
    });
    const current = normalizePlatformConnections(settings.platformConnections);
    const next = patchPlatformConnections(current, provider, {
      healthOk: result.ok,
      healthDetail: result.detail,
    });
    await this.prisma.founderBuilderSettings.update({
      where: { userId },
      data: { platformConnections: next },
    });

    return { provider, ...result, checkedAt: new Date().toISOString() };
  }
}
