export const AI_STACK_HREF = '/settings/builder';

export type ProviderRow = {
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
    RULE_BASED: 'Project memory',
  };
  return names[provider.key] ?? provider.label.split(/[(\[]/)[0].trim();
}

/** Who answers chat in Founder OS (LLM or local memory). */
export function listChatProviders(providers: ProviderRow[], defaultProvider: string) {
  const connected = providers.filter(
    (p) =>
      p.connected &&
      p.key !== 'RULE_BASED' &&
      (p.connectMode === 'api_key' || p.connectMode === 'founder_node'),
  );
  const defaultChat =
    connected.find((p) => p.key === defaultProvider) ??
    connected.find((p) => p.connectMode === 'api_key') ??
    connected[0] ??
    null;
  return { connected, defaultChat };
}

export function buildWorkerDisplayName(worker: string): string {
  switch (worker) {
    case 'CURSOR':
      return 'Cursor';
    case 'OPENHANDS':
      return 'OpenHands';
    case 'FOUNDER_NODE':
      return 'Founder Node';
    default:
      return '';
  }
}

export type CopilotStackSummary = {
  canAsk: boolean;
  canBuild: boolean;
  askLabel: string;
  buildLabel: string;
  chatProviders: { key: string; label: string }[];
  buildWorker: string;
  statusLine: string;
};

export function resolveCopilotStack(
  providers: ProviderRow[],
  defaultProvider: string,
  buildWorker: string,
): CopilotStackSummary {
  const { connected: chatProviders, defaultChat } = listChatProviders(providers, defaultProvider);
  const canAsk = chatProviders.length > 0;
  const canBuild = buildWorker === 'CURSOR' || buildWorker === 'OPENHANDS';
  const askLabel = defaultChat ? shortProviderName(defaultChat) : 'Project memory';
  const buildLabel = buildWorkerDisplayName(buildWorker) || 'Builder';

  const parts: string[] = [];
  if (canAsk) parts.push(`Answers: ${askLabel}`);
  else parts.push('Answers: project memory (connect an LLM in AI Stack)');
  if (canBuild) parts.push(`Codes: ${buildLabel} (cloud agent on your repo)`);
  else parts.push('Codes: connect Cursor or OpenHands in AI Stack');

  return {
    canAsk,
    canBuild,
    askLabel,
    buildLabel,
    chatProviders: chatProviders.map((p) => ({ key: p.key, label: shortProviderName(p) })),
    buildWorker,
    statusLine: parts.join(' · '),
  };
}

export function formatMessageProviderLabel(provider?: string, routedAgent?: string): string {
  if (routedAgent) return `${routedAgent} · workforce`;
  if (!provider) return 'Copilot';
  if (provider === 'BUILDER') return 'Builder agent · codes in repo';
  if (provider === 'CURSOR') return 'Cursor · codes in repo';
  if (provider === 'RULE_BASED') return 'Project memory';
  if (provider === 'FOUNDER_OS') return 'Founder OS · autopilot';
  if (provider.startsWith('WORKER:')) return provider.replace('WORKER:', '') + ' · tasks';
  return `${shortProviderName({ key: provider, label: provider })} · answers here`;
}

export type CopilotSendMode = 'ask' | 'build';

export function defaultSendMode(stack: CopilotStackSummary): CopilotSendMode {
  if (stack.canBuild && !stack.canAsk) return 'build';
  return 'ask';
}

export function primaryButtonLabel(mode: CopilotSendMode, stack: CopilotStackSummary): string {
  if (mode === 'build' && stack.canBuild) {
    return `Run in ${stack.buildLabel}`;
  }
  if (stack.canAsk) {
    return `Ask ${stack.askLabel}`;
  }
  return 'Send';
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
