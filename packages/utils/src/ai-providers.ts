export type AiProviderConnectMode = 'none' | 'api_key' | 'remote_agent';

export type AiProviderKey =
  | 'RULE_BASED'
  | 'OPENAI'
  | 'ANTHROPIC'
  | 'GEMINI'
  | 'OPENHANDS';

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
  return key === 'OPENHANDS';
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
];

export function aiProviderConfig(key: string): AiProviderConfig | undefined {
  return AI_PROVIDERS.find((p) => p.key === key);
}

export const QUICK_BUILD_AI_SYSTEM = `You are a startup product assistant for Founder OS.
Return ONLY valid JSON (no markdown) with keys:
ideaTitle, spec (markdown string), tasks (string array), githubIssues (string array),
roadmapTitle, cursorPrompt (string for coding agent), traderView (1-2 sentences for investors).`;

export const FOUNDER_BRAIN_SYSTEM = `You are Founder Brain — the public Q&A layer for a crypto startup project.
Answer using ONLY the project context provided. Be concise, founder-friendly, and honest about gaps.
If data is missing, say what the founder has not published yet. Never invent token addresses or raise numbers.`;
