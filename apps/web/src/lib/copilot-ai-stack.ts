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

export type BuildWorkerOption = { key: 'CURSOR' | 'OPENHANDS'; label: string };

export function listBuildWorkers(connections: {
  cursor?: boolean;
  openHands?: boolean;
}): BuildWorkerOption[] {
  const out: BuildWorkerOption[] = [];
  if (connections.cursor) out.push({ key: 'CURSOR', label: 'Cursor' });
  if (connections.openHands) out.push({ key: 'OPENHANDS', label: 'OpenHands' });
  return out;
}

export type CopilotStackSummary = {
  canAsk: boolean;
  canBuild: boolean;
  askLabel: string;
  buildLabel: string;
  chatProviders: { key: string; label: string }[];
  buildWorkers: BuildWorkerOption[];
  buildWorker: string;
  statusLine: string;
};

export function resolveCopilotStack(
  providers: ProviderRow[],
  defaultProvider: string,
  buildWorker: string,
  connections?: { cursor?: boolean; openHands?: boolean },
): CopilotStackSummary {
  const { connected: chatProviders, defaultChat } = listChatProviders(providers, defaultProvider);
  const buildWorkers = listBuildWorkers(connections ?? {});
  const activeBuild =
    buildWorkers.find((w) => w.key === buildWorker) ?? buildWorkers[0] ?? null;
  const canAsk = chatProviders.length > 0;
  const canBuild = buildWorkers.length > 0;
  const askLabel = defaultChat ? shortProviderName(defaultChat) : 'Project memory';
  const buildLabel = activeBuild?.label ?? (buildWorkerDisplayName(buildWorker) || 'Builder');

  const parts: string[] = [];
  if (canAsk) {
    const names = chatProviders.map((p) => shortProviderName(p)).join(', ');
    parts.push(`Answers: ${names}`);
  } else parts.push('Answers: project memory (connect an LLM in AI Stack)');
  if (canBuild) {
    const codeNames = buildWorkers.map((w) => w.label).join(', ');
    parts.push(`Codes: ${codeNames} (live in this chat)`);
  } else parts.push('Codes: connect Cursor or OpenHands in AI Stack');

  return {
    canAsk,
    canBuild,
    askLabel,
    buildLabel,
    chatProviders: chatProviders.map((p) => ({ key: p.key, label: shortProviderName(p) })),
    buildWorkers,
    buildWorker: activeBuild?.key ?? buildWorker,
    statusLine: parts.join(' · '),
  };
}

export function formatMessageProviderLabel(provider?: string, routedAgent?: string): string {
  if (routedAgent) return `${routedAgent} · streamed inline`;
  if (!provider) return 'Copilot';
  if (provider === 'BUILDER') return 'Builder agent · streamed in chat';
  if (provider === 'CURSOR') return 'Cursor · streamed in chat';
  if (provider === 'OPENHANDS') return 'OpenHands · streamed in chat';
  if (provider === 'RULE_BASED') return 'Project memory · streamed inline';
  if (provider === 'FOUNDER_OS') return 'Founder OS · autopilot';
  if (provider.startsWith('WORKER:')) return provider.replace('WORKER:', '') + ' · streamed inline';
  return `${shortProviderName({ key: provider, label: provider })} · streamed inline`;
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
