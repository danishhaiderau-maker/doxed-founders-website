/** Maps worker statuses → trust-building steps in Founder OS chat (P1 Agent Runtime). */

export type AgentRuntimeStep = {
  index: number;
  total: number;
  label: string;
  detail?: string;
  done: boolean;
  active: boolean;
};

const CURSOR_PIPELINE = [
  'Reading repository',
  'Creating plan',
  'Editing files',
  'Generating commit',
  'Opening pull request',
  'Finishing',
] as const;

const OPENHANDS_PIPELINE = [
  'Reading repository',
  'Planning changes',
  'Editing files',
  'Reviewing output',
  'Finishing',
] as const;

function normalizeStatus(status: string): string {
  return status.toUpperCase().replace(/\s+/g, '_');
}

export function mapCursorStatusToStepIndex(status: string, hasPr: boolean): number {
  const s = normalizeStatus(status);
  if (s === 'FINISHED' || s === 'COMPLETED' || s === 'DONE') return hasPr ? 5 : 4;
  if (s === 'ERROR' || s === 'CANCELLED' || s === 'EXPIRED' || s === 'FAILED') return 5;
  if (hasPr) return 4;
  if (s === 'RUNNING' || s === 'IN_PROGRESS' || s === 'WORKING') return 3;
  if (s === 'CREATING' || s === 'PENDING' || s === 'QUEUED') return 1;
  return 2;
}

export function mapOpenHandsStatusToStepIndex(status: string): number {
  const s = normalizeStatus(status);
  if (['FINISHED', 'COMPLETED', 'DONE'].includes(s)) return 4;
  if (['ERROR', 'FAILED', 'CANCELLED', 'STOPPED'].includes(s)) return 4;
  if (s === 'WORKING' || s === 'RUNNING') return 2;
  return 1;
}

export function buildAgentRuntimeSteps(input: {
  worker: 'CURSOR' | 'OPENHANDS';
  status: string;
  prUrl?: string | null;
  branch?: string | null;
}): AgentRuntimeStep[] {
  const pipeline =
    input.worker === 'CURSOR' ? [...CURSOR_PIPELINE] : [...OPENHANDS_PIPELINE];
  const hasPr = Boolean(input.prUrl?.trim());
  const activeIdx =
    input.worker === 'CURSOR'
      ? mapCursorStatusToStepIndex(input.status, hasPr)
      : mapOpenHandsStatusToStepIndex(input.status);
  const total = pipeline.length;

  return pipeline.map((label, index) => ({
    index: index + 1,
    total,
    label,
    detail:
      index === 4 && hasPr && input.worker === 'CURSOR'
        ? input.prUrl!.trim()
        : index === 3 && input.branch?.trim()
          ? `Branch: ${input.branch.trim()}`
          : undefined,
    done: index < activeIdx,
    active: index === activeIdx,
  }));
}

export function formatAgentRuntimeStepsBlock(steps: AgentRuntimeStep[]): string {
  const lines = steps.map((s) => {
    const mark = s.done ? '✓' : s.active ? '→' : '○';
    const detail = s.detail ? ` — ${s.detail}` : '';
    return `${mark} **${s.index}/${s.total}** ${s.label}${detail}`;
  });
  return ['**Agent run**', '', ...lines].join('\n');
}
