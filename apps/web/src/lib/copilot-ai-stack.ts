export const AI_STACK_HREF = '/settings/builder';

type ProviderRow = {
  key: string;
  label: string;
  connected: boolean;
  connectMode?: string;
};

export function shortProviderName(provider: { key: string; label: string }): string {
  const names: Record<string, string> = {
    CURSOR: 'Cursor',
    DEEPSEEK: 'DeepSeek',
    OPENAI: 'OpenAI',
    ANTHROPIC: 'Claude',
    GEMINI: 'Gemini',
    PHALA: 'Phala TEE',
    OPENROUTER: 'OpenRouter',
    OPENHANDS: 'OpenHands',
    OLLAMA_LOCAL: 'Ollama',
  };
  return names[provider.key] ?? provider.label.split(/[(\[]/)[0].trim();
}

export type AiStackAction =
  | { kind: 'connect' }
  | { kind: 'cursor'; label: string }
  | { kind: 'connected'; label: string };

export function resolveAiStackAction(
  providers: ProviderRow[],
  defaultProvider: string,
): AiStackAction {
  const cursor = providers.find((p) => p.key === 'CURSOR' && p.connected);
  if (cursor) {
    return { kind: 'cursor', label: shortProviderName(cursor) };
  }

  const preferred =
    providers.find((p) => p.key === defaultProvider && p.connected && p.key !== 'RULE_BASED') ??
    providers.find(
      (p) => p.connected && p.connectMode === 'api_key' && p.key !== 'RULE_BASED',
    ) ??
    providers.find((p) => p.connected && p.key !== 'RULE_BASED');

  if (preferred) {
    return { kind: 'connected', label: shortProviderName(preferred) };
  }

  return { kind: 'connect' };
}
