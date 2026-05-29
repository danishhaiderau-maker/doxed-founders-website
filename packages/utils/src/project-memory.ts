export type ProjectMemoryTask = {
  id: string;
  title: string;
  kind: string;
  status: string;
  done: boolean;
};

export type DeploymentHealth = {
  provider: string;
  label: string;
  healthy: boolean;
};

export function computeProjectProgress(input: {
  launchReadiness: number;
  openTasks: number;
  doneTasks: number;
}): number {
  const total = input.openTasks + input.doneTasks;
  const taskPct = total > 0 ? Math.round((input.doneTasks / total) * 100) : input.launchReadiness;
  return Math.round(input.launchReadiness * 0.55 + taskPct * 0.45);
}

export function formatRelativeTime(iso: string | Date | null | undefined): string {
  if (!iso) return 'No activity yet';
  const then = typeof iso === 'string' ? new Date(iso).getTime() : iso.getTime();
  const diffMs = Date.now() - then;
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return 'Yesterday';
  return `${days}d ago`;
}

export function buildDailyStandup(input: {
  founderName: string;
  projectName: string;
  yesterdayCommits: number;
  yesterdayDeploys: number;
  yesterdayHighlights: string[];
  openTasks: string[];
  suggestedNext: string;
  progressPercent: number;
  estimatedDays?: number;
}): string {
  const name = input.founderName.split(' ')[0] ?? 'Founder';
  const highlights =
    input.yesterdayHighlights.length > 0
      ? input.yesterdayHighlights.map((h) => `✓ ${h}`).join('\n')
      : '✓ Building in public on Founder OS';

  const suggestions =
    input.openTasks.length > 0
      ? input.openTasks.slice(0, 3).map((t, i) => `${i + 1}. ${t}`).join('\n')
      : `1. ${input.suggestedNext}`;

  return [
    `Good morning, ${name}.`,
    '',
    'Yesterday:',
    highlights,
    input.yesterdayCommits > 0 ? `✓ ${input.yesterdayCommits} commits` : '',
    input.yesterdayDeploys > 0 ? `✓ ${input.yesterdayDeploys} deployment(s) successful` : '',
    '',
    "Today's suggestions:",
    suggestions,
    '',
    `Progress: ${input.progressPercent}%`,
    input.estimatedDays != null ? `Estimated completion: ~${input.estimatedDays} days` : '',
  ]
    .filter(Boolean)
    .join('\n');
}

export function buildResumeCursorPrompt(input: {
  projectName: string;
  currentGoal: string;
  suggestedNext: string;
  openTasks: string[];
  lastCommit?: string;
}): string {
  return [
    `# Continue: ${input.projectName}`,
    '',
    `Current goal: ${input.currentGoal}`,
    input.lastCommit ? `Last commit: ${input.lastCommit}` : '',
    '',
    'Suggested next:',
    input.suggestedNext,
    '',
    'Open tasks:',
    ...input.openTasks.slice(0, 8).map((t) => `- ${t}`),
    '',
    'Pick up where we left off — ship minimal working version.',
  ]
    .filter(Boolean)
    .join('\n');
}
