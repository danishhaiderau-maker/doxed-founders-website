import { BadRequestException, Injectable } from '@nestjs/common';
import { randomBytes } from 'crypto';
import { Prisma } from '@prisma/client';
import { INTEGRATION_PROVIDERS } from '@dcf/utils';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
import { PrismaService } from '../prisma/prisma.service';

type VerifyResult = { accountName: string; metadata: Record<string, unknown> };

@Injectable()
export class FounderOsIntegrationService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly crypto: CredentialsCryptoService,
  ) {}

  getProviderConfigs() {
    return INTEGRATION_PROVIDERS.map((p) => ({
      key: p.key,
      label: p.label,
      connectType: p.connectType,
      reputationBoost: p.reputationBoost,
      billTip: p.billTip,
      fields: p.fields,
    }));
  }

  async connectIntegration(
    userId: string,
    input: { provider: string; token?: string; repoFullName?: string; projectName?: string },
  ) {
    const config = INTEGRATION_PROVIDERS.find((p) => p.key === input.provider);
    if (!config) throw new BadRequestException('Unknown provider');

    if (config.connectType === 'toggle') {
      await this.upsertCredential(userId, input.provider, null, {
        accountName: 'Founder Copilot enabled',
        enabledAt: new Date().toISOString(),
      });
      await this.upsertAppStatus(userId, input.provider, true, { accountName: 'Founder Copilot enabled' });
      return { success: true, provider: input.provider, accountName: 'Founder Copilot enabled' };
    }

    if (config.connectType === 'oauth') {
      throw new BadRequestException('Connect X via Sign in with X on login/register.');
    }

    if (config.connectType === 'repo') {
      throw new BadRequestException('Use GitHub connect endpoint for repositories.');
    }

    if (!input.token?.trim()) throw new BadRequestException('API token required');

    const verified = await this.verifyToken(input.provider, input.token.trim(), input.projectName);
    const webhookSecret = randomBytes(16).toString('hex');

    await this.upsertCredential(userId, input.provider, input.token.trim(), {
      ...verified.metadata,
      accountName: verified.accountName,
      projectName: input.projectName ?? null,
      webhookSecret,
    });

    await this.upsertAppStatus(userId, input.provider, true, {
      accountName: verified.accountName,
      projectName: input.projectName ?? null,
      webhookUrl: `/api/founder-os/webhooks/deploy/${webhookSecret}`,
    });

    return {
      success: true,
      provider: input.provider,
      accountName: verified.accountName,
      webhookUrl: `/api/founder-os/webhooks/deploy/${webhookSecret}`,
    };
  }

  async disconnectIntegration(userId: string, provider: string) {
    await this.prisma.integrationCredential.deleteMany({ where: { userId, provider } });
    await this.upsertAppStatus(userId, provider, false, null);
    return { success: true };
  }

  async findByWebhookSecret(secret: string) {
    return this.prisma.integrationCredential.findFirst({
      where: { webhookSecret: secret },
      include: {
        user: {
          include: {
            founder: {
              include: { projects: { where: { approved: true }, take: 1 } },
            },
          },
        },
      },
    });
  }

  private async verifyToken(
    provider: string,
    token: string,
    projectName?: string,
  ): Promise<VerifyResult> {
    switch (provider) {
      case 'vercel':
        return this.verifyVercel(token, projectName);
      case 'railway':
        return this.verifyRailway(token, projectName);
      case 'neon':
        return this.verifyNeon(token, projectName);
      case 'digitalocean':
        return this.verifyDigitalOcean(token);
      case 'supabase':
        return this.verifySupabase(token, projectName);
      default:
        throw new BadRequestException(`Verification not implemented for ${provider}`);
    }
  }

  private async verifyVercel(token: string, projectName?: string): Promise<VerifyResult> {
    const res = await fetch('https://api.vercel.com/v2/user', {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) throw new BadRequestException('Invalid Vercel token');
    const data = (await res.json()) as { user?: { username?: string; email?: string } };
    const accountName = data.user?.username ?? data.user?.email ?? 'Vercel account';
    return { accountName, metadata: { projectName: projectName ?? null } };
  }

  private async verifyRailway(token: string, projectName?: string): Promise<VerifyResult> {
    const res = await fetch('https://backboard.railway.app/graphql/v2', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ query: '{ me { email name } }' }),
    });
    if (!res.ok) throw new BadRequestException('Invalid Railway token');
    const data = (await res.json()) as { data?: { me?: { email?: string; name?: string } } };
    if (data.data?.me == null) throw new BadRequestException('Invalid Railway token');
    const accountName = data.data.me.name ?? data.data.me.email ?? 'Railway account';
    return { accountName, metadata: { projectName: projectName ?? null } };
  }

  private async verifyNeon(token: string, projectName?: string): Promise<VerifyResult> {
    const res = await fetch('https://console.neon.tech/api/v2/projects?limit=1', {
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' },
    });
    if (!res.ok) throw new BadRequestException('Invalid Neon API key');
    const data = (await res.json()) as { projects?: { name: string }[] };
    const accountName = data.projects?.[0]?.name ?? projectName ?? 'Neon project';
    return { accountName, metadata: { projectName: projectName ?? data.projects?.[0]?.name ?? null } };
  }

  private async verifyDigitalOcean(token: string): Promise<VerifyResult> {
    const res = await fetch('https://api.digitalocean.com/v2/account', {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) throw new BadRequestException('Invalid DigitalOcean token');
    const data = (await res.json()) as { account?: { email?: string; uuid?: string } };
    const accountName = data.account?.email ?? 'DigitalOcean account';
    return { accountName, metadata: { accountUuid: data.account?.uuid ?? null } };
  }

  private async verifySupabase(token: string, projectName?: string): Promise<VerifyResult> {
    const res = await fetch('https://api.supabase.com/v1/projects', {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) throw new BadRequestException('Invalid Supabase access token');
    const data = (await res.json()) as { name?: string; id?: string }[] | { projects?: unknown };
    const projects = Array.isArray(data) ? data : [];
    const match = projectName
      ? projects.find((p) => p.id === projectName || p.name === projectName)
      : projects[0];
    const accountName = match?.name ?? projectName ?? 'Supabase project';
    return {
      accountName,
      metadata: { projectRef: match?.id ?? projectName ?? null },
    };
  }

  private async upsertCredential(
    userId: string,
    provider: string,
    token: string | null,
    metadata: Record<string, unknown>,
  ) {
    const existing = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider } },
    });
    const webhookSecret =
      (existing?.webhookSecret as string | undefined) ??
      (metadata.webhookSecret as string | undefined) ??
      randomBytes(16).toString('hex');

    const existingMetadata =
      existing?.metadata && typeof existing.metadata === 'object' && !Array.isArray(existing.metadata)
        ? (existing.metadata as Record<string, unknown>)
        : {};
    const storedToken = token ? this.crypto.encrypt(token) : (existing?.token ?? null);
    const storedMetadata =
      token || !existing?.token
        ? metadata
        : {
            ...existingMetadata,
            founderCopilotToggle: true,
            founderCopilotEnabledAt:
              typeof metadata.enabledAt === 'string' ? metadata.enabledAt : new Date().toISOString(),
          };

    await this.prisma.integrationCredential.upsert({
      where: { userId_provider: { userId, provider } },
      create: {
        userId,
        provider,
        token: storedToken,
        metadata: storedMetadata as Prisma.InputJsonValue,
        webhookSecret,
        verifiedAt: new Date(),
      },
      update: {
        token: storedToken,
        metadata: storedMetadata as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
    });
  }

  private async upsertAppStatus(
    userId: string,
    provider: string,
    connected: boolean,
    metadata: Record<string, unknown> | null,
  ) {
    const config = INTEGRATION_PROVIDERS.find((p) => p.key === provider);
    await this.prisma.connectedAppStatus.upsert({
      where: { userId_provider: { userId, provider } },
      create: {
        userId,
        provider,
        connected,
        label: config?.label ?? provider,
        metadata: (metadata ?? undefined) as Prisma.InputJsonValue | undefined,
      },
      update: { connected, metadata: (metadata ?? undefined) as Prisma.InputJsonValue | undefined },
    });
  }
}
