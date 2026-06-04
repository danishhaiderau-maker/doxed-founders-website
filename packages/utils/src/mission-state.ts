import type { FounderMemoryGraph, FounderMemoryGraphPatch } from './founder-memory-graph';

export function detectContinueMissionIntent(text: string): boolean {
  return /continue\s+(where\s+i\s+left\s+off|last\s+task)|resume\s+(work|where)|pick\s+up\s+where/i.test(
    text.trim(),
  );
}

const FAILURE_STATUSES = new Set(['ERROR', 'FAILED', 'CANCELLED', 'EXPIRED', 'STOPPED']);

export function isBuilderRunFailureStatus(status: string): boolean {
  return FAILURE_STATUSES.has(status.toUpperCase());
}

export function isBuilderRunSuccessStatus(status: string): boolean {
  const s = status.toUpperCase();
  return s === 'FINISHED' || s === 'COMPLETED' || s === 'DONE';
}

/** Human-readable Mission State block for resume / continue flows. */
export function formatMissionStateBlock(
  graph: FounderMemoryGraph,
  extras?: { lastCommit?: string | null; openTaskCount?: number },
): string {
  const lines = [
    '**Mission State**',
    '',
    `**Goal:** ${graph.active_goal}`,
  ];
  if (graph.current_sprint) lines.push(`**Sprint:** ${graph.current_sprint}`);
  if (graph.current_task) lines.push(`**Current task:** ${graph.current_task}`);
  if (graph.blocked_by) lines.push(`**Blocked by:** ${graph.blocked_by}`);
  if (graph.next_action) lines.push(`**Next action:** ${graph.next_action}`);
  if (graph.current_branch) lines.push(`**Branch:** \`${graph.current_branch}\``);
  if (graph.current_pr) lines.push(`**PR:** ${graph.current_pr}`);

  if (extras?.lastCommit) {
    lines.push('', `**Last commit:** ${extras.lastCommit.split('\n')[0]?.slice(0, 100)}`);
  }
  if (extras?.openTaskCount != null) {
    lines.push(`**Open queue items:** ${extras.openTaskCount}`);
  }

  lines.push('', '_Updated ' + new Date(graph.updated_at).toLocaleString() + '_');
  return lines.join('\n');
}

/** Spec + label for one-click builder dispatch from Mission State (Sprint 7d). */
export function resolveMissionBuildTask(graph: FounderMemoryGraph): {
  spec: string;
  taskLabel: string;
} {
  const taskLabel =
    graph.current_task?.trim() ||
    graph.next_action?.trim() ||
    graph.active_goal?.trim() ||
    'Continue project work';
  const spec = graph.next_action?.trim() || taskLabel;
  return { spec, taskLabel };
}

/** Copilot instruction when user continues work — graph leads, infra hints follow. */
export function buildContinueFromMissionPrompt(graph: FounderMemoryGraph): string {
  const parts = [
    'Continue where I left off using this Mission State as ground truth.',
    `Goal: ${graph.active_goal}`,
  ];
  if (graph.current_sprint) parts.push(`Sprint: ${graph.current_sprint}`);
  if (graph.current_task) parts.push(`Current task: ${graph.current_task}`);
  if (graph.blocked_by) parts.push(`Blocked by: ${graph.blocked_by}`);
  if (graph.next_action) parts.push(`Do next: ${graph.next_action}`);
  if (graph.current_pr) parts.push(`PR: ${graph.current_pr}`);
  if (graph.current_branch) parts.push(`Branch: ${graph.current_branch}`);
  return parts.join('\n');
}

export type AfterBuildPatchInput = {
  task: string;
  status: string;
  result?: string | null;
  branch?: string | null;
  prUrl?: string | null;
  previous: FounderMemoryGraph;
};

/** Light rules to refresh Mission State when a builder run finishes. */
export function deriveMissionPatchAfterBuild(input: AfterBuildPatchInput): FounderMemoryGraphPatch {
  const { task, status, result, branch, prUrl, previous } = input;
  const patch: FounderMemoryGraphPatch = {};

  if (branch?.trim()) patch.current_branch = branch.trim();
  if (prUrl?.trim()) patch.current_pr = prUrl.trim();

  if (isBuilderRunFailureStatus(status)) {
    patch.blocked_by = `Builder run ${status}${result ? `: ${result.slice(0, 160)}` : ''}`;
    return patch;
  }

  if (!isBuilderRunSuccessStatus(status)) {
    return patch;
  }

  const taskNorm = task.trim().toLowerCase();
  const currentNorm = (previous.current_task ?? '').trim().toLowerCase();
  const wasCurrent = !currentNorm || currentNorm === taskNorm || taskNorm.includes(currentNorm);

  if (wasCurrent) {
    const promoted = previous.next_action?.trim();
    if (promoted) patch.current_task = promoted;
    patch.next_action = result?.trim()
      ? `Review builder output and ship: ${result.trim().slice(0, 140)}`
      : 'Review the PR, run smoke tests, and deploy';
    patch.blocked_by = null;
  } else if (!previous.next_action?.trim()) {
    patch.next_action = 'Review builder output on GitHub';
  }

  return patch;
}
