import type { FounderMemoryGraph, FounderMemoryGraphPatch } from './founder-memory-graph';
import type { MissionIntelligence } from './founder-brain-context';

/** Stale vault/tasks.json items that should not drive Resume or auto-build. */
export function isStaleBoilerplateMissionTask(title: string | null | undefined): boolean {
  if (!title?.trim()) return false;
  const t = title.trim().toLowerCase();
  return (
    /security hardening|owasp|burp suite|hsts header|review api keys/.test(t) ||
    /define (mvp|your next milestone|your next)/.test(t) ||
    /^define your next milestone/.test(t) ||
    /deploy verification.*(dns|ssl|page load)/.test(t) ||
    /^list acceptance criteria/.test(t) ||
    /^map user stories/.test(t)
  );
}

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
export function resolveMissionBuildTask(
  graph: FounderMemoryGraph,
  intel?: Pick<MissionIntelligence, 'recommendedNextStep' | 'currentInitiative'> | null,
): {
  spec: string;
  taskLabel: string;
} {
  const graphTask = graph.current_task?.trim() || '';
  const graphNext = graph.next_action?.trim() || '';
  const useIntel =
    intel?.recommendedNextStep?.trim() &&
    (isStaleBoilerplateMissionTask(graphTask) || isStaleBoilerplateMissionTask(graphNext));

  const taskLabel = useIntel
    ? intel!.recommendedNextStep.trim()
    : graphTask ||
      graphNext ||
      intel?.recommendedNextStep?.trim() ||
      graph.active_goal?.trim() ||
      'Continue project work';
  const spec = useIntel
    ? intel!.recommendedNextStep.trim()
    : graphNext || taskLabel;
  return { spec, taskLabel };
}

/** Resume Work briefing — GitHub + vault snapshot, no fabricated scans. */
export function formatResumeWorkBrief(input: {
  intelligence: MissionIntelligence;
  graph: FounderMemoryGraph;
  repoFullName?: string | null;
  lastCommit?: string | null;
  vaultNote?: string | null;
  launchReadiness?: number;
}): string {
  const { intelligence: intel, graph } = input;
  const lines = [
    '**Resumed — synced GitHub + Mission State**',
    '',
    `_Does not auto-start Builder. Use **Run build** when you want Cursor to implement code._`,
    '',
    `**Current initiative:** ${intel.currentInitiative}`,
    `**Progress:** ${intel.progressPercent}% · Launch readiness ${input.launchReadiness ?? '—'}%`,
    '',
  ];

  if (intel.shippedRecently.length > 0) {
    lines.push(
      '**What you shipped recently (from commits, not tasks.json):**',
      ...intel.shippedRecently.slice(0, 6).map((s) => `• ${s}`),
      '',
    );
  }

  if (intel.themes.length > 0) {
    lines.push(
      `**Active workstreams:** ${intel.themes.slice(0, 4).map((t) => `${t.label} (${t.commitCount})`).join(' · ')}`,
      '',
    );
  }

  if (intel.blocker) lines.push(`**Blocker:** ${intel.blocker}`, '');
  lines.push(`**Start here:** ${intel.recommendedNextStep}`, '');

  if (graph.current_task && !isStaleBoilerplateMissionTask(graph.current_task)) {
    lines.push(`**Mission graph task:** ${graph.current_task}`);
  } else if (graph.current_task) {
    lines.push(`_Vault task "${graph.current_task.slice(0, 80)}" looks stale — updated recommendation above._`);
  }

  if (input.lastCommit) {
    lines.push(`**Latest commit:** ${input.lastCommit.split('\n')[0]?.slice(0, 120)}`);
  }
  if (input.repoFullName) lines.push(`**Repo:** \`${input.repoFullName}\``);
  if (input.vaultNote) lines.push('', input.vaultNote);

  lines.push(
    '',
    '_Tip: Ask “What am I working on?” for a fresh Brain read · Use Build only for implementation._',
  );

  return lines.filter(Boolean).join('\n');
}

/** Copilot instruction when user continues work — graph leads, infra hints follow. */
export function buildContinueFromMissionPrompt(
  graph: FounderMemoryGraph,
  intel?: Pick<MissionIntelligence, 'currentInitiative' | 'recommendedNextStep' | 'blocker'> | null,
): string {
  const parts = [
    'Continue where I left off. Use GitHub commits and mission intelligence as ground truth — ignore stale tasks.json security/MVP boilerplate unless commits support it.',
    `Goal: ${graph.active_goal}`,
  ];
  if (intel?.currentInitiative) parts.push(`Initiative (from GitHub): ${intel.currentInitiative}`);
  if (graph.current_sprint) parts.push(`Sprint: ${graph.current_sprint}`);
  if (graph.current_task && !isStaleBoilerplateMissionTask(graph.current_task)) {
    parts.push(`Current task: ${graph.current_task}`);
  }
  if (intel?.blocker) parts.push(`Blocker: ${intel.blocker}`);
  else if (graph.blocked_by) parts.push(`Blocked by: ${graph.blocked_by}`);
  parts.push(`Do next: ${intel?.recommendedNextStep ?? graph.next_action ?? graph.current_task ?? 'Sync GitHub'}`);
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

/** Display block for dynamic mission intelligence (command center sidebar). */
export function formatMissionIntelligenceBlock(intel: {
  currentInitiative: string;
  progressPercent: number;
  blocker: string | null;
  impact: string;
  recommendedNextStep: string;
  confidence: string;
}): string {
  const lines = [
    '**Mission intelligence**',
    '',
    `**Initiative:** ${intel.currentInitiative}`,
    `**Progress:** ${intel.progressPercent}%`,
    intel.blocker ? `**Blocker:** ${intel.blocker}` : '**Blocker:** —',
    `**Impact:** ${intel.impact}`,
    `**Next step:** ${intel.recommendedNextStep}`,
    `_Confidence: ${intel.confidence}_`,
  ];
  return lines.join('\n');
}
