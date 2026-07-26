import type { AiRuntimeSection } from './founder-ai-runtime.types';

export const PROVIDER_EGRESS_CALL_SITE_IDS = [
  'runtime.copilot',
  'runtime.quick_build',
  'runtime.founder_draft',
  'runtime.share_paraphrase',
  'runtime.wall_summarizer',
  'runtime.platform_brain',
  'ai_routing.copilot',
  'ai_routing.quick_build',
  'ai_routing.founder_draft',
  'ai_routing.share_paraphrase',
  'ai_routing.wall_summarizer',
  'ai_routing.platform_brain',
  'ai_routing.other',
  'ai_proxy.chat',
  'ai_proxy.intent_classifier',
  'ai_proxy.speech',
  'ai_proxy.visual',
  'builder.legacy_completion',
  'builder.legacy_stream',
  'builder.key_verification',
  'builder.local_ollama',
  'founder_brain.provider_verification',
] as const;

export type ProviderEgressCallSiteId =
  (typeof PROVIDER_EGRESS_CALL_SITE_IDS)[number];

export type ProviderEgressBoundary =
  | 'founder_ai_runtime'
  | 'ai_proxy_runtime'
  | 'managed_auxiliary'
  | 'approved_exception'
  | 'unscoped';

export type ProviderEgressBudgetDomain =
  | 'founder_managed'
  | 'founder_byok'
  | 'founder_managed_chat'
  | 'founder_managed_routing'
  | 'founder_managed_speech'
  | 'founder_managed_vision'
  | 'provider_verification'
  | 'local_inference'
  | 'unattributed_legacy';

export type ProviderEgressContext = {
  boundary: Exclude<ProviderEgressBoundary, 'unscoped'>;
  callSiteId: ProviderEgressCallSiteId;
  budgetDomain: ProviderEgressBudgetDomain;
  runtimeExecutionId: string;
};

export type ProviderEgressEvent = {
  boundary: ProviderEgressBoundary;
  callSiteId: ProviderEgressCallSiteId;
  budgetDomain: ProviderEgressBudgetDomain;
  runtimeExecutionId: string;
  adapterName: string;
  provider: string;
  timestamp: string;
};

export type ProviderEgressSnapshot = {
  total: number;
  governed: number;
  founderRuntime: number;
  ideProxyRuntime: number;
  managedAuxiliary: number;
  approvedExceptions: number;
  bypassed: number;
  governedCoverageRatio: number | null;
  founderRuntimeCoverageRatio: number | null;
  byCallSite: Record<string, number>;
  recent: ProviderEgressEvent[];
};

const RUNTIME_CALL_SITE_BY_SECTION: Record<
  AiRuntimeSection,
  ProviderEgressCallSiteId
> = {
  copilot: 'runtime.copilot',
  quick_build: 'runtime.quick_build',
  founder_draft: 'runtime.founder_draft',
  share_paraphrase: 'runtime.share_paraphrase',
  wall_summarizer: 'runtime.wall_summarizer',
  platform_brain: 'runtime.platform_brain',
};

const ROUTED_CALL_SITE_BY_SECTION: Record<
  string,
  ProviderEgressCallSiteId
> = {
  copilot: 'ai_routing.copilot',
  quick_build: 'ai_routing.quick_build',
  founder_draft: 'ai_routing.founder_draft',
  share_paraphrase: 'ai_routing.share_paraphrase',
  wall_summarizer: 'ai_routing.wall_summarizer',
  platform_brain: 'ai_routing.platform_brain',
};

export function runtimeCallSiteForSection(
  section: AiRuntimeSection,
): ProviderEgressCallSiteId {
  return RUNTIME_CALL_SITE_BY_SECTION[section];
}

export function routedCallSiteForSection(
  section: string,
): ProviderEgressCallSiteId {
  return ROUTED_CALL_SITE_BY_SECTION[section] ?? 'ai_routing.other';
}

export function budgetDomainForBillingSource(
  billingSource: string | undefined,
): ProviderEgressBudgetDomain {
  return billingSource === 'byok' ? 'founder_byok' : 'founder_managed';
}
