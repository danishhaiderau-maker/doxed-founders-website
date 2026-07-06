import type { FounderBrainMode } from '@dcf/utils';
import type { ModelRoute } from './founder-ai-runtime.types';

export type FounderBrainProviderSlug = 'deepseek' | 'glm';

export const FOUNDER_BRAIN_PROVIDER_ALLOWLIST: FounderBrainProviderSlug[] = ['deepseek', 'glm'];

export type FounderBrainProvidersConfig = {
  twoModelRoutingEnabled: boolean;
  fastProvider: FounderBrainProviderSlug;
  codingProvider: FounderBrainProviderSlug;
  defaultMode: FounderBrainMode;
  fastModel: string;
  codingModel: string;
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

function envProvider(name: string, fallback: FounderBrainProviderSlug): FounderBrainProviderSlug {
  const raw = process.env[name]?.trim().toLowerCase();
  return raw === 'glm' || raw === 'deepseek' ? raw : fallback;
}

export const DEFAULT_FOUNDER_BRAIN_PROVIDERS_CONFIG: FounderBrainProvidersConfig = {
  twoModelRoutingEnabled: envBool('FOUNDER_BRAIN_TWO_MODEL_ROUTING'),
  fastProvider: envProvider('FOUNDER_BRAIN_FAST_PROVIDER', 'deepseek'),
  codingProvider: envProvider('FOUNDER_BRAIN_CODING_PROVIDER', 'glm'),
  defaultMode: 'automatic',
  fastModel: process.env.AI_RUNTIME_FAST_MODEL?.trim() || 'deepseek-chat',
  codingModel: process.env.AI_RUNTIME_CODE_MODEL?.trim() || 'glm-5.2',
};

export type ResolvedModelRoute = ModelRoute;
