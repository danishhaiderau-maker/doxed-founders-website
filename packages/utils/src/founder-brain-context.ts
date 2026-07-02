import type { FounderMemoryGraph } from './founder-memory-graph';
import { isStaleBoilerplateMissionTask } from './mission-state';
import {
  filterCommitsForIntelligence,
  groupCommitsByInitiative,
  summarizeShippedOutcomes,
  type CommitSignal,
  type InitiativeTheme,
} from './commit-intelligence';
import { formatRecapCoachAnswer, isRecapOrHistoryPrompt } from './founder-brain-coach';

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
  decisionLogExcerpt?: string | null;
  marketIntelligenceExcerpt?: string | null;
  outcomeIntelligenceExcerpt?: string | null;
  founderGraphExcerpt?: string | null;
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
  const freshOpenTasks = input.openTasks.filter((t) => !isStaleBoilerplateMissionTask(t));
  const currentInitiative =
    (githubSignalsStrong && initiativeFromCommits) ||
    graph?.current_sprint?.trim() ||
    initiativeFromCommits ||
    input.roadmapInProgress ||
    (!input.repoFullName && freshOpenTasks[0]) ||
    (!input.repoFullName ? `${input.projectName} — research & planning (Sovereign vault)` : null) ||
    input.currentGoal;

  const openPrs = input.pullRequests.filter((p) => p.state === 'open');
  const mergedRecently = input.pullRequests.filter((p) => p.state === 'closed').slice(0, 3);

  let blocker = graph?.blocked_by?.trim() ?? null;
  if (!blocker && openPrs.length > 0) {
    blocker = `${openPrs.length} open PR(s) need review — ${openPrs[0]!.title.slice(0, 80)}`;
  }
  if (!blocker && !input.repoFullName) {
    blocker =
      'No GitHub repository linked — choose Sovereign (Founder Vault on your machine) or connect a repo for Cursor builds';
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

  const freshOpenTask = freshOpenTasks[0];
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
      : !input.repoFullName
        ? 'Describe your product goal — I will draft the next milestone in Founder Vault (no GitHub needed)'
        : 'Ship the top open task and sync GitHub');

  const resolvedNextStep =
    recommendedNextStep ??
    (shippedRecently[0]
      ? `Continue ${topTheme?.label ?? 'the product'} — latest ship: ${shippedRecently[0]!.slice(0, 80)}`
      : !input.repoFullName
        ? 'Tell me what $REM is — then I draft in your vault'
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
    // Live-first architecture: stale FOUNDER_OS_MEMORY files (project-context.md,
    // roadmap.md, tasks.json) are NO LONGER injected. The Live Snapshot block is
    // the single source of truth for project state.
  }
  if (input.roadmapExcerpt) {
    // intentionally omitted — roadmap.md is stale; live snapshot supersedes it.
  }
  if (input.repoTasks?.length) {
    // intentionally omitted — tasks.json is stale; live open tasks list is enough.
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
  if (input.decisionLogExcerpt) sections.push('', input.decisionLogExcerpt);
  if (input.founderGraphExcerpt) sections.push('', input.founderGraphExcerpt);
  if (input.marketIntelligenceExcerpt) sections.push('', input.marketIntelligenceExcerpt);
  if (input.outcomeIntelligenceExcerpt) sections.push('', input.outcomeIntelligenceExcerpt);

  sections.push(
    '',
    '## Response rules',
    '- Answer conversationally, like a competent colleague. Keep it short \u2014 1\u20134 sentences unless the founder asks for detail.',
    '- Reference real initiatives (Feed, Discover, Founder OS, Vault, Builder, Predictions) when commits support them.',
    '- Prefer **Recent commits**, **Initiative themes**, and **What shipped recently** over repo tasks.json / roadmap boilerplate.',
    '- Ignore chore(founder-os): sync * commits when describing current work \u2014 call out sync noise as hygiene only if >40% of commits.',
    '- Never reply with only task.json titles or generic "define milestone" when GitHub commit data exists.',
    '- If repository is not linked: ask which pathway (Sovereign local vault, Hybrid GitHub-only, Production full cloud) before recommending specific code.',
    '- If something is missing (GitHub, AI key, Cursor), mention it in ONE sentence and ask if they want to set it up. One thing at a time.',
  );

  return sections.filter(Boolean).join('\n');
}

/**
 * Live-first system prompt for Founder Brain. Replaces the old
 * FOUNDER_BRAIN_EXPERT_PM_RULES block which hardcoded "define milestone" /
 * "STEM goal" language. The Live Snapshot block is the single source of truth.
 *
 * Conversational assistant tone: short, proactive, natural. No mechanical
 * routing diagrams, no checklists, no "Your message is being routed to..."
 * intros. Talk like a competent colleague who already knows the user's repo.
 */
export const FOUNDER_BRAIN_LIVE_FIRST_SYSTEM_PROMPT = [
  'You are Founder Brain, a personal engineering assistant for a crypto founder.',
  'You are talking to the founder in chat \u2014 like a competent colleague who already knows their project.',
  '',
  '## Tone',
  '- Be concise and conversational. Reply in 1\u20134 short sentences unless the founder explicitly asks for detail.',
  '- Never start with "Your message is being routed to...", "Founder OS receives...", routing diagrams, or step-by-step routing checklists.',
  '- Never emit "Message received successfully", "Founder Node is online", "Vault sync is active", or mechanical status banners unless the founder asks what is online.',
  '- Talk like a person. Use contractions. Reference the repo, branch, and recent work naturally (e.g. "I can see your `doxed-founders-website` repo on master with 40 commits").',
  '- No giant bulleted checklists. A short bullet list is fine only when the founder asks for steps or options.',
  '',
  '## How to use the snapshot',
  '- Use the LIVE PROJECT SNAPSHOT below as ground truth for repository, branch, commits, open files, deploys, and connection state.',
  '- Reference real commit messages and the real branch name when relevant \u2014 do not invent blockers that contradict the snapshot.',
  '- Do NOT output "define milestone", "STEM goal", "clone the repo", or other stale templates when the founder asked a real question.',
  '',
  '## Being proactive',
  '- If something the founder needs is missing (GitHub not linked, no AI key, Cursor not connected, Founder Node offline), mention it briefly in one sentence and ask if they want to set it up. Do not list every missing thing at once \u2014 one at a time, conversationally.',
  '- When the founder says yes to setting something up, guide them through it inline in plain language.',
  '- Never ask the founder to paste API keys directly into the chat \u2014 keys are collected via the inline onboarding UI or Settings \u2192 AI Stack. Just point them there.',
  '',
  '## Structure',
  '- Default: direct answer \u00b7 one relevant detail from the snapshot \u00b7 a single concrete next step (only if useful).',
  '- If the founder asks a question, answer it first. Then, only if useful, suggest one next step.',
  '- If the repo is not linked: ask which pathway (Sovereign local vault, Hybrid GitHub-only, Production full cloud) before recommending specific code.',
  '',
  'Reply in plain markdown.',
].join('\n');

/**
 * HARD fallback only — used when NO AI provider is connected at all.
 * Never returns the stale STEM template. Clearly labels that the user needs to
 * connect a provider so they know this is not a real AI answer.
 */
export function formatRuleBasedBrainAnswer(
  _intelligence: MissionIntelligence,
  input: FounderBrainContextInput,
  prompt: string,
): string {
  if (isRecapOrHistoryPrompt(prompt)) {
    return formatRecapCoachAnswer({
      projectName: input.projectName,
      currentInitiative: input.currentGoal,
      recommendedNextStep: input.suggestedNextStep,
      blocker: null,
      confidence: 'low',
      repoFullName: input.repoFullName,
      conn: {
        githubConnected: Boolean(input.repoFullName),
        cursorConnected: false,
        llmConnected: false,
      },
    });
  }

  const repoLine = input.repoFullName ? `Repository: \`${input.repoFullName}\`` : 'Repository: not linked';
  return [
    '> No AI provider connected — this is a deterministic fallback, not a model answer.',
    '',
    'To get real answers from Founder Brain, connect an LLM in **Settings \u2192 AI Stack**:',
    '- GLM 5.2 (cheapest coding model, promo eligible)',
    '- Claude / OpenAI / Gemini / DeepSeek / OpenRouter (bring your own API key)',
    '- Local Ollama (free, offline \u2014 run on your machine)',
    '',
    'Then pick the model in the dropdown above and ask again.',
    '',
    `**Your question:** ${prompt.trim().slice(0, 400)}`,
    '',
    `**Live context I do have:** ${input.projectName} \u00b7 ${repoLine} \u00b7 ${input.commits.length} recent commit(s) \u00b7 launch readiness ${input.launchReadiness}%`,
  ].join('\n');
}
