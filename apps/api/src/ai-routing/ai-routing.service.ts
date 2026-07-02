import { BadRequestException, Injectable, Logger, NotFoundException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
import {
  AI_SECTION_DEFAULT_PROVIDER,
  AI_SECTION_LABELS,
  AI_SECTION_SLUGS,
  type AiSectionSlug,
  PROVIDER_SEEDS,
} from './ai-routing.constants';

export type ProviderRow = {
  key: string;
  label: string;
  baseUrl: string;
  defaultModel: string;
  adapter: string;
  enabled: boolean;
  hasKey: boolean;
  updatedAt: string;
};

export type SectionRoutingRow = {
  section: string;
  label: string;
  providerKey: string;
  providerLabel: string;
  providerEnabled: boolean;
  providerHasKey: boolean;
  updatedAt: string;
};

@Injectable()
export class AiRoutingService {
  private readonly logger = new Logger(AiRoutingService.name);
  private seeded = false;

  constructor(
    private readonly prisma: PrismaService,
    private readonly crypto: CredentialsCryptoService,
  ) {}

  /**
   * Idempotently seed the known providers + section rows on first access.
   * Providers are seeded enabled=false with no key. Sections are seeded with
   * their default provider so behaviour matches the pre-routing setup.
   * Safe to call repeatedly — uses upserts.
   */
  async seedDefaults(): Promise<void> {
    if (this.seeded) return;
    this.seeded = true;
    try {
      for (const seed of PROVIDER_SEEDS) {
        await this.prisma.aiRoutingProvider.upsert({
          where: { key: seed.key },
          create: {
            key: seed.key,
            label: seed.label,
            baseUrl: seed.baseUrl,
            defaultModel: seed.defaultModel,
            adapter: seed.adapter,
            enabled: false,
          },
          update: {},
        });
      }
      for (const section of AI_SECTION_SLUGS) {
        const defaultKey = AI_SECTION_DEFAULT_PROVIDER[section];
        await this.prisma.aiSectionRouting.upsert({
          where: { section },
          create: { section, providerKey: defaultKey },
          update: {},
        });
      }
    } catch (err) {
      this.seeded = false;
      this.logger.warn(`seedDefaults failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  // ─── Providers ────────────────────────────────────────────────────────────

  async listProviders(): Promise<ProviderRow[]> {
    await this.seedDefaults();
    const rows = await this.prisma.aiRoutingProvider.findMany({ orderBy: { key: 'asc' } });
    return rows.map((r) => ({
      key: r.key,
      label: r.label,
      baseUrl: r.baseUrl,
      defaultModel: r.defaultModel,
      adapter: r.adapter,
      enabled: r.enabled,
      hasKey: Boolean(r.encryptedApiKey),
      updatedAt: r.updatedAt.toISOString(),
    }));
  }

  async upsertProvider(
    actorUserId: string,
    input: {
      key: string;
      label?: string;
      baseUrl?: string;
      defaultModel?: string;
      adapter?: string;
      apiKey?: string | null;
      enabled?: boolean;
    },
  ): Promise<ProviderRow> {
    await this.seedDefaults();
    const key = input.key.trim().toLowerCase();
    if (!/^[a-z0-9_-]{2,32}$/.test(key)) {
      throw new BadRequestException('Provider key must be 2-32 chars: a-z, 0-9, _ or -');
    }
    const existing = await this.prisma.aiRoutingProvider.findUnique({ where: { key } });

    // Key handling: empty/null → clear; non-empty → encrypt+store; undefined → keep.
    let encryptedApiKey: string | null | undefined = undefined;
    if (input.apiKey !== undefined) {
      if (input.apiKey == null || input.apiKey.trim() === '') {
        encryptedApiKey = null;
      } else {
        const trimmed = input.apiKey.trim();
        if (trimmed.length < 8) throw new BadRequestException('API key is too short');
        encryptedApiKey = this.crypto.encrypt(trimmed);
      }
    }

    const data: Record<string, unknown> = {};
    if (input.label !== undefined) data.label = input.label;
    if (input.baseUrl !== undefined) data.baseUrl = input.baseUrl;
    if (input.defaultModel !== undefined) data.defaultModel = input.defaultModel;
    if (input.adapter !== undefined) data.adapter = input.adapter;
    if (input.enabled !== undefined) data.enabled = input.enabled;
    if (encryptedApiKey !== undefined) data.encryptedApiKey = encryptedApiKey;

    if (existing) {
      const updated = await this.prisma.aiRoutingProvider.update({ where: { key }, data });
      await this.recordAdminAction(actorUserId, 'ai_routing_provider_upsert', key, input);
      return this.toProviderRow(updated);
    }
    if (!input.label) throw new BadRequestException('label is required when creating a new provider');
    if (!input.baseUrl) throw new BadRequestException('baseUrl is required when creating a new provider');
    if (!input.defaultModel) throw new BadRequestException('defaultModel is required when creating a new provider');
    const created = await this.prisma.aiRoutingProvider.create({
      data: {
        key,
        label: input.label,
        baseUrl: input.baseUrl,
        defaultModel: input.defaultModel,
        adapter: input.adapter ?? 'openai_compat',
        enabled: input.enabled ?? false,
        encryptedApiKey: encryptedApiKey ?? null,
      },
    });
    await this.recordAdminAction(actorUserId, 'ai_routing_provider_create', key, input);
    return this.toProviderRow(created);
  }

  async removeProvider(actorUserId: string, key: string): Promise<{ ok: true }> {
    await this.seedDefaults();
    // Reassign any sections pointing at this provider back to the deepseek default
    // before deleting (the FK is ON DELETE CASCADE, but we want graceful fallback).
    const sections = await this.prisma.aiSectionRouting.findMany({ where: { providerKey: key } });
    for (const s of sections) {
      await this.prisma.aiSectionRouting.update({
        where: { section: s.section },
        data: { providerKey: 'deepseek' },
      });
    }
    try {
      await this.prisma.aiRoutingProvider.delete({ where: { key } });
      await this.recordAdminAction(actorUserId, 'ai_routing_provider_remove', key, null);
    } catch {
      /* already gone */
    }
    return { ok: true };
  }

  /** Decrypt the API key for a provider. Internal — never returned to the client. */
  async getDecryptedKey(providerKey: string): Promise<string | null> {
    const row = await this.prisma.aiRoutingProvider.findUnique({ where: { key: providerKey } });
    if (!row?.encryptedApiKey) return null;
    return this.crypto.decrypt(row.encryptedApiKey);
  }

  // ─── Section routing ──────────────────────────────────────────────────────

  async listSections(): Promise<SectionRoutingRow[]> {
    await this.seedDefaults();
    const rows = await this.prisma.aiSectionRouting.findMany({
      include: { provider: true },
      orderBy: { section: 'asc' },
    });
    return rows.map((r) => ({
      section: r.section,
      label: AI_SECTION_LABELS[r.section as AiSectionSlug] ?? r.section,
      providerKey: r.providerKey,
      providerLabel: r.provider?.label ?? r.providerKey,
      providerEnabled: r.provider?.enabled ?? false,
      providerHasKey: Boolean(r.provider?.encryptedApiKey),
      updatedAt: r.updatedAt.toISOString(),
    }));
  }

  async setSectionProvider(
    actorUserId: string,
    section: string,
    providerKey: string,
  ): Promise<SectionRoutingRow> {
    await this.seedDefaults();
    const provider = await this.prisma.aiRoutingProvider.findUnique({ where: { key: providerKey } });
    if (!provider) throw new NotFoundException(`Provider "${providerKey}" not found`);
    const row = await this.prisma.aiSectionRouting.upsert({
      where: { section },
      create: { section, providerKey },
      update: { providerKey },
      include: { provider: true },
    });
    await this.recordAdminAction(actorUserId, 'ai_routing_section_update', section, { providerKey });
    return this.toSectionRow(row);
  }

  /** Public-ish: resolve the routed provider for a single section. */
  async getSectionRouting(section: string): Promise<SectionRoutingRow | null> {
    await this.seedDefaults();
    const row = await this.prisma.aiSectionRouting.findUnique({
      where: { section },
      include: { provider: true },
    });
    if (!row) return null;
    return this.toSectionRow(row);
  }

  // ─── Helpers ──────────────────────────────────────────────────────────────

  private toProviderRow(r: {
    key: string;
    label: string;
    baseUrl: string;
    defaultModel: string;
    adapter: string;
    enabled: boolean;
    encryptedApiKey: string | null;
    updatedAt: Date;
  }): ProviderRow {
    return {
      key: r.key,
      label: r.label,
      baseUrl: r.baseUrl,
      defaultModel: r.defaultModel,
      adapter: r.adapter,
      enabled: r.enabled,
      hasKey: Boolean(r.encryptedApiKey),
      updatedAt: r.updatedAt.toISOString(),
    };
  }

  private toSectionRow(r: {
    section: string;
    providerKey: string;
    updatedAt: Date;
    provider: { label: string; enabled: boolean; encryptedApiKey: string | null } | null;
  }): SectionRoutingRow {
    return {
      section: r.section,
      label: AI_SECTION_LABELS[r.section as AiSectionSlug] ?? r.section,
      providerKey: r.providerKey,
      providerLabel: r.provider?.label ?? r.providerKey,
      providerEnabled: r.provider?.enabled ?? false,
      providerHasKey: Boolean(r.provider?.encryptedApiKey),
      updatedAt: r.updatedAt.toISOString(),
    };
  }

  private async recordAdminAction(
    adminId: string,
    action: string,
    entityId: string,
    metadata: unknown,
  ): Promise<void> {
    try {
      await this.prisma.adminAction.create({
        data: {
          adminId,
          action,
          entityType: 'ai_routing',
          entityId,
          metadata: metadata as never,
        },
      });
    } catch {
      /* non-fatal */
    }
  }
}
