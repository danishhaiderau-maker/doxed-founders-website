import type {
  FounderGoalUiDecision,
  FounderGoalUiState,
} from './founder-goal-state';

const MAX_GOAL_CONTEXT_CHARS = 2_400;
const MAX_PENDING_DECISIONS = 5;
const MAX_BLOCKED_TASKS = 8;

export function renderFounderGoalContext(
  state: FounderGoalUiState | null | undefined,
): string {
  if (!state) return '';
  const objective = compact(state.objective, 500);
  if (!objective) return '';
  const pending = state.decisions
    .filter((decision) => decision.status === 'pending')
    .slice(0, MAX_PENDING_DECISIONS);
  const blockedTasks = Array.from(new Set(
    pending.flatMap((decision) => decision.blockingTaskIds),
  )).slice(0, MAX_BLOCKED_TASKS);
  const lines = [
    '## Pursuing Goal (North Star)',
    `Goal id: ${compact(state.id, 120)}`,
    `Version: ${Math.max(1, Math.trunc(state.version))}`,
    `Status: ${state.status}`,
    `Objective: ${objective}`,
    '',
    'Operating rules:',
    '- Keep this objective as the parent outcome for the current request.',
    '- Do not silently replace, expand, or declare this goal complete.',
    '- A pending decision blocks only the task ids listed below; unrelated safe work may continue.',
    '- Research, silence, timeout, or a recommended option never grants permission for deletion, deployment, secret use, purchases, or external writes.',
    '- Report evidence against both the current request and this goal before claiming completion.',
  ];
  if (pending.length > 0) {
    lines.push('', `Pending founder decisions (${pending.length} shown):`);
    lines.push(...pending.map(renderPendingDecision));
  }
  if (blockedTasks.length > 0) {
    lines.push(`Blocked task ids: ${blockedTasks.map((task) =>
      compact(task, 120)).filter(Boolean).join(', ')}`);
  }
  return lines.join('\n').slice(0, MAX_GOAL_CONTEXT_CHARS);
}

function renderPendingDecision(decision: FounderGoalUiDecision) {
  const independent = decision.independentWorkMayContinue
    ? 'unrelated work may continue'
    : 'wait for founder';
  return `- ${compact(decision.title, 160)} [${decision.risk}; ${independent}]`;
}

function compact(value: string, max: number) {
  return value.replace(/\s+/g, ' ').trim().slice(0, max);
}
