/** Canonical founder memory — every AI path should read this first (Sprint 1). */

export type FounderMemoryExperimentStatus = 'idle' | 'testing' | 'validated' | 'failed';

export type FounderMemoryGraph = {
  version: 1;
  project: string;
  active_goal: string;
  current_task: string | null;
  blocked_by: string | null;
  next_action: string | null;
  current_branch: string | null;
  current_pr: string | null;
  hypothesis: string | null;
  experiment_status: FounderMemoryExperimentStatus | null;
  updated_at: string;
};

export type FounderMemoryGraphPatch = Partial<
  Omit<FounderMemoryGraph, 'version' | 'updated_at'>
>;

export type FounderMemoryGraphHints = {
  projectName?: string;
  activeGoal?: string;
  currentTask?: string | null;
  nextAction?: string | null;
  blockedBy?: string | null;
  currentBranch?: string | null;
  currentPr?: string | null;
};

const GOAL_KEYS = ['active_goal', 'activeGoal'] as const;

function pickString(obj: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === 'string' && v.trim()) return v.trim();
  }
  return null;
}

export function parseFounderMemoryGraph(raw: unknown): FounderMemoryGraph | null {
  if (!raw || typeof raw !== 'object') return null;
  const o = raw as Record<string, unknown>;
  const project = pickString(o, ['project']);
  const activeGoal = pickString(o, [...GOAL_KEYS]);
  if (!project || !activeGoal) return null;

  const exp = o.experiment_status ?? o.experimentStatus;
  const experimentStatus =
    exp === 'idle' || exp === 'testing' || exp === 'validated' || exp === 'failed' ? exp : null;

  return {
    version: 1,
    project,
    active_goal: activeGoal,
    current_task: pickString(o, ['current_task', 'currentTask']),
    blocked_by: pickString(o, ['blocked_by', 'blockedBy']),
    next_action: pickString(o, ['next_action', 'nextAction']),
    current_branch: pickString(o, ['current_branch', 'currentBranch']),
    current_pr: pickString(o, ['current_pr', 'currentPr']),
    hypothesis: pickString(o, ['hypothesis']),
    experiment_status: experimentStatus,
    updated_at:
      typeof o.updated_at === 'string'
        ? o.updated_at
        : typeof o.updatedAt === 'string'
          ? o.updatedAt
          : new Date().toISOString(),
  };
}

export function emptyFounderMemoryGraph(projectName: string, activeGoal: string): FounderMemoryGraph {
  return {
    version: 1,
    project: projectName,
    active_goal: activeGoal,
    current_task: null,
    blocked_by: null,
    next_action: null,
    current_branch: null,
    current_pr: null,
    hypothesis: null,
    experiment_status: null,
    updated_at: new Date().toISOString(),
  };
}

/** Merge stored graph with live hints; user patch wins on overlapping fields. */
export function mergeFounderMemoryGraph(
  stored: FounderMemoryGraph | null,
  hints: FounderMemoryGraphHints,
  patch?: FounderMemoryGraphPatch,
): FounderMemoryGraph {
  const project = patch?.project?.trim() || hints.projectName?.trim() || stored?.project || 'My startup';
  const activeGoal =
    patch?.active_goal?.trim() ||
    hints.activeGoal?.trim() ||
    stored?.active_goal ||
    'Define your next milestone';

  const base = stored ?? emptyFounderMemoryGraph(project, activeGoal);

  const merged: FounderMemoryGraph = {
    ...base,
    project,
    active_goal: activeGoal,
    current_task:
      patch?.current_task !== undefined
        ? patch.current_task
        : hints.currentTask !== undefined
          ? hints.currentTask
          : base.current_task,
    blocked_by:
      patch?.blocked_by !== undefined
        ? patch.blocked_by
        : hints.blockedBy !== undefined
          ? hints.blockedBy
          : base.blocked_by,
    next_action:
      patch?.next_action !== undefined
        ? patch.next_action
        : hints.nextAction !== undefined
          ? hints.nextAction
          : base.next_action,
    current_branch:
      patch?.current_branch !== undefined
        ? patch.current_branch
        : hints.currentBranch !== undefined
          ? hints.currentBranch
          : base.current_branch,
    current_pr:
      patch?.current_pr !== undefined
        ? patch.current_pr
        : hints.currentPr !== undefined
          ? hints.currentPr
          : base.current_pr,
    hypothesis: patch?.hypothesis !== undefined ? patch.hypothesis : base.hypothesis,
    experiment_status:
      patch?.experiment_status !== undefined ? patch.experiment_status : base.experiment_status,
    updated_at: new Date().toISOString(),
  };

  return merged;
}

/** Prefix injected before every LLM system prompt. */
export function buildMemoryPrefix(graph: FounderMemoryGraph): string {
  const lines = [
    '## Founder Memory Graph (read first)',
    `Project: ${graph.project}`,
    `Active goal: ${graph.active_goal}`,
  ];
  if (graph.current_task) lines.push(`Current task: ${graph.current_task}`);
  if (graph.blocked_by) lines.push(`Blocked by: ${graph.blocked_by}`);
  if (graph.next_action) lines.push(`Next action: ${graph.next_action}`);
  if (graph.current_branch) lines.push(`Branch: ${graph.current_branch}`);
  if (graph.current_pr) lines.push(`PR: ${graph.current_pr}`);
  if (graph.hypothesis) lines.push(`Hypothesis: ${graph.hypothesis}`);
  if (graph.experiment_status) lines.push(`Experiment: ${graph.experiment_status}`);
  lines.push(`Memory updated: ${graph.updated_at}`, '');
  return lines.join('\n');
}
