/** Build copy for multi-destination publish (no LLM). */

import { buildFounderUpdateSystemPrompt } from './founder-update-pipeline';
import { DDOLLAR_CURRENCY_NAME, formatDdollar } from './ddollar';
import type { ControlPlaneReadiness } from './control-plane';
import { translateCommitForTraders } from './github-translate';

export type SocialDraftCommit = {
  sha: string;
  message: string;
  date: string;
  author?: string;
};

export type SocialDraftInfraStep = {
  step: string;
  ok: boolean;
  detail: string;
};

export type PublishDestinations = {
  buildFeed: boolean;
  x: boolean;
  community: boolean;
};

export const DEFAULT_PUBLISH_DESTINATIONS: PublishDestinations = {
  buildFeed: true,
  x: true,
  community: true,
};

export function buildFeedPostBody(input: {
  body: string;
  devSummary: string;
  traderSummary: string;
}): string {
  return [
    input.body,
    '',
    '---',
    '**Developer view:**',
    input.devSummary,
    '',
    '**Trader view:**',
    input.traderSummary,
  ].join('\n');
}

function dayKey(isoDate: string): string {
  const d = new Date(isoDate);
  if (Number.isNaN(d.getTime())) return 'unknown';
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
}

/** Group commits newest-first by calendar day for Social Hub context. */
export function formatCommitsByDay(
  commits: SocialDraftCommit[],
  options?: { maxDays?: number; maxPerDay?: number },
): string {
  if (commits.length === 0) return 'No commits in the last 14 days.';
  const maxDays = options?.maxDays ?? 7;
  const maxPerDay = options?.maxPerDay ?? 8;
  const byDay = new Map<string, SocialDraftCommit[]>();
  for (const c of commits) {
    const key = dayKey(c.date);
    const list = byDay.get(key) ?? [];
    list.push(c);
    byDay.set(key, list);
  }
  const lines: string[] = [];
  let days = 0;
  for (const [day, list] of byDay) {
    if (days >= maxDays) break;
    days += 1;
    lines.push(`${day} (${list.length} commit${list.length === 1 ? '' : 's'}):`);
    for (const c of list.slice(0, maxPerDay)) {
      const subject = c.message.split('\n')[0]?.trim() ?? c.message;
      lines.push(`  - ${c.sha.slice(0, 7)} ${subject}`);
      const body = c.message.split('\n').slice(1).join('\n').trim();
      if (body) {
        lines.push(`    ${body.split('\n').slice(0, 4).join(' ').slice(0, 280)}`);
      }
    }
    if (list.length > maxPerDay) {
      lines.push(`  … +${list.length - maxPerDay} more that day`);
    }
  }
  return lines.join('\n');
}

export function formatLastCommitDetail(commit: SocialDraftCommit | null | undefined): string {
  if (!commit) return 'No latest commit on record.';
  const subject = commit.message.split('\n')[0]?.trim() ?? commit.message;
  const body = commit.message.split('\n').slice(1).join('\n').trim();
  const when = new Date(commit.date).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' });
  return [
    `SHA: ${commit.sha}`,
    `When: ${when}`,
    `Subject: ${subject}`,
    body ? `Body:\n${body.slice(0, 1200)}` : '',
  ]
    .filter(Boolean)
    .join('\n');
}

/** Mirror Hybrid control plane infrastructure lines (include ✗ — gaps, not shame). */
export function formatAutopilotInfrastructureBlock(input: {
  steps: SocialDraftInfraStep[];
  controlPlane?: ControlPlaneReadiness;
  siteUrl?: string;
}): string {
  const lines: string[] = ['Hybrid control plane (latest infrastructure check):'];
  if (input.controlPlane) {
    const modeLabel =
      input.controlPlane.mode === 'FULL_STACK' ? 'Full stack' : 'Cursor-first';
    lines.push(`Mode: ${modeLabel}`);
    for (const leg of input.controlPlane.legs) {
      lines.push(
        `${leg.connected ? '✓' : '○'} ${leg.label} — ${leg.subtitle}${leg.provider ? ` (${leg.provider})` : ''}${leg.detail ? `: ${leg.detail}` : ''}`,
      );
    }
    if (input.controlPlane.missingForFullStack.length > 0) {
      lines.push(
        `Stack gaps (connect in AI Stack to close the loop): ${input.controlPlane.missingForFullStack.join(', ')}`,
      );
    }
    lines.push('');
  }
  lines.push('Infrastructure steps:');
  for (const s of input.steps) {
    lines.push(`${s.ok ? '✓' : '✗'} ${s.step}: ${s.detail}`);
  }
  if (input.siteUrl) {
    lines.push(`Live site: ${input.siteUrl}`);
  }
  lines.push(
    'Interpret ✗ lines honestly: production may still run via Railway DATABASE_URL and Git deploys even when Neon/Vercel/Railway tokens are not linked in Stack.',
  );
  return lines.join('\n');
}

export function buildSocialDraftFounderAccountBlock(input: {
  founderName: string;
  projectName?: string;
  journeyStage?: string;
  buildStreakDays?: number;
  reputationScore?: number;
  founderCredits?: number;
  paperCashUsd?: number;
  launchReadiness?: number;
  progressPercent?: number;
  currentGoal?: string;
  userDisplayName?: string;
}): string {
  const wallet =
    input.paperCashUsd != null ? formatDdollar(input.paperCashUsd) : 'Paper wallet not started';
  const credits =
    input.founderCredits != null
      ? `${input.founderCredits.toLocaleString()} ${DDOLLAR_CURRENCY_NAME} credits`
      : 'n/a';
  return [
    'Founder profile (Mission Control / account):',
    `Founder: ${input.founderName}${input.userDisplayName ? ` (signed in as ${input.userDisplayName})` : ''}`,
    input.projectName ? `Project: ${input.projectName}` : '',
    input.journeyStage ? `Journey stage: ${input.journeyStage.replace(/_/g, ' ')}` : '',
    `Account balance: ${wallet}`,
    `Founder credits: ${credits}`,
    input.buildStreakDays != null ? `Build streak: ${input.buildStreakDays} day(s)` : '',
    input.reputationScore != null ? `Reputation: ${input.reputationScore}` : '',
    input.launchReadiness != null ? `Launch readiness: ${input.launchReadiness}%` : '',
    input.progressPercent != null ? `Build progress: ${input.progressPercent}%` : '',
    input.currentGoal ? `Current goal: ${input.currentGoal}` : '',
  ]
    .filter(Boolean)
    .join('\n');
}

/** Heuristic: tie recent commits + stack gaps to “missing link” narrative for the draft. */
export function buildMissingLinkNarrativeHints(input: {
  commits: SocialDraftCommit[];
  missingPlatforms: string[];
}): string {
  const text = input.commits.map((c) => c.message.toLowerCase()).join(' ');
  const hints: string[] = [];
  if (/profile.?lock|claim|dexscreener|founder claim/i.test(text)) {
    hints.push('Founder claim + profile lock closes trust gap between listing page and verified owner.');
  }
  if (/copilot|mission control|inline|cursor|openhands/i.test(text)) {
    hints.push('Mission Control Copilot + code agents link “ask → code → ship” without leaving Founder OS.');
  }
  if (/ddollar|founder credit|wallet|paper trad/i.test(text)) {
    hints.push('Ddollar / paper wallet makes platform stats and raises feel like one in-game economy.');
  }
  if (/hsts|security header|copilot/i.test(text)) {
    hints.push('Security headers and clearer Copilot copy address scanner noise — not a breach.');
  }
  if (/founder.?node|pairing|tray|heartbeat/i.test(text)) {
    hints.push('Founder Node pairing + tray copy fixes “paired but offline” — the desktop app must stay open to sync.');
  }
  if (/autopilot|control plane|neon|vercel|railway|sync/i.test(text)) {
    hints.push('Hybrid control plane + Autopilot tie GitHub commits to memory, deploy checks, and publish paths.');
  }
  if (/social|draft|publish|feed|build.?in.?public/i.test(text)) {
    hints.push('Social Hub drafts turn repo truth into feed/X/community posts builders can trust.');
  }
  if (input.missingPlatforms.length > 0) {
    hints.push(
      `Still to connect in AI Stack (optional API tokens — app can ship via Git + Railway): ${input.missingPlatforms.join(', ')}.`,
    );
  }
  if (hints.length === 0) {
    hints.push(
      'Recent commits advance the full loop: GitHub truth → Founder OS memory → Mission Control → public feed/X.',
    );
  }
  return ['How this work closes missing links (use only what commits + infra support):', ...hints.map((h) => `- ${h}`)].join(
    '\n',
  );
}

export function resolveProjectDisplayForSocial(project?: {
  name?: string | null;
  ticker?: string | null;
} | null): { displayName: string; ticker: string | null } {
  const name = project?.name?.trim();
  const rawTicker = project?.ticker?.trim().replace(/^\$/, '') ?? null;
  const ticker = rawTicker ? `$${rawTicker}` : null;
  if (name && ticker) {
    return { displayName: `${name} (${ticker})`, ticker };
  }
  return { displayName: name ?? ticker ?? 'our project', ticker };
}

/** Plain-English digest of last-24h commits for traders (no LLM). */
export function formatCommitsLast24hForTraders(
  commits: SocialDraftCommit[],
  projectDisplayName: string,
): string {
  if (commits.length === 0) {
    return [
      `**Last 24 hours on GitHub** — no new pushes yet for ${projectDisplayName}.`,
      'If you coded locally, run `git push` so Founder OS and traders see real progress.',
    ].join('\n');
  }
  const lines = [
    `**Last 24 hours** — ${commits.length} commit${commits.length === 1 ? '' : 's'} on GitHub for ${projectDisplayName}:`,
  ];
  for (const c of commits.slice(0, 15)) {
    const subject = c.message.split('\n')[0]?.trim() ?? c.message;
    lines.push(`- \`${c.sha.slice(0, 7)}\` ${subject}`);
    lines.push(`  → ${translateCommitForTraders(c.message)}`);
  }
  if (commits.length > 15) {
    lines.push(`_…and ${commits.length - 15} more commit(s) in the same window._`);
  }
  return lines.join('\n');
}

export function buildFounderUpdateFallback(input: {
  projectDisplayName: string;
  commits24h: SocialDraftCommit[];
  currentGoal?: string;
  suggestedNext?: string;
  launchReadiness?: number;
  buildStreakDays?: number;
  completedTasks?: string[];
  platformClosing?: string;
}): { headline: string; body: string; xHook: string } {
  const digest = formatCommitsLast24hForTraders(input.commits24h, input.projectDisplayName);
  const taskBlock =
    input.completedTasks && input.completedTasks.length > 0
      ? ['', '**Shipped in Mission Control:**', ...input.completedTasks.map((t) => `- ${t}`)]
      : [];

  const headline =
    input.commits24h[0]?.message.split('\n')[0]?.slice(0, 100) ??
    `Building ${input.projectDisplayName} in public`;

  const body = [
    `Here’s what we actually shipped for **${input.projectDisplayName}** in the last 24 hours — in plain English for traders and the community.`,
    '',
    digest,
    ...taskBlock,
    input.launchReadiness != null
      ? `\n**Launch readiness:** ${input.launchReadiness}%${input.buildStreakDays != null ? ` · **Build streak:** ${input.buildStreakDays} day(s)` : ''}`
      : '',
    input.currentGoal ? `\n**Current focus:** ${input.currentGoal}` : '',
    input.suggestedNext ? `\n**Next:** ${input.suggestedNext}` : '',
    input.platformClosing
      ? ['', '---', input.platformClosing.trim()]
      : [],
  ]
    .filter(Boolean)
    .join('\n');

  const xHook =
    input.commits24h.length > 0
      ? `${input.projectDisplayName}: ${input.commits24h[0].message.split('\n')[0]?.slice(0, 160) ?? 'New commits live'}`
      : `${input.projectDisplayName} — shipping in public on Doxxed Crypto`;

  return {
    headline: headline.slice(0, 200),
    body: body.slice(0, 6000),
    xHook: xHook.slice(0, 280),
  };
}

/** System prompt for Social Hub — delegates to Founder Update Pipeline prompts. */
export function buildSocialDraftSystemPrompt(
  codeAgent?: string | null,
  options?: { forcedLlm?: 'DEEPSEEK' | 'OPENAI' | 'ANTHROPIC' | 'GEMINI' | 'OPENROUTER' | 'PHALA' },
): string {
  return buildFounderUpdateSystemPrompt(codeAgent, options);
}

export function parseSocialDraftLlmResponse(text: string): {
  headline: string;
  body: string;
  xHook: string;
} {
  const raw = text.trim();
  const headline =
    raw.match(/^HEADLINE:\s*(.+)$/im)?.[1]?.trim() ??
    raw.split('\n').find((l) => l.trim())?.trim() ??
    'Founder update';
  const bodyMatch = raw.match(/^BODY:\s*([\s\S]*?)(?=^X_HOOK:|$)/im);
  const body =
    bodyMatch?.[1]?.trim() ??
    raw.replace(/^HEADLINE:.*$/im, '').replace(/^X_HOOK:.*$/im, '').trim();
  const xHook =
    raw.match(/^X_HOOK:\s*(.+)$/im)?.[1]?.trim() ??
    headline;

  return {
    headline: headline.slice(0, 200),
    body: body.slice(0, 6000),
    xHook: xHook.slice(0, 280),
  };
}

export function buildXUpdateTweet(input: {
  headline: string;
  traderSummary: string;
  projectName?: string;
}): string {
  const prefix = input.projectName ? `${input.projectName}: ` : '';
  const traderLine = input.traderSummary.split('\n')[0]?.replace(/^✓\s*/, '') ?? input.headline;
  const raw = `${prefix}${input.headline}\n\n${traderLine}\n\n#BuildInPublic`;
  return raw.length <= 280 ? raw : `${prefix}${input.headline}`.slice(0, 276) + '…';
}

export function buildCommunityAnnouncement(input: {
  headline: string;
  body: string;
  traderSummary: string;
}): { title: string; body: string } {
  return {
    title: input.headline,
    body: [input.body, '', '**For traders:**', input.traderSummary].join('\n'),
  };
}

export type PublishChannelResult = {
  buildFeed?: { ok: boolean; buildPostId?: string; error?: string };
  x?: { ok: boolean; tweetUrl?: string; error?: string; skipped?: boolean };
  community?: { ok: boolean; threadId?: string; error?: string; skipped?: boolean };
};
