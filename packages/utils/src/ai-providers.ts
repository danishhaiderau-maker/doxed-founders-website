export type AiProviderConnectMode = 'none' | 'api_key' | 'desk';

export type AiProviderKey =
  | 'RULE_BASED'
  | 'OPENAI'
  | 'ANTHROPIC'
  | 'GEMINI'
  | 'CURSOR'
  | 'CLAUDE_CODE'
  | 'CODEX'
  | 'WINDSURF'
  | 'OPENHANDS'
  | 'OPENCLAW';

export type AiProviderConfig = {
  key: AiProviderKey;
  label: string;
  connectMode: AiProviderConnectMode;
  needsApiKey: boolean;
  defaultModel: string | null;
  billTip: string;
  credentialProvider: string | null;
  copyCommand?: string;
};

export const DESK_WORKFLOW_PROVIDER_KEYS: AiProviderKey[] = [
  'CURSOR',
  'CLAUDE_CODE',
  'CODEX',
  'WINDSURF',
  'OPENHANDS',
  'OPENCLAW',
];

export function isDeskWorkflowProvider(key: string): boolean {
  return DESK_WORKFLOW_PROVIDER_KEYS.includes(key as AiProviderKey);
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
    billTip: 'Remote AI — your OpenAI key, billed to your account.',
    credentialProvider: 'openai',
  },
  {
    key: 'ANTHROPIC',
    label: 'Anthropic API (Claude)',
    connectMode: 'api_key',
    needsApiKey: true,
    defaultModel: 'claude-3-5-haiku-latest',
    billTip: 'Remote AI — your Anthropic key, Founder OS orchestrates.',
    credentialProvider: 'anthropic',
  },
  {
    key: 'GEMINI',
    label: 'Google Gemini API',
    connectMode: 'api_key',
    needsApiKey: true,
    defaultModel: 'gemini-2.0-flash',
    billTip: 'Remote AI — your Gemini API key.',
    credentialProvider: 'gemini',
  },
  {
    key: 'CURSOR',
    label: 'Cursor',
    connectMode: 'desk',
    needsApiKey: false,
    defaultModel: null,
    billTip: 'Desk workflow — copy prompts from Founder Copilot into Cursor Desktop.',
    credentialProvider: 'cursor',
    copyCommand: 'Copy for Cursor',
  },
  {
    key: 'CLAUDE_CODE',
    label: 'Claude Code',
    connectMode: 'desk',
    needsApiKey: false,
    defaultModel: null,
    billTip: 'Desk workflow — paste resume prompt into Claude Code CLI / IDE extension.',
    credentialProvider: 'claude_code',
    copyCommand: 'Copy for Claude Code',
  },
  {
    key: 'CODEX',
    label: 'OpenAI Codex',
    connectMode: 'desk',
    needsApiKey: false,
    defaultModel: null,
    billTip: 'Desk workflow — use Codex CLI or ChatGPT coding agent with copied context.',
    credentialProvider: 'codex',
    copyCommand: 'Copy for Codex',
  },
  {
    key: 'WINDSURF',
    label: 'Windsurf (Codeium)',
    connectMode: 'desk',
    needsApiKey: false,
    defaultModel: null,
    billTip: 'Desk workflow — paste into Windsurf Cascade with project memory attached.',
    credentialProvider: 'windsurf',
    copyCommand: 'Copy for Windsurf',
  },
  {
    key: 'OPENHANDS',
    label: 'OpenHands',
    connectMode: 'desk',
    needsApiKey: false,
    defaultModel: null,
    billTip: 'Desk workflow — feed task spec to OpenHands agent from Founder Copilot export.',
    credentialProvider: 'openhands',
    copyCommand: 'Copy for OpenHands',
  },
  {
    key: 'OPENCLAW',
    label: 'OpenClaw',
    connectMode: 'desk',
    needsApiKey: false,
    defaultModel: null,
    billTip: 'Desk workflow — dispatch via OpenClaw with Founder OS cursor copy payload.',
    credentialProvider: 'openclaw',
    copyCommand: 'Copy for OpenClaw',
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
