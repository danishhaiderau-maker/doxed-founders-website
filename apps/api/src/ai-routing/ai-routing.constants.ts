/**
 * Known AI sections on the platform. Each is a stable slug stored in
 * `AiSectionRouting.section`. New sections can be added here and they will
 * be auto-seeded by `AiRoutingService.seedDefaults()`.
 */
export type AiSectionSlug =
  | 'share_paraphrase'
  | 'wall_summarizer'
  | 'copilot'
  | 'quick_build'
  | 'founder_draft'
  | 'platform_brain';

export const AI_SECTION_SLUGS: AiSectionSlug[] = [
  'share_paraphrase',
  'wall_summarizer',
  'copilot',
  'quick_build',
  'founder_draft',
  'platform_brain',
];

export const AI_SECTION_LABELS: Record<AiSectionSlug, string> = {
  share_paraphrase: 'X Share — Paraphrase',
  wall_summarizer: 'Project Wall — Chat Summarizer',
  copilot: 'Founder Copilot chat',
  quick_build: 'Quick Build (idea → spec)',
  founder_draft: 'Founder draft / update generator',
  platform_brain: 'Platform Brain fallback',
};

/** Default provider key per section — matches the pre-routing behaviour. */
export const AI_SECTION_DEFAULT_PROVIDER: Record<AiSectionSlug, string> = {
  share_paraphrase: 'deepseek',
  wall_summarizer: 'glm',
  copilot: 'deepseek',
  quick_build: 'deepseek',
  founder_draft: 'deepseek',
  platform_brain: 'deepseek',
};

/** Seed list for the provider registry. enabled=false, no key — admin feeds key + toggles on. */
export type ProviderSeed = {
  key: string;
  label: string;
  baseUrl: string;
  defaultModel: string;
  adapter: 'openai_compat' | 'anthropic' | 'gemini_native';
};

export const PROVIDER_SEEDS: ProviderSeed[] = [
  {
    key: 'deepseek',
    label: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com/v1',
    defaultModel: 'deepseek-chat',
    adapter: 'openai_compat',
  },
  {
    key: 'glm',
    label: 'GLM 5.2 (z.ai Coding Plan)',
    baseUrl: 'https://api.z.ai/api/coding/paas/v4',
    defaultModel: 'glm-5.2',
    adapter: 'openai_compat',
  },
  {
    key: 'gemini',
    label: 'Google Gemini (OpenAI-compat)',
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai',
    defaultModel: 'gemini-1.5-flash',
    adapter: 'openai_compat',
  },
  {
    key: 'openai',
    label: 'OpenAI',
    baseUrl: 'https://api.openai.com/v1',
    defaultModel: 'gpt-4o-mini',
    adapter: 'openai_compat',
  },
  {
    key: 'claude',
    label: 'Anthropic Claude',
    baseUrl: 'https://api.anthropic.com/v1',
    defaultModel: 'claude-3-5-sonnet-20241022',
    adapter: 'anthropic',
  },
  {
    key: 'xiaomi',
    label: 'Xiaomi (MiMo)',
    baseUrl: 'https://api.x.ai/v1',
    defaultModel: 'mimo-7b',
    adapter: 'openai_compat',
  },
  {
    key: 'ollama',
    label: 'Ollama (local)',
    baseUrl: 'http://localhost:11434/v1',
    defaultModel: 'llama3.1',
    adapter: 'openai_compat',
  },
];
