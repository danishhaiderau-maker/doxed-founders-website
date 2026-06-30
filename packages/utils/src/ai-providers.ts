export type AiProviderConnectMode = 'none' | 'api_key' | 'remote_agent' | 'founder_node';

export type AiProviderKey =
  | 'RULE_BASED'
  | 'OPENAI'
  | 'ANTHROPIC'
  | 'GEMINI'
  | 'DEEPSEEK'
  | 'GLM'
  | 'OPENROUTER'
  | 'JATEVO'
  | 'SURPLUS'
  | 'OLLAMA_LOCAL'
  | 'PHALA'
  | 'OPENHANDS'
  | 'CURSOR';

export type AiProviderCategory = 'marketplace' | 'direct' | 'local' | 'private';

export type AiProviderGuide = {
  category: AiProviderCategory;
  categoryLabel: string;
  keyUrl: string;
  keyUrlLabel: string;
  keyPlaceholder: string;
  modelPlaceholder: string;
  steps: string[];
};

export type AiProviderConfig = {
  key: AiProviderKey;
  label: string;
  connectMode: AiProviderConnectMode;
  needsApiKey: boolean;
  needsBaseUrl?: boolean;
  defaultModel: string | null;
  billTip: string;
  credentialProvider: string | null;
};

export function isRemoteAgentProvider(key: string): boolean {
  return key === 'OPENHANDS' || key === 'CURSOR';
}

export function isFounderNodeAiProvider(key: string): boolean {
  return key === 'OLLAMA_LOCAL';
}

export const AI_PROVIDERS: AiProviderConfig[] = [
  {
    key: 'RULE_BASED',
    label: 'Rule-based (free)',
    connectMode: 'none',
    needsApiKey: false,
    defaultModel: null,
    billTip: 'Founder OS generates specs locally — zero AI API cost.',
    credentialProvider: null,
  },
  {
    key: 'GLM',
    label: 'GLM 5.2 (ZhipuAI)',
    connectMode: 'api_key',
    needsApiKey: true,
    defaultModel: 'glm-5.2',
    billTip:
      'Cheapest coding LLM — OpenAI-compatible. Free for new founders via promo. Best $/token for Founder Brain.',
    credentialProvider: 'glm',
  },
  {
    key: 'OPENAI',
    label: 'OpenAI API',
    connectMode: 'api_key',
    needsApiKey: true,
    defaultModel: 'gpt-4o-mini',
    billTip: 'Remote LLM — your OpenAI key for Quick Build specs and Founder Brain.',
    credentialProvider: 'openai',
  },
  {
    key: 'ANTHROPIC',
    label: 'Anthropic API (Claude)',
    connectMode: 'api_key',
    needsApiKey: true,
    defaultModel: 'claude-3-5-haiku-latest',
    billTip: 'Remote LLM — your Anthropic key, Founder OS orchestrates.',
    credentialProvider: 'anthropic',
  },
  {
    key: 'GEMINI',
    label: 'Google Gemini API',
    connectMode: 'api_key',
    needsApiKey: true,
    defaultModel: 'gemini-2.0-flash',
    billTip: 'Remote LLM — your Gemini API key.',
    credentialProvider: 'gemini',
  },
  {
    key: 'DEEPSEEK',
    label: 'DeepSeek API',
    connectMode: 'api_key',
    needsApiKey: true,
    defaultModel: 'deepseek-chat',
    billTip: 'Remote LLM — OpenAI-compatible DeepSeek key for specs and Founder Brain.',
    credentialProvider: 'deepseek',
  },
  {
    key: 'OPENROUTER',
    label: 'OpenRouter (BYO models)',
    connectMode: 'api_key',
    needsApiKey: true,
    defaultModel: 'openrouter/auto',
    billTip:
      'One API key — route Copilot and Quick Build to Claude, GPT, Llama, DeepSeek, and more. Billed on your OpenRouter account.',
    credentialProvider: 'openrouter',
  },
  {
    key: 'JATEVO',
    label: 'Jatevo ($JTVO gateway)',
    connectMode: 'api_key',
    needsApiKey: true,
    defaultModel: 'auto',
    billTip:
      'OpenAI-compatible gateway — GPT, Qwen, Kimi, GLM, and more. Daily quota scales with $JTVO holdings on your Jatevo account.',
    credentialProvider: 'jatevo',
  },
  {
    key: 'SURPLUS',
    label: 'Surplus Intelligence (marketplace)',
    connectMode: 'api_key',
    needsApiKey: true,
    defaultModel: 'claude-opus-4.8',
    billTip:
      'One inf_ key — routes Copilot to the cheapest healthy seller on the Surplus inference marketplace. Billed to your Surplus balance.',
    credentialProvider: 'surplus',
  },
  {
    key: 'OLLAMA_LOCAL',
    label: 'Ollama (local via Founder Node)',
    connectMode: 'founder_node',
    needsApiKey: false,
    defaultModel: 'llama3.2',
    billTip:
      'Runs on your desktop via Founder Node + Ollama. Prompts stay on your machine — zero cloud inference cost.',
    credentialProvider: 'ollama',
  },
  {
    key: 'PHALA',
    label: 'Private AI (Phala TEE)',
    connectMode: 'api_key',
    needsApiKey: true,
    needsBaseUrl: true,
    defaultModel: 'phala/deepseek-chat-v3-0324',
    billTip:
      'Confidential inference in a hardware TEE — OpenAI-compatible API. Your key or platform credits; prompts are not used for public model training.',
    credentialProvider: 'phala',
  },
  {
    key: 'OPENHANDS',
    label: 'OpenHands (remote agent)',
    connectMode: 'remote_agent',
    needsApiKey: true,
    needsBaseUrl: true,
    defaultModel: null,
    billTip:
      'Self-hosted or OpenHands Cloud — Founder OS dispatches build tasks via REST (URL + API key). LLM cost is on your OpenHands instance.',
    credentialProvider: 'openhands',
  },
  {
    key: 'CURSOR',
    label: 'Cursor Cloud Agents',
    connectMode: 'remote_agent',
    needsApiKey: true,
    defaultModel: null,
    billTip:
      'Cursor Cloud Agents API — Founder OS creates/resumes cloud agents on your GitHub repo. Billing is on your Cursor account (api.cursor.com).',
    credentialProvider: 'cursor',
  },
];

export function aiProviderConfig(key: string): AiProviderConfig | undefined {
  return AI_PROVIDERS.find((p) => p.key === key);
}

/** Step-by-step connect instructions shown in Settings → Builder Step 3. */
export const AI_PROVIDER_GUIDES: Partial<Record<AiProviderKey, AiProviderGuide>> = {
  SURPLUS: {
    category: 'marketplace',
    categoryLabel: 'Marketplace gateways',
    keyUrl: 'https://www.surplusintelligence.ai/buy',
    keyUrlLabel: 'surplusintelligence.ai/buy',
    keyPlaceholder: 'inf_…',
    modelPlaceholder: 'claude-opus-4.8, gpt-4o, …',
    steps: [
      'Sign in at surplusintelligence.ai/buy and fund your balance.',
      'Create an API key (starts with inf_).',
      'Paste below → Connect & activate → pick as Default brain.',
    ],
  },
  OPENROUTER: {
    category: 'marketplace',
    categoryLabel: 'Marketplace gateways',
    keyUrl: 'https://openrouter.ai/keys',
    keyUrlLabel: 'openrouter.ai/keys',
    keyPlaceholder: 'sk-or-…',
    modelPlaceholder: 'openrouter/auto, anthropic/claude-3.5-haiku, …',
    steps: [
      'Create a key at openrouter.ai/keys.',
      'Paste below → Connect & activate.',
      'Optional: set Preferred model to a specific route.',
    ],
  },
  JATEVO: {
    category: 'marketplace',
    categoryLabel: 'Marketplace gateways',
    keyUrl: 'https://jatevo.ai',
    keyUrlLabel: 'jatevo.ai',
    keyPlaceholder: 'sk-clb-…',
    modelPlaceholder: 'auto',
    steps: [
      'Get your sk-clb-… key from jatevo.ai.',
      'Paste below → Connect & activate.',
      'Quota scales with $JTVO holdings on your account.',
    ],
  },
  OPENAI: {
    category: 'direct',
    categoryLabel: 'Direct vendor APIs',
    keyUrl: 'https://platform.openai.com/api-keys',
    keyUrlLabel: 'platform.openai.com',
    keyPlaceholder: 'sk-…',
    modelPlaceholder: 'gpt-4o-mini, gpt-4o, …',
    steps: [
      'Create an API key at platform.openai.com.',
      'Paste below → Connect & activate → select OpenAI as Default brain.',
    ],
  },
  ANTHROPIC: {
    category: 'direct',
    categoryLabel: 'Direct vendor APIs',
    keyUrl: 'https://console.anthropic.com/settings/keys',
    keyUrlLabel: 'console.anthropic.com',
    keyPlaceholder: 'sk-ant-…',
    modelPlaceholder: 'claude-3-5-haiku-latest, claude-sonnet-4-…',
    steps: [
      'Create a key at console.anthropic.com.',
      'Paste below → Connect & activate → select Anthropic as Default brain.',
    ],
  },
  GEMINI: {
    category: 'direct',
    categoryLabel: 'Direct vendor APIs',
    keyUrl: 'https://aistudio.google.com/apikey',
    keyUrlLabel: 'aistudio.google.com',
    keyPlaceholder: 'AIza…',
    modelPlaceholder: 'gemini-2.0-flash, gemini-1.5-pro, …',
    steps: [
      'Create an API key in Google AI Studio.',
      'Paste below → Connect & activate → select Gemini as Default brain.',
    ],
  },
  DEEPSEEK: {
    category: 'direct',
    categoryLabel: 'Direct vendor APIs',
    keyUrl: 'https://platform.deepseek.com/api_keys',
    keyUrlLabel: 'platform.deepseek.com',
    keyPlaceholder: 'sk-…',
    modelPlaceholder: 'deepseek-chat, deepseek-reasoner',
    steps: [
      'Create a key at platform.deepseek.com.',
      'Paste below → Connect & activate.',
    ],
  },
};

export function listBrainProvidersByCategory(): {
  category: AiProviderCategory;
  label: string;
  keys: AiProviderKey[];
}[] {
  return [
    {
      category: 'marketplace',
      label: 'Marketplace gateways',
      keys: ['SURPLUS', 'OPENROUTER', 'JATEVO'],
    },
    {
      category: 'direct',
      label: 'Direct vendor APIs',
      keys: ['OPENAI', 'ANTHROPIC', 'GEMINI', 'DEEPSEEK', 'GLM'],
    },
    {
      category: 'local',
      label: 'Local & private',
      keys: ['OLLAMA_LOCAL', 'PHALA'],
    },
  ];
}

export const QUICK_BUILD_AI_SYSTEM = `You are a startup product assistant for Founder OS.
Return ONLY valid JSON (no markdown) with keys:
ideaTitle, spec (markdown string), tasks (string array), githubIssues (string array),
roadmapTitle, cursorPrompt (string for coding agent), traderView (1-2 sentences for investors).`;

export const FOUNDER_BRAIN_SYSTEM = `You are Founder Brain — the public Q&A layer for a crypto startup project.
Answer using ONLY the project context provided. Be concise, founder-friendly, and honest about gaps.
If data is missing, say what the founder has not published yet. Never invent token addresses or raise numbers.`;
