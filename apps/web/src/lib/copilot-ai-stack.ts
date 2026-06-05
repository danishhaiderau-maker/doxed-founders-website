import {
  classifyFounderBrainTask,
  isFounderRepoStatusPrompt,
  shouldDispatchBuilderForCodeAsk,
} from '@dcf/utils';

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
    JATEVO: 'Jatevo ($JTVO)',
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
  const { connected: chatProviders } = listChatProviders(providers, defaultProvider);
  const buildWorkers = listBuildWorkers(connections ?? {});
  const activeBuild =
    buildWorkers.find((w) => w.key === buildWorker) ?? buildWorkers[0] ?? null;
  const canAsk = chatProviders.length > 0;
  const canBuild = buildWorkers.length > 0;
  const askLabel = canAsk ? 'Founder Brain' : 'Project memory';
  const buildLabel = canBuild ? 'Builder Agent' : buildWorkerDisplayName(buildWorker) || 'Builder';

  const parts: string[] = [];
  if (canAsk) parts.push('Ask → Founder Brain');
  else parts.push('Ask → connect LLM in Settings');
  if (canBuild) parts.push('Build → Builder Agent (live in chat)');
  else parts.push('Build → connect Cursor in Settings');

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
  if (routedAgent) return `${routedAgent} · Founder OS`;
  if (!provider) return 'Founder Brain';
  if (provider === 'FOUNDER_BRAIN') return routedAgent ?? 'Founder Brain';
  if (provider === 'BUILDER') return 'Builder Agent · in chat';
  if (provider === 'CURSOR') return 'Builder Agent · in chat';
  if (provider === 'OPENHANDS') return 'Builder Agent · in chat';
  if (provider === 'RULE_BASED') return 'Project memory · streamed inline';
  if (provider === 'FOUNDER_OS') return 'Founder OS · autopilot';
  if (provider.startsWith('WORKER:')) return provider.replace('WORKER:', '') + ' · streamed inline';
  return `${shortProviderName({ key: provider, label: provider })} · streamed inline`;
}

export type CopilotSendMode = 'ask' | 'build';

export type CopilotAction = {
  id: string;
  kind: CopilotSendMode;
  providerKey?: string;
  workerKey?: 'CURSOR' | 'OPENHANDS';
  label: string;
};

/** Connected chat LLMs + code agents as explicit actions (no generic Ask/Build). */
export function listCopilotActions(
  providers: ProviderRow[],
  defaultProvider: string,
  connections?: { cursor?: boolean; openHands?: boolean },
): CopilotAction[] {
  const { connected } = listChatProviders(providers, defaultProvider);
  const asks: CopilotAction[] = connected.map((p) => ({
    id: `ask:${p.key}`,
    kind: 'ask',
    providerKey: p.key,
    label: `Ask ${shortProviderName(p)}`,
  }));
  const builds: CopilotAction[] = listBuildWorkers(connections ?? {}).map((w) => ({
    id: `build:${w.key}`,
    kind: 'build',
    workerKey: w.key,
    label: `Build with ${w.label}`,
  }));
  if (asks.length === 0) {
    asks.push({
      id: 'ask:RULE_BASED',
      kind: 'ask',
      providerKey: 'RULE_BASED',
      label: 'Ask (project memory)',
    });
  }
  return [...asks, ...builds];
}

export function defaultCopilotAction(
  actions: CopilotAction[],
  defaultProvider: string,
): CopilotAction | null {
  if (!actions.length) return null;
  return (
    actions.find((a) => a.kind === 'ask' && a.providerKey === defaultProvider) ??
    actions.find((a) => a.kind === 'ask') ??
    actions[0]
  );
}

export function copilotActionSendMode(action: CopilotAction | null): CopilotSendMode {
  return action?.kind === 'build' ? 'build' : 'ask';
}

export type CopilotUsageLine = { title: string; detail?: string };

/** When to use each connected provider — varies per founder account. */
export function buildCopilotUsageLines(
  actions: CopilotAction[],
  defaultProvider: string,
): CopilotUsageLine[] {
  const lines: CopilotUsageLine[] = [
    {
      title: 'Resume',
      detail: 'Sync GitHub + vault briefing. No code changes.',
    },
    {
      title: "What's the status?",
      detail: 'GitHub-grounded answer — pick any Ask model below.',
    },
  ];

  const askKeys = new Set(actions.filter((a) => a.kind === 'ask').map((a) => a.providerKey));
  const defaultAsk = actions.find((a) => a.kind === 'ask' && a.providerKey === defaultProvider);
  if (defaultAsk) {
    lines.push({
      title: defaultAsk.label,
      detail: 'Your default for planning, status, and strategy.',
    });
  }
  if (askKeys.has('OLLAMA_LOCAL')) {
    lines.push({
      title: 'Ask Ollama',
      detail: 'Private on your machine — drafts and experiments without cloud API cost.',
    });
  }
  if (askKeys.has('DEEPSEEK')) {
    lines.push({
      title: 'Ask DeepSeek',
      detail: 'Strong reasoning for architecture, tradeoffs, and ship plans.',
    });
  }
  if (askKeys.has('OPENROUTER') || askKeys.has('OPENAI') || askKeys.has('ANTHROPIC')) {
    lines.push({
      title: 'Cloud LLM',
      detail: 'General chat when Ollama is off or you want a hosted model.',
    });
  }
  const build = actions.find((a) => a.kind === 'build');
  if (build) {
    lines.push({
      title: build.label,
      detail: 'Implements in your GitHub repo — PRs and deploys (several minutes).',
    });
  }
  if (!actions.some((a) => a.kind === 'build')) {
    lines.push({
      title: 'Build with Cursor',
      detail: 'Connect Cursor in Settings → AI stack to edit the repo from chat.',
    });
  }
  if (!actions.some((a) => a.kind === 'ask')) {
    lines.push({
      title: 'Ask (LLM)',
      detail: 'Connect DeepSeek, Ollama, or OpenRouter in Settings for smarter answers.',
    });
  }

  return lines;
}

export function defaultSendMode(stack: CopilotStackSummary): CopilotSendMode {
  if (stack.canBuild && !stack.canAsk) return 'build';
  return 'ask';
}

/** Status / repo questions belong on Ask (Founder Brain), not Builder Agent. */
export function resolveCopilotSendMode(
  prompt: string,
  stack: CopilotStackSummary,
  requested?: CopilotSendMode,
): CopilotSendMode {
  if (isFounderRepoStatusPrompt(prompt)) return stack.canAsk ? 'ask' : defaultSendMode(stack);
  return requested ?? defaultSendMode(stack);
}

/** Mission Control hero chat — auto-route build vs research without mode picker. */
export function resolveHeroBrainSendMode(
  prompt: string,
  stack: CopilotStackSummary,
): CopilotSendMode {
  if (isFounderRepoStatusPrompt(prompt)) return stack.canAsk ? 'ask' : defaultSendMode(stack);
  if (/take full control|sync everything|push all updates/i.test(prompt)) return 'ask';
  const task = classifyFounderBrainTask(prompt);
  if (shouldDispatchBuilderForCodeAsk(prompt, task) && stack.canBuild) return 'build';
  if (
    task === 'code' &&
    stack.canBuild &&
    /\b(fix|implement|build|ship|refactor|create pr|patch|add )\b/i.test(prompt)
  ) {
    return 'build';
  }
  return stack.canAsk ? 'ask' : defaultSendMode(stack);
}

export function primaryButtonLabel(mode: CopilotSendMode, _stack: CopilotStackSummary): string {
  return mode === 'build' ? 'Build' : 'Ask';
}

export function primaryButtonLabelForAction(action: CopilotAction | null): string {
  if (!action) return 'Send';
  return action.label;
}

/** What Founder OS needs before it can act like a real operator (not a memory dump). */
export function copilotSetupGapMessage(
  mode: CopilotSendMode,
  stack: CopilotStackSummary,
): string | null {
  if (mode === 'build') {
    if (stack.canBuild) return null;
    return [
      '**Build** needs a code agent connected.',
      '',
      '1. Open **Settings → Founder Node** (AI stack)',
      '2. Connect **Cursor** (API key) — or OpenHands',
      '3. Link **GitHub** to your repo',
      '4. Come back and tap **Build** again',
      '',
      '_Until then, use **Ask** for planning — or connect DeepSeek/OpenRouter for smarter answers._',
    ].join('\n');
  }
  return null;
}

export type AiTeamAgentStatus = 'ready' | 'working' | 'offline' | 'needs_setup';

export type AiTeamAgentCard = {
  id: 'research' | 'builder' | 'content';
  label: string;
  role: string;
  status: AiTeamAgentStatus;
  statusLabel: string;
  providerLabel?: string;
};

export function resolveAiTeamCards(
  stack: CopilotStackSummary,
  providers: ProviderRow[],
): AiTeamAgentCard[] {
  const researchConnected = providers.some(
    (p) =>
      p.connected &&
      [
        'DEEPSEEK',
        'JATEVO',
        'OPENROUTER',
        'PHALA',
        'OPENAI',
        'ANTHROPIC',
        'GEMINI',
        'OLLAMA_LOCAL',
      ].includes(p.key),
  );
  const contentConnected = providers.some(
    (p) =>
      p.connected && ['OPENAI', 'ANTHROPIC', 'GEMINI', 'OPENROUTER', 'JATEVO'].includes(p.key),
  );

  return [
    {
      id: 'research',
      label: 'Research Agent',
      role: 'Analysis · tokenomics · strategy',
      status: researchConnected ? 'ready' : 'needs_setup',
      statusLabel: researchConnected ? 'Connected' : 'Connect LLM',
      providerLabel: researchConnected ? 'Founder Brain' : undefined,
    },
    {
      id: 'builder',
      label: 'Builder Agent',
      role: 'Code · PRs · fixes · deploy',
      status: stack.canBuild ? 'ready' : 'needs_setup',
      statusLabel: stack.canBuild ? 'Ready' : 'Connect in Settings',
      providerLabel: stack.canBuild ? 'Builder Agent' : undefined,
    },
    {
      id: 'content',
      label: 'Content Agent',
      role: 'Posts · docs · announcements',
      status: contentConnected ? 'ready' : 'needs_setup',
      statusLabel: contentConnected ? 'Ready' : 'Connect Claude/GPT',
    },
  ];
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
