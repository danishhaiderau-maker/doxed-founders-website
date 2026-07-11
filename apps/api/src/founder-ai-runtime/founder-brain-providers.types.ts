import type { FounderBrainMode } from '@dcf/utils';
import type { ModelRoute } from './founder-ai-runtime.types';

export type FounderBrainProviderSlug = 'deepseek' | 'glm';

export const FOUNDER_BRAIN_PROVIDER_ALLOWLIST: FounderBrainProviderSlug[] = ['deepseek', 'glm'];

export type FounderBrainProvidersConfig = {
  twoModelRoutingEnabled: boolean;
  deepseekFastModel: string;
  deepseekCodingModel: string;
  glmFastModel: string;
  glmCodingModel: string;
  defaultMode: FounderBrainMode;
};

export type FounderBrainProviderKeyStatus = {
  configured: boolean;
  source: 'env' | 'promo' | 'routing' | 'platform_brain' | null;
  last4: string | null;
};

export type FounderBrainProvidersAdminView = FounderBrainProvidersConfig & {
  keys: Record<FounderBrainProviderSlug, FounderBrainProviderKeyStatus>;
  glmApiBase: string;
  updatedAt: string | null;
};

export type FounderBrainProviderTestResult = {
  provider: FounderBrainProviderSlug;
  ok: boolean;
  message: string;
  latencyMs: number;
};

function envBool(name: string): boolean {
  return process.env[name]?.trim().toLowerCase() === 'true';
}

export const DEFAULT_FOUNDER_BRAIN_PROVIDERS_CONFIG: FounderBrainProvidersConfig = {
  twoModelRoutingEnabled: envBool('FOUNDER_BRAIN_TWO_MODEL_ROUTING'),
  deepseekFastModel: process.env.DEEPSEEK_FAST_MODEL?.trim() || 'deepseek-chat',
  deepseekCodingModel: process.env.DEEPSEEK_CODING_MODEL?.trim() || 'deepseek-reasoner',
  glmFastModel: process.env.GLM_FAST_MODEL?.trim() || 'glm-4-flash',
  glmCodingModel: process.env.GLM_CODING_MODEL?.trim() || 'glm-5.2',
  defaultMode: 'automatic',
};

export type ResolvedModelRoute = ModelRoute;
