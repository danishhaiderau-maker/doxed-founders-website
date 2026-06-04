import type { AiProviderKey } from './ai-providers';
import { isFounderNodeAiProvider, isRemoteAgentProvider } from './ai-providers';

export type FounderBrainTask = 'research' | 'writing' | 'strategy' | 'code' | 'general';

const TASK_LABELS: Record<FounderBrainTask, string> = {
  research: 'Research',
  writing: 'Writing',
  strategy: 'Strategy',
  code: 'Code planning',
  general: 'General',
};

/** Route label shown in UI — always Founder Brain, never vendor names. */
export function getFounderBrainRouteLabel(task: FounderBrainTask): string {
  return `Founder Brain · ${TASK_LABELS[task]}`;
}

export function classifyFounderBrainTask(prompt: string): FounderBrainTask {
  const t = prompt.trim().toLowerCase();
  if (
    /\b(implement|refactor|debug|fix bug|pull request|create pr|typescript|react component|api route|deploy|ship code|cursor agent)\b/.test(
      t,
    )
  ) {
    return 'code';
  }
  if (/\b(write|draft|announcement|blog post|tweet|thread|copy|headline|email|press release)\b/.test(t)) {
    return 'writing';
  }
  if (
    /\b(strategy|roadmap|priorit|should we|go-to-market|positioning|fundraise|pitch deck|business model)\b/.test(
      t,
    )
  ) {
    return 'strategy';
  }
  if (
    /\b(research|analyze|analyse|compare|tokenomics|competitor|market size|due diligence|data on)\b/.test(
      t,
    )
  ) {
    return 'research';
  }
  return 'general';
}

/** Strong signal that Ask should dispatch Builder Agent instead of an LLM. */
export function shouldDispatchBuilderForCodeAsk(prompt: string, task: FounderBrainTask): boolean {
  if (isFounderRepoStatusPrompt(prompt)) return false;
  if (task !== 'code') return false;
  return /\b(implement|fix|build|ship|refactor|add |create pr|write code|patch)\b/i.test(prompt);
}

/** GitHub ground-truth questions — use Ask + commit intelligence, not Builder or Researcher workers. */
export function isFounderRepoStatusPrompt(prompt: string): boolean {
  const t = prompt.trim().toLowerCase();
  if (!t) return false;
  if (/\b(implement|refactor|fix bug|write code|create pr|open pr|patch)\b/.test(t)) return false;
  return (
    /what (am i|are we|were we|is|are) (working|building|shipping)/.test(t) ||
    /what (have i|did i|have we) (ship|shipped|done)/.test(t) ||
    /what (changed|shipped).{0,30}(today|this week|last 24|yesterday|recently)/.test(t) ||
    /what should (i|we) (work on|ship|build) next/.test(t) ||
    /check (my |our )?(git\s*hub|repo|repository)/.test(t) ||
    /(see|look at|check|review).{0,48}(repo|repository|github|commits?)/.test(t) ||
    /currently working on/.test(t) ||
    /last 24 hours?/.test(t) ||
    /analyze.{0,40}(repo|repository|github|commits?)/.test(t)
  );
}

/** Prefer deterministic commit/PR intelligence over raw LLM paraphrase of tasks.json. */
export function shouldPreferGithubGroundedBrainAnswer(
  prompt: string,
  commitsWithSignal: number,
): boolean {
  return isFounderRepoStatusPrompt(prompt) && commitsWithSignal >= 3;
}

const TASK_PROVIDER_PREFERENCE: Record<Exclude<FounderBrainTask, 'general'>, AiProviderKey[]> = {
  research: ['DEEPSEEK', 'JATEVO', 'PHALA', 'OPENROUTER', 'GEMINI', 'OPENAI', 'ANTHROPIC'],
  writing: ['ANTHROPIC', 'OPENAI', 'OPENROUTER', 'JATEVO', 'DEEPSEEK', 'GEMINI', 'PHALA'],
  strategy: ['JATEVO', 'OPENAI', 'ANTHROPIC', 'DEEPSEEK', 'OPENROUTER', 'GEMINI', 'PHALA'],
  code: ['DEEPSEEK', 'JATEVO', 'OPENAI', 'ANTHROPIC', 'OPENROUTER', 'GEMINI', 'PHALA'],
};

const GLOBAL_LLM_FALLBACK: AiProviderKey[] = [
  'PHALA',
  'JATEVO',
  'OPENROUTER',
  'DEEPSEEK',
  'OPENAI',
  'ANTHROPIC',
  'GEMINI',
];

function isChatLlmProvider(key: AiProviderKey): boolean {
  return key !== 'RULE_BASED' && !isRemoteAgentProvider(key) && !isFounderNodeAiProvider(key);
}

/**
 * Provider try-order for Founder Brain (Sprint 4). Caller skips providers without credentials.
 */
export function buildFounderBrainProviderOrder(
  task: FounderBrainTask,
  defaultProvider: AiProviderKey,
): AiProviderKey[] {
  const order: AiProviderKey[] = [];

  if (task === 'general' && isChatLlmProvider(defaultProvider)) {
    order.push(defaultProvider);
  }

  if (task !== 'general') {
    for (const p of TASK_PROVIDER_PREFERENCE[task]) {
      if (!order.includes(p)) order.push(p);
    }
  }

  if (isChatLlmProvider(defaultProvider) && !order.includes(defaultProvider)) {
    order.push(defaultProvider);
  }

  for (const p of GLOBAL_LLM_FALLBACK) {
    if (!order.includes(p)) order.push(p);
  }

  return order.filter(isChatLlmProvider);
}
