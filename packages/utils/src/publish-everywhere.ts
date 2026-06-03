/** Build copy for multi-destination publish (no LLM). */

import { DDOLLAR_CURRENCY_NAME, formatDdollar } from './ddollar';
import type { ControlPlaneReadiness } from './control-plane';

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

/** System prompt for Social Hub — LLM must return HEADLINE / BODY / X_HOOK blocks. */
export function buildSocialDraftSystemPrompt(codeAgent?: string | null): string {
  const lens =
    codeAgent === 'CURSOR'
      ? [
          'You are drafting as the founder’s Cursor coding agent — you shipped the commits in context.',
          'You must use ALL structured sections below: founder account, hybrid control plane (include every ✗ fail line verbatim in meaning),',
          'last commit detail, day-by-day commit breakdown, highlights, and missing-link narrative.',
          'Explain how recent commits + infra fixes close gaps in the stack (GitHub → memory → deploy → publish → community).',
        ].join(' ')
      : codeAgent === 'OPENHANDS'
        ? 'You interpret the founder’s latest repo and builder activity (as their OpenHands coding agent). Use every context section provided.'
        : 'You are an elite crypto founder marketing writer for build-in-public. Use every context section provided.';

  return [
    lens,
    'Read the technical context and write for non-developers.',
    'Explain WHAT shipped, WHY it matters, WHO benefits, and how it fixes “missing links” (trust, sync, deploy, publish) — like a sharp build-in-public story, not a raw changelog.',
    'Include concrete proof: cite at least one commit subject/SHA, mention launch readiness or Ddollar balance if provided, and reference control-plane ✗ items as optional Stack connections (not as “the product is down”).',
    'Use simple English. No jargon unless you immediately explain it.',
    'Be honest — only claim what the context supports.',
    'BODY should weave: (1) headline win, (2) day-by-day shipping rhythm, (3) hybrid plane / account snapshot, (4) what gap was closed, (5) what’s next.',
    '',
    'Return exactly this format (no extra sections):',
    'HEADLINE: (one punchy line, max 120 chars)',
    'BODY: (3–5 short paragraphs covering commits, infra/account context, missing links fixed, next step)',
    'X_HOOK: (one tweet-sized hook under 220 chars, exciting, no hashtag spam)',
  ].join('\n');
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
