import { createHash } from 'node:crypto';

export interface EfficientPromptMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string;
  name?: string;
  tool_call_id?: string;
  tool_calls?: Array<{
    id: string;
    type: 'function';
    function: { name: string; arguments: string };
  }>;
}

export interface PromptEfficiencyEstimate {
  measurement: 'estimated';
  baselineTokens: number;
  sentTokens: number;
  avoidedTokens: number;
  savingsPercent: number;
  compactedToolResults: number;
  removedStaleCoordinationBlocks: number;
  techniques: string[];
}

export interface PromptEfficiencyPlan {
  messages: EfficientPromptMessage[];
  estimate: PromptEfficiencyEstimate;
}

export const DEFAULT_TOOL_RESULT_CHAR_BUDGET = 16_000;
const COORDINATION_PREFIX = '## Live agent coordination';

export function estimateTokensFromText(value: string): number {
  return value.length === 0 ? 0 : Math.ceil(value.length / 4);
}

export function estimateMessagesTokens(messages: readonly EfficientPromptMessage[]): number {
  return messages.reduce((total, message) => {
    const toolCallChars = message.tool_calls ? JSON.stringify(message.tool_calls) : '';
    return total + estimateTokensFromText(message.content) + estimateTokensFromText(toolCallChars) + 4;
  }, 0);
}

export function composeFounderSystemPrompt(input: {
  identity: string;
  memory?: string;
  projectContext?: string;
  coordination?: string;
  additionalStableContext?: string;
}): string {
  return [
    input.identity,
    input.memory,
    input.projectContext,
    input.additionalStableContext,
    input.coordination,
  ]
    .map((block) => block?.trim() ?? '')
    .filter(Boolean)
    .join('\n\n');
}

export function composeFounderPromptMessages(input: {
  identity: string;
  memory?: string;
  projectContext?: string;
  coordination?: string;
  additionalStableContext?: string;
}): EfficientPromptMessage[] {
  const identity = input.identity.trim();
  const context = [
    input.memory,
    input.projectContext,
    input.additionalStableContext,
    input.coordination,
  ]
    .map((block) => block?.trim() ?? '')
    .filter(Boolean)
    .join('\n\n');
  return [
    ...(identity ? [{ role: 'system' as const, content: identity }] : []),
    ...(context ? [{ role: 'system' as const, content: context }] : []),
  ];
}

function compactToolResult(content: string, budget: number): string {
  if (content.length <= budget) return content;
  const digest = createHash('sha256').update(content, 'utf8').digest('hex').slice(0, 16);
  const marker = `\n\n[Founder compacted ${content.length - budget} characters; full output remains in the local terminal; sha256:${digest}]\n\n`;
  const available = Math.max(200, budget - marker.length);
  const head = Math.floor(available * 0.6);
  return `${content.slice(0, head)}${marker}${content.slice(-(available - head))}`;
}

export function planPromptEfficiency(
  input: readonly EfficientPromptMessage[],
  options: { maxToolResultChars?: number } = {},
): PromptEfficiencyPlan {
  const baselineTokens = estimateMessagesTokens(input);
  const maxToolResultChars = Math.max(1_000, options.maxToolResultChars ?? DEFAULT_TOOL_RESULT_CHAR_BUDGET);
  const latestCoordination = input.reduce(
    (latest, message, index) =>
      message.role === 'system' && message.content.trimStart().startsWith(COORDINATION_PREFIX)
        ? index
        : latest,
    -1,
  );
  let compactedToolResults = 0;
  let removedStaleCoordinationBlocks = 0;
  const messages: EfficientPromptMessage[] = [];

  for (let index = 0; index < input.length; index += 1) {
    const message = input[index]!;
    if (
      message.role === 'system'
      && message.content.trimStart().startsWith(COORDINATION_PREFIX)
      && index !== latestCoordination
    ) {
      removedStaleCoordinationBlocks += 1;
      continue;
    }
    if (message.role === 'tool' && message.content.length > maxToolResultChars) {
      compactedToolResults += 1;
      messages.push({ ...message, content: compactToolResult(message.content, maxToolResultChars) });
      continue;
    }
    messages.push({ ...message });
  }

  const sentTokens = estimateMessagesTokens(messages);
  const avoidedTokens = Math.max(0, baselineTokens - sentTokens);
  const techniques = ['stable-system-prefix'];
  if (compactedToolResults > 0) techniques.push('bounded-tool-results');
  if (removedStaleCoordinationBlocks > 0) techniques.push('latest-coordination-only');

  return {
    messages,
    estimate: {
      measurement: 'estimated',
      baselineTokens,
      sentTokens,
      avoidedTokens,
      savingsPercent: baselineTokens > 0
        ? Math.round((avoidedTokens / baselineTokens) * 10_000) / 100
        : 0,
      compactedToolResults,
      removedStaleCoordinationBlocks,
      techniques,
    },
  };
}
