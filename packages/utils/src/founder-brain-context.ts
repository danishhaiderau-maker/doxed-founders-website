import type { FounderMemoryGraph } from './founder-memory-graph';
import { isStaleBoilerplateMissionTask } from './mission-state';
import {
  filterCommitsForIntelligence,
  groupCommitsByInitiative,
  summarizeShippedOutcomes,
  type CommitSignal,
  type InitiativeTheme,
} from './commit-intelligence';

export type MissionIntelligence = {
  currentInitiative: string;
  progressPercent: number;
  blocker: string | null;
  impact: string;
  recommendedNextStep: string;
  confidence: 'low' | 'medium' | 'high';
  themes: InitiativeTheme[];
  shippedRecently: string[];
};

export type FounderBrainContextInput = {
  projectName: string;
  projectDescription?: string | null;
  repoFullName?: string | null;
  currentGoal: string;
  progressPercent: number;
  launchReadiness: number;
  suggestedNextStep: string;
  openTasks: string[];
  roadmapInProgress?: string | null;
  memoryGraph: FounderMemoryGraph | null;
  commits: CommitSignal[];
  pullRequests: { title: string; state: string; url: string; number: number }[];
  recentDeploys: { title: string; at: string }[];
  projectContextExcerpt?: string | null;
  roadmapExcerpt?: string | null;
  repoTasks?: string[];
  weeklySummary?: string;
  workspaceActivityBlock?: string | null;
  vaultNote?: string | null;
  timelineExcerpt?: string | null;
  deployIntelligenceExcerpt?: string | null;
  desktopBridgeBlock?: string | null;
};

export function deriveMissionIntelligence(input: FounderBrainContextInput): MissionIntelligence {
  const signalCommits = filterCommitsForIntelligence(input.commits);
  const themes = groupCommitsByInitiative(signalCommits);
  const shippedRecently = summarizeShippedOutcomes(signalCommits, 8);
  const topTheme = themes[0];

  const graph = input.memoryGraph;
  const initiativeFromCommits = topTheme
    ? `${topTheme.label} (${topTheme.commitCount} recent commits)`
    : null;

  const githubSignalsStrong = signalCommits.length >= 3 && Boolean(topTheme);
  const currentInitiative =
    (githubSignalsStrong && initiativeFromCommits) ||
    graph?.current_sprint?.trim() ||
    initiativeFromCommits ||
    input.roadmapInProgress ||
    input.currentGoal;

  const openPrs = input.pullRequests.filter((p) => p.state === 'open');
  const mergedRecently = input.pullRequests.filter((p) => p.state === 'closed').slice(0, 3);

  let blocker = graph?.blocked_by?.trim() ?? null;
  if (!blocker && openPrs.length > 0) {
    blocker = `${openPrs.length} open PR(s) need review — ${openPrs[0]!.title.slice(0, 80)}`;
  }
  if (!blocker && input.commits.length === 0 && input.repoFullName) {
    blocker = 'No recent commits synced — run GitHub sync in AI Stack';
  }

  const impact =
    shippedRecently.length > 0
      ? `Recent work advances ${topTheme?.label ?? 'the product'} — latest: ${shippedRecently[0]!.slice(0, 100)}`
      : `Launch readiness ${input.launchReadiness}% — connect repo activity for sharper signals`;

  const syncNoiseRatio =
    input.commits.length > 0
      ? 1 - signalCommits.length / input.commits.length
      : 0;
  const batchedSyncAlreadyLive = input.commits.some((c) =>
    /sync memory \(context \+ roadmap \+ tasks\)/i.test(c.message),
  );
  const syncHygieneStep =
    !batchedSyncAlreadyLive && syncNoiseRatio >= 0.4 && input.commits.length >= 6
      ? 'Batch Founder OS sync (context + roadmap + tasks) into one commit; skip when unchanged'
      : null;

  const freshOpenTask = input.openTasks.find((t) => !isStaleBoilerplateMissionTask(t));
  const graphNext = graph?.next_action?.trim();
  const suggested = input.suggestedNextStep?.trim();

  const recommendedNextStep =
    syncHygieneStep ||
    (openPrs.length > 0
      ? `Review open PR #${openPrs[0]!.number}: ${openPrs[0]!.title.slice(0, 80)}`
      : null) ||
    (graphNext && !isStaleBoilerplateMissionTask(graphNext) ? graphNext : null) ||
    (githubSignalsStrong ? null : freshOpenTask) ||
    (suggested && !isStaleBoilerplateMissionTask(suggested) ? suggested : null) ||
    (mergedRecently.length > 0
      ? `Review merged PR and deploy: ${mergedRecently[0]!.title.slice(0, 80)}`
      : null) ||
    (shippedRecently[0]
      ? `Publish or ship next: ${shippedRecently[0]!.slice(0, 80)}`
      : 'Ship the top open task and sync GitHub');

  const resolvedNextStep =
    recommendedNextStep ??
    (shippedRecently[0]
      ? `Continue ${topTheme?.label ?? 'the product'} — latest ship: ${shippedRecently[0]!.slice(0, 80)}`
      : 'Sync GitHub and ship the next feature commit');

  const signalCount =
    signalCommits.length +
    input.pullRequests.length +
    input.recentDeploys.length +
    (graph?.current_task ? 1 : 0);

  const confidence: MissionIntelligence['confidence'] =
    signalCount >= 15 ? 'high' : signalCount >= 5 ? 'medium' : 'low';

  return {
    currentInitiative,
    progressPercent: input.progressPercent,
    blocker,
    impact,
    recommendedNextStep: resolvedNextStep,
    confidence,
    themes: themes.slice(0, 6),
    shippedRecently,
  };
}

export function formatFounderBrainContextForPrompt(
  input: FounderBrainContextInput,
  intelligence: MissionIntelligence,
): string {
  const sections: string[] = [
    '# Founder Brain Context (assembled — do not answer from task titles alone)',
    '',
    '## Project',
    `Name: ${input.projectName}`,
    input.projectDescription ? `Description: ${input.projectDescription.slice(0, 600)}` : '',
    input.repoFullName ? `Repository: ${input.repoFullName}` : 'Repository: not linked',
    '',
    '## Mission intelligence (derived from GitHub + memory)',
    `Current initiative: ${intelligence.currentInitiative}`,
    `Progress: ${intelligence.progressPercent}% · Launch readiness ${input.launchReadiness}%`,
    `Impact: ${intelligence.impact}`,
    intelligence.blocker ? `Blocker: ${intelligence.blocker}` : 'Blocker: none detected',
    `Recommended next step: ${intelligence.recommendedNextStep}`,
    `Confidence: ${intelligence.confidence}`,
    '',
    '## Static mission graph',
    input.memoryGraph
      ? [
          `Active goal: ${input.memoryGraph.active_goal}`,
          input.memoryGraph.current_sprint ? `Sprint: ${input.memoryGraph.current_sprint}` : '',
          input.memoryGraph.current_task ? `Current task: ${input.memoryGraph.current_task}` : '',
          input.memoryGraph.next_action ? `Next action: ${input.memoryGraph.next_action}` : '',
        ]
          .filter(Boolean)
          .join('\n')
      : 'Mission graph: not initialized',
    '',
    '## Initiative themes (from recent commits)',
    intelligence.themes.length > 0
      ? intelligence.themes
          .map((t) => `- ${t.label}: ${t.commitCount} commit(s) — e.g. ${t.samples[0] ?? 'n/a'}`)
          .join('\n')
      : '- No commit themes yet — sync GitHub',
    '',
    '## What shipped recently (outcomes, not raw sync messages)',
    intelligence.shippedRecently.length > 0
      ? intelligence.shippedRecently.map((s) => `- ${s}`).join('\n')
      : '- No feature/fix commits in window',
    '',
    '## Recent commits (last 30)',
    input.commits.length > 0
      ? input.commits
          .slice(0, 30)
          .map((c) => `- ${c.sha?.slice(0, 7) ?? ''} ${c.message}`)
          .join('\n')
      : '- None synced',
    '',
    '## Pull requests',
    input.pullRequests.length > 0
      ? input.pullRequests
          .slice(0, 12)
          .map((p) => `- [${p.state}] #${p.number} ${p.title}`)
          .join('\n')
      : '- None listed',
    '',
    '## Recent deployments',
    input.recentDeploys.length > 0
      ? input.recentDeploys.map((d) => `- ${d.title} (${d.at})`).join('\n')
      : '- None in window',
    '',
    '## Open tasks',
    input.openTasks.length > 0 ? input.openTasks.map((t) => `- ${t}`).join('\n') : '- None',
  ];

  if (input.projectContextExcerpt) {
    sections.push('', '## project-context.md (excerpt)', input.projectContextExcerpt.slice(0, 1500));
  }
  if (input.roadmapExcerpt) {
    sections.push('', '## roadmap.md (excerpt)', input.roadmapExcerpt.slice(0, 1000));
  }
  if (input.repoTasks?.length) {
    sections.push('', '## Repo tasks.json', input.repoTasks.map((t) => `- ${t}`).join('\n'));
  }
  if (input.workspaceActivityBlock) {
    sections.push('', '## Workspace activity', input.workspaceActivityBlock);
  }
  if (input.vaultNote) sections.push('', input.vaultNote);
  if (input.weeklySummary) sections.push('', '## Weekly summary', input.weeklySummary);
  if (input.timelineExcerpt) {
    sections.push('', '## Project timeline (narrative)', input.timelineExcerpt);
  }
  if (input.deployIntelligenceExcerpt) {
    sections.push('', '## Deployment intelligence', input.deployIntelligenceExcerpt);
  }
  if (input.desktopBridgeBlock) sections.push('', input.desktopBridgeBlock);

  sections.push(
    '',
    '## Response rules',
    '- Answer as a command center: initiative, what changed, why it matters, blockers, next step.',
    '- Prefer **Recent commits**, **Initiative themes**, and **What shipped recently** over repo tasks.json / roadmap boilerplate.',
    '- Ignore chore(founder-os): sync * commits when describing current work — call out sync noise as hygiene if >40% of commits.',
    '- Never reply with only task.json titles or generic "define milestone" when GitHub commit data exists.',
    '- Name real initiatives (Feed, Discover, Founder OS, Vault, Builder, Predictions) when commits support them.',
  );

  return sections.filter(Boolean).join('\n');
}

export function formatRuleBasedBrainAnswer(
  intelligence: MissionIntelligence,
  input: FounderBrainContextInput,
  prompt: string,
): string {
  const q = prompt.toLowerCase();
  const workingOn =
    /what.*(working|building|focus)|what am i|what should i ship|status|left off/i.test(q);

  if (workingOn) {
    return [
      `**Current initiative:** ${intelligence.currentInitiative}`,
      '',
      `**Progress:** ${intelligence.progressPercent}% · Launch readiness ${input.launchReadiness}%`,
      '',
      intelligence.shippedRecently.length > 0
        ? `**What shipped recently:**\n${intelligence.shippedRecently.map((s) => `• ${s}`).join('\n')}`
        : '**What shipped recently:** Sync GitHub to pull commit outcomes.',
      '',
      intelligence.themes.length > 0
        ? `**Active workstreams:** ${intelligence.themes.slice(0, 4).map((t) => t.label).join(' · ')}`
        : '',
      '',
      intelligence.blocker ? `**Blocker:** ${intelligence.blocker}` : '',
      '',
      `**Why it matters:** ${intelligence.impact}`,
      '',
      `**Recommended next step:** ${intelligence.recommendedNextStep}`,
      '',
      `_Confidence: ${intelligence.confidence} · from ${input.commits.length} commits, ${input.pullRequests.length} PRs, mission memory_`,
      '',
      input.repoFullName ? `Repo: \`${input.repoFullName}\`` : '',
    ]
      .filter(Boolean)
      .join('\n');
  }

  return [
    `**${input.projectName}** — ${intelligence.currentInitiative}`,
    '',
    intelligence.shippedRecently.slice(0, 4).map((s) => `• ${s}`).join('\n') || '• Sync GitHub for recent work',
    '',
    `**Next:** ${intelligence.recommendedNextStep}`,
  ]
    .filter(Boolean)
    .join('\n');
}
