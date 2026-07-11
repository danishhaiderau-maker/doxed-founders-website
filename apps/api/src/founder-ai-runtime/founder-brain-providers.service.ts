import { BadRequestException, Injectable, Logger, OnModuleInit } from '@nestjs/common';
import { parseFounderBrainMode, type FounderBrainMode } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { FounderPromoService } from '../founder-os/founder-promo.service';
import { AiRoutingService } from '../ai-routing/ai-routing.service';
import { getGlmApiBaseUrl } from '../founder-os/glm-config';
import type { ModelRoute } from './founder-ai-runtime.types';
import {
  DEFAULT_FOUNDER_BRAIN_PROVIDERS_CONFIG,
  FOUNDER_BRAIN_PROVIDER_ALLOWLIST,
  type FounderBrainProviderKeyStatus,
  type FounderBrainProvidersAdminView,
  type FounderBrainProvidersConfig,
  type FounderBrainProviderSlug,
  type FounderBrainProviderTestResult,
} from './founder-brain-providers.types';

const DEEPSEEK_CHAT_URL = 'https://api.deepseek.com/chat/completions';

@Injectable()
export class FounderBrainProvidersService implements OnModuleInit {
  private readonly logger = new Logger(FounderBrainProvidersService.name);
  private cachedConfig: FounderBrainProvidersConfig = { ...DEFAULT_FOUNDER_BRAIN_PROVIDERS_CONFIG };

  constructor(
    private readonly prisma: PrismaService,
    private readonly founderPromo: FounderPromoService,
    private readonly aiRouting: AiRoutingService,
  ) {}

  async onModuleInit(): Promise<void> {
    await this.refreshCache();
  }

  getSyncConfig(): FounderBrainProvidersConfig {
    return this.cachedConfig;
  }

  isTwoModelRoutingEnabled(): boolean {
    return this.cachedConfig.twoModelRoutingEnabled;
  }

  async refreshCache(): Promise<void> {
    try {
      const row = await this.prisma.platformSettings.findUnique({
        where: { id: 'default' },
        select: { founderBrainProvidersJson: true },
      });
      this.cachedConfig = this.mergeConfig(row?.founderBrainProvidersJson);
    } catch (err) {
      this.logger.warn(
        `refreshCache failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      this.cachedConfig = { ...DEFAULT_FOUNDER_BRAIN_PROVIDERS_CONFIG };
    }
  }

  async getAdminView(): Promise<FounderBrainProvidersAdminView> {
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    const config = this.mergeConfig(row?.founderBrainProvidersJson);
    this.cachedConfig = config;
    const [deepseekStatus, glmStatus] = await Promise.all([
      this.resolveKeyStatus('deepseek'),
      this.resolveKeyStatus('glm'),
    ]);
    return {
      ...config,
      keys: { deepseek: deepseekStatus, glm: glmStatus },
      glmApiBase: getGlmApiBaseUrl(),
      updatedAt: row?.updatedAt?.toISOString() ?? null,
    };
  }

  async updateSettings(
    userId: string,
    patch: Partial<{
      twoModelRoutingEnabled: boolean;
      deepseekFastModel: string;
      deepseekCodingModel: string;
      glmFastModel: string;
      glmCodingModel: string;
      defaultMode: FounderBrainMode;
    }>,
  ): Promise<FounderBrainProvidersAdminView> {
    const current = (await this.getAdminView()) as FounderBrainProvidersConfig;
    const next: FounderBrainProvidersConfig = { ...current };

    if (patch.twoModelRoutingEnabled !== undefined) {
      next.twoModelRoutingEnabled = patch.twoModelRoutingEnabled;
    }
    if (patch.deepseekFastModel !== undefined) {
      next.deepseekFastModel = patch.deepseekFastModel.trim() || DEFAULT_FOUNDER_BRAIN_PROVIDERS_CONFIG.deepseekFastModel;
    }
    if (patch.deepseekCodingModel !== undefined) {
      next.deepseekCodingModel = patch.deepseekCodingModel.trim() || DEFAULT_FOUNDER_BRAIN_PROVIDERS_CONFIG.deepseekCodingModel;
    }
    if (patch.glmFastModel !== undefined) {
      next.glmFastModel = patch.glmFastModel.trim() || DEFAULT_FOUNDER_BRAIN_PROVIDERS_CONFIG.glmFastModel;
    }
    if (patch.glmCodingModel !== undefined) {
      next.glmCodingModel = patch.glmCodingModel.trim() || DEFAULT_FOUNDER_BRAIN_PROVIDERS_CONFIG.glmCodingModel;
    }
    if (patch.defaultMode !== undefined) {
      next.defaultMode = patch.defaultMode;
    }

    await this.prisma.platformSettings.upsert({
      where: { id: 'default' },
      create: {
        id: 'default',
        founderBrainProvidersJson: next,
        updatedByUserId: userId,
      },
      update: {
        founderBrainProvidersJson: next,
        updatedByUserId: userId,
      },
    });

    this.cachedConfig = next;
    return this.getAdminView();
  }

  resolveRouteProviders(route: ModelRoute): ModelRoute {
    if (!this.cachedConfig.twoModelRoutingEnabled) {
      return route;
    }
    return { ...route };
  }

  async resolveApiKey(provider: FounderBrainProviderSlug): Promise<string | null> {
    if (provider === 'glm') {
      const env = process.env.GLM_API_KEY?.trim();
      if (env) return env;
      const promo = await this.founderPromo.getDecryptedPlatformGlmKey();
      if (promo) return promo;
      return this.aiRouting.getDecryptedKey('glm');
    }

    const env = process.env.DEEPSEEK_API_KEY?.trim();
    if (env) return env;
    const brain = await this.founderPromo.getDecryptedPlatformDeepseekKey();
    if (brain) return brain;
    const promo = await this.founderPromo.getDecryptedPlatformPromoDeepseekKey();
    if (promo) return promo;
    return this.aiRouting.getDecryptedKey('deepseek');
  }

  async testProvider(provider: FounderBrainProviderSlug): Promise<FounderBrainProviderTestResult> {
    const started = Date.now();
    const key = await this.resolveApiKey(provider);
    if (!key) {
      return {
        provider,
        ok: false,
        message: 'No API key configured for this provider',
        latencyMs: Date.now() - started,
      };
    }

    try {
      if (provider === 'glm') {
        const res = await fetch(`${getGlmApiBaseUrl()}/models?limit=1`, {
          headers: { Authorization: `Bearer ${key}` },
        });
        if (!res.ok) {
          const body = await res.text().catch(() => '');
          return {
            provider,
            ok: false,
            message: `GLM ping failed (${res.status}): ${body.slice(0, 120)}`,
            latencyMs: Date.now() - started,
          };
        }
      } else {
        const res = await fetch(DEEPSEEK_CHAT_URL, {
          method: 'POST',
          headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({
            model: this.cachedConfig.deepseekFastModel,
            messages: [{ role: 'user', content: 'ping' }],
            max_tokens: 8,
          }),
        });
        if (!res.ok) {
          const body = await res.text().catch(() => '');
          return {
            provider,
            ok: false,
            message: `DeepSeek ping failed (${res.status}): ${body.slice(0, 120)}`,
            latencyMs: Date.now() - started,
          };
        }
      }
      return {
        provider,
        ok: true,
        message: 'Provider reachable',
        latencyMs: Date.now() - started,
      };
    } catch (err) {
      return {
        provider,
        ok: false,
        message: err instanceof Error ? err.message : String(err),
        latencyMs: Date.now() - started,
      };
    }
  }

  async testAllProviders(): Promise<FounderBrainProviderTestResult[]> {
    return Promise.all([
      this.testProvider('deepseek'),
      this.testProvider('glm'),
    ]);
  }

  private async resolveKeyStatus(provider: FounderBrainProviderSlug): Promise<FounderBrainProviderKeyStatus> {
    if (provider === 'glm') {
      const env = process.env.GLM_API_KEY?.trim();
      if (env) return { configured: true, source: 'env', last4: env.slice(-4) };
      const promo = await this.founderPromo.getDecryptedPlatformGlmKey();
      if (promo) return { configured: true, source: 'promo', last4: promo.slice(-4) };
      const routed = await this.aiRouting.getDecryptedKey('glm');
      if (routed) return { configured: true, source: 'routing', last4: routed.slice(-4) };
      return { configured: false, source: null, last4: null };
    }

    const env = process.env.DEEPSEEK_API_KEY?.trim();
    if (env) return { configured: true, source: 'env', last4: env.slice(-4) };
    const brain = await this.founderPromo.getDecryptedPlatformDeepseekKey();
    if (brain) return { configured: true, source: 'platform_brain', last4: brain.slice(-4) };
    const promo = await this.founderPromo.getDecryptedPlatformPromoDeepseekKey();
    if (promo) return { configured: true, source: 'promo', last4: promo.slice(-4) };
    const routed = await this.aiRouting.getDecryptedKey('deepseek');
    if (routed) return { configured: true, source: 'routing', last4: routed.slice(-4) };
    return { configured: false, source: null, last4: null };
  }

  private mergeConfig(raw: unknown): FounderBrainProvidersConfig {
    const base = { ...DEFAULT_FOUNDER_BRAIN_PROVIDERS_CONFIG };
    if (!raw || typeof raw !== 'object') return base;
    const obj = raw as Record<string, unknown>;

    if (typeof obj.twoModelRoutingEnabled === 'boolean') {
      base.twoModelRoutingEnabled = obj.twoModelRoutingEnabled;
    }
    if (typeof obj.deepseekFastModel === 'string' && obj.deepseekFastModel.trim()) {
      base.deepseekFastModel = obj.deepseekFastModel.trim();
    }
    if (typeof obj.deepseekCodingModel === 'string' && obj.deepseekCodingModel.trim()) {
      base.deepseekCodingModel = obj.deepseekCodingModel.trim();
    }
    if (typeof obj.glmFastModel === 'string' && obj.glmFastModel.trim()) {
      base.glmFastModel = obj.glmFastModel.trim();
    }
    if (typeof obj.glmCodingModel === 'string' && obj.glmCodingModel.trim()) {
      base.glmCodingModel = obj.glmCodingModel.trim();
    }
    if (typeof obj.defaultMode === 'string') {
      base.defaultMode = parseFounderBrainMode(obj.defaultMode);
    }
    return base;
  }
}
