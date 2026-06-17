/** Maps worker statuses → trust-building steps in Founder OS chat (P1 Agent Runtime). */

export type AgentRuntimeStep = {
  index: number;
  total: number;
  label: string;
  detail?: string;
  done: boolean;
  active: boolean;
};

/** Phase 3 — 8-step command center pipeline (CodeGrid-style visibility). */
export const COMMAND_CENTER_PIPELINE = [
  'Reading repository',
  'Analyzing architecture',
  'Updating context',
  'Creating branch',
  'Writing code',
  'Running tests',
  'Creating PR',
  'Waiting approval',
] as const;

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

/** Map Cursor/OpenHands status → 0-based index in COMMAND_CENTER_PIPELINE. */
export function mapWorkerStatusToCommandCenterStepIndex(
  worker: 'CURSOR' | 'OPENHANDS',
  status: string,
  hasPr: boolean,
): number {
  const s = normalizeStatus(status);
  if (['FINISHED', 'COMPLETED', 'DONE'].includes(s)) return hasPr ? 7 : 6;
  if (['ERROR', 'CANCELLED', 'EXPIRED', 'FAILED'].includes(s)) return 7;
  if (hasPr) return 6;
  if (worker === 'CURSOR') {
    if (s === 'RUNNING' || s === 'IN_PROGRESS' || s === 'WORKING') return 4;
    if (s === 'CREATING' || s === 'PENDING' || s === 'QUEUED') return 1;
    return 3;
  }
  if (s === 'WORKING' || s === 'RUNNING') return 4;
  return 2;
}

export function buildCommandCenterRuntimeSteps(input: {
  worker: 'CURSOR' | 'OPENHANDS';
  status: string;
  prUrl?: string | null;
  branch?: string | null;
}): AgentRuntimeStep[] {
  const hasPr = Boolean(input.prUrl?.trim());
  const activeIdx = mapWorkerStatusToCommandCenterStepIndex(input.worker, input.status, hasPr);
  const total = COMMAND_CENTER_PIPELINE.length;

  return COMMAND_CENTER_PIPELINE.map((label, index) => ({
    index: index + 1,
    total,
    label,
    detail:
      index === 6 && hasPr
        ? input.prUrl!.trim()
        : index === 3 && input.branch?.trim()
          ? `Branch: ${input.branch.trim()}`
          : undefined,
    done: index < activeIdx,
    active: index === activeIdx,
  }));
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
  /** Phase 3 — use 8-step command center pipeline in UI. */
  commandCenter?: boolean;
}): AgentRuntimeStep[] {
  if (input.commandCenter !== false) {
    return buildCommandCenterRuntimeSteps(input);
  }
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

export function formatAgentRuntimeStepsBlock(steps: AgentRuntimeStep[], title = 'Builder Agent'): string {
  const lines = steps.map((s) => {
    const mark = s.done ? '✓' : s.active ? '→' : '○';
    const detail = s.detail ? ` — ${s.detail}` : '';
    return `${mark} **${s.index}/${s.total}** ${s.label}${detail}`;
  });
  return [`**${title}**`, '', ...lines].join('\n');
}
