export type AiProviderKey = 'RULE_BASED' | 'OPENAI' | 'ANTHROPIC' | 'GEMINI' | 'CURSOR';

export type AiProviderConfig = {
  key: AiProviderKey;
  label: string;
  needsApiKey: boolean;
  defaultModel: string | null;
  billTip: string;
  credentialProvider: string | null;
};

export const AI_PROVIDERS: AiProviderConfig[] = [
  {
    key: 'RULE_BASED',
    label: 'Rule-based (free)',
    needsApiKey: false,
    defaultModel: null,
    billTip: 'Founder OS generates specs locally — zero AI API cost.',
    credentialProvider: null,
  },
  {
    key: 'OPENAI',
    label: 'OpenAI',
    needsApiKey: true,
    defaultModel: 'gpt-4o-mini',
    billTip: 'Your OpenAI key — usage billed to your account, not Founder OS.',
    credentialProvider: 'openai',
  },
  {
    key: 'ANTHROPIC',
    label: 'Anthropic (Claude)',
    needsApiKey: true,
    defaultModel: 'claude-3-5-haiku-latest',
    billTip: 'Your Anthropic key — Founder OS orchestrates, you pay the provider.',
    credentialProvider: 'anthropic',
  },
  {
    key: 'GEMINI',
    label: 'Google Gemini',
    needsApiKey: true,
    defaultModel: 'gemini-2.0-flash',
    billTip: 'Your Gemini API key — scalable middleware architecture.',
    credentialProvider: 'gemini',
  },
  {
    key: 'CURSOR',
    label: 'Cursor (desk workflow)',
    needsApiKey: false,
    defaultModel: null,
    billTip: 'No remote API — copy prompts into Cursor Desktop at your desk.',
    credentialProvider: 'cursor',
  },
];

export function aiProviderConfig(key: string): AiProviderConfig | undefined {
  return AI_PROVIDERS.find((p) => p.key === key);
}

export const QUICK_BUILD_AI_SYSTEM = `You are a startup product assistant for Founder OS.
Return ONLY valid JSON (no markdown) with keys:
ideaTitle, spec (markdown string), tasks (string array), githubIssues (string array),
roadmapTitle, cursorPrompt (string for coding agent), traderView (1-2 sentences for investors).`;
