/**
 * Founder Update Pipeline — project context → trader/developer copy → feed & X.
 * Not commit summaries; business impact for traders, scouts, and investors.
 */

import { fitXShareTextWithFooter } from './share';

function parseLegacySocialDraft(text: string): { headline: string; body: string; xHook: string } {
  const raw = text.trim();
  const headline =
    raw.match(/^HEADLINE:\s*(.+)$/im)?.[1]?.trim() ??
    raw.split('\n').find((l) => l.trim())?.trim() ??
    'Founder update';
  const bodyMatch = raw.match(/^BODY:\s*([\s\S]*?)(?=^X_HOOK:|$)/im);
  const body =
    bodyMatch?.[1]?.trim() ??
    raw.replace(/^HEADLINE:.*$/im, '').replace(/^X_HOOK:.*$/im, '').trim();
  const xHook = raw.match(/^X_HOOK:\s*(.+)$/im)?.[1]?.trim() ?? headline;
  return {
    headline: headline.slice(0, 200),
    body: body.slice(0, 6000),
    xHook: xHook.slice(0, 280),
  };
}

export type FounderUpdateImpact = 'LOW' | 'MEDIUM' | 'HIGH';

export type FounderUpdateContextInput = {
  projectDisplayName: string;
  projectTicker?: string | null;
  projectDescription?: string | null;
  currentGoal?: string | null;
  suggestedNext?: string | null;
  launchReadiness?: number;
  progressPercent?: number;
  roadmapLines?: string[];
  commitsLast30?: string;
  commitsLast24h?: string;
  openTasks?: string[];
  recentDeployments?: string[];
  founderNotes?: string[];
  communityActivity?: string[];
  ddollarActivity?: string[];
  recentPullRequests?: string[];
  missionControlMemory?: string;
  accountBlock?: string;
  infraBlock?: string;
  achievementSeed?: { title: string; detail: string; kind?: string };
};

export type FounderUpdateParsed = {
  headline: string;
  whatShipped: string;
  whyItMatters: string;
  whatUsersNotice: string;
  whatsNext: string;
  developerSummary: string;
  traderSummary: string;
  tweetVersion: string;
  feedVersion: string;
  impactLevel: FounderUpdateImpact;
  launchReadinessDelta: number;
};

export function formatFounderUpdateContextBlock(input: FounderUpdateContextInput): string {
  const sections: string[] = [
    '=== PROJECT CONTEXT LAYER (read fully before writing) ===',
    `Project: ${input.projectDisplayName}`,
    input.projectTicker ? `Ticker: ${input.projectTicker}` : '',
    input.projectDescription?.trim()
      ? `Project description:\n${input.projectDescription.trim().slice(0, 2000)}`
      : '',
    input.currentGoal?.trim() ? `Current goal: ${input.currentGoal.trim()}` : '',
    input.suggestedNext?.trim() ? `Suggested next step: ${input.suggestedNext.trim()}` : '',
    input.launchReadiness != null ? `Launch readiness: ${input.launchReadiness}%` : '',
    input.progressPercent != null ? `Build progress: ${input.progressPercent}%` : '',
    input.roadmapLines?.length
      ? ['Roadmap:', ...input.roadmapLines.map((l) => `- ${l}`)].join('\n')
      : '',
    '',
    '=== LAST 30 COMMITS (themes + evidence — do not list every SHA) ===',
    input.commitsLast30?.trim() || '_No commits on record._',
    '',
    '=== LAST 24 HOURS (primary “today” evidence) ===',
    input.commitsLast24h?.trim() || '_No commits in last 24h._',
    input.openTasks?.length
      ? ['', '=== Open tasks (Mission Control) ===', ...input.openTasks.map((t) => `- ${t}`)].join('\n')
      : '',
    input.recentDeployments?.length
      ? ['', '=== Recent deployments ===', ...input.recentDeployments.map((d) => `- ${d}`)].join('\n')
      : '',
    input.founderNotes?.length
      ? ['', '=== Recent founder notes / feed posts ===', ...input.founderNotes.map((n) => `- ${n}`)].join('\n')
      : '',
    input.communityActivity?.length
      ? ['', '=== Recent community activity ===', ...input.communityActivity.map((c) => `- ${c}`)].join('\n')
      : '',
    input.ddollarActivity?.length
      ? ['', '=== Recent DDollar / paper wallet activity ===', ...input.ddollarActivity.map((d) => `- ${d}`)].join('\n')
      : '',
    input.recentPullRequests?.length
      ? ['', '=== Recent GitHub pull requests ===', ...input.recentPullRequests.map((p) => `- ${p}`)].join('\n')
      : '',
    input.achievementSeed
      ? [
          '',
          '=== ACHIEVEMENT TO EXPLAIN (focus the update on this milestone) ===',
          `Kind: ${input.achievementSeed.kind ?? 'activity'}`,
          `Title: ${input.achievementSeed.title}`,
          `Detail: ${input.achievementSeed.detail}`,
          'Explain what was actually shipped for this achievement — not a commit count headline.',
        ].join('\n')
      : '',
    input.missionControlMemory?.trim()
      ? ['', '=== Mission Control memory ===', input.missionControlMemory.trim()].join('\n')
      : '',
    input.accountBlock?.trim() ? ['', input.accountBlock.trim()].join('\n') : '',
    input.infraBlock?.trim() ? ['', input.infraBlock.trim()].join('\n') : '',
  ];
  return sections.filter(Boolean).join('\n');
}

const SHARED_RULES = [
  'Do NOT write commit summaries or “N commits pushed” headlines unless that count is the only verifiable fact and you immediately translate it into product impact.',
  'Explain what was actually shipped, why it matters, what users will notice, and what milestone it unlocks.',
  'Assume the reader cannot code. Use simple language.',
  'Only describe work supported by the context sections — never invent outages, hacks, or features.',
  'If PLATFORM CLOSING appears in the user message, weave its spirit into the closing (do not paste verbatim unless it fits).',
].join('\n');

export function buildDeepSeekFounderUpdateSystemPrompt(options?: {
  forcedLlm?: string;
}): string {
  const llm =
    options?.forcedLlm === 'DEEPSEEK'
      ? 'You are DeepSeek acting as the Doxxed Crypto Founder Update Writer.'
      : 'You are the Doxxed Crypto Founder Update Writer.';
  return [
    llm,
    'Audience: traders, investors, scouts, and community members.',
    SHARED_RULES,
    'Focus on: product progress, user benefits, milestone completion, launch readiness, demand validation, competitive advantages.',
    '',
    'Return exactly these labeled blocks (no markdown code fences):',
    'HEADLINE: (one punchy line, max 120 chars, trader-friendly — never “X commits pushed”)',
    'WHAT_SHIPPED_TODAY: (2–4 sentences — what product capability moved forward)',
    'WHY_IT_MATTERS: (bullet list with • — business impact)',
    'WHAT_USERS_WILL_NOTICE: (1–3 sentences)',
    'WHATS_NEXT: (1–2 sentences — next milestone)',
    'DEVELOPER_SUMMARY: (technical detail for founders — files/features/PRs if known)',
    'TRADER_SUMMARY: (3–5 short sentences — zero jargon)',
    'TWEET_VERSION: (under 220 chars — hook + impact, no hashtag spam)',
    'FEED_VERSION: (3–5 short paragraphs for the build feed)',
    'IMPACT: LOW or MEDIUM or HIGH',
    'LAUNCH_READINESS_DELTA: integer 0–15 (estimated % bump toward launch)',
  ].join('\n');
}

export function buildCursorFounderUpdateSystemPrompt(): string {
  return [
    'You are the Technical Founder Update Writer reading a GitHub repository and Mission Control context.',
    'Your job: read commits, changed-file themes, pull requests, project goal, and roadmap — then determine what feature was built, why, what problem it solves, and how close the project is to launch.',
    'Rate whether today’s work improves: trust, growth, trading, onboarding, or infrastructure (mention only what evidence supports).',
    SHARED_RULES,
    '',
    'Return exactly:',
    'HEADLINE: (trader-friendly, max 120 chars)',
    'WHAT_SHIPPED_TODAY:',
    'WHY_IT_MATTERS: (bullets with •)',
    'WHAT_USERS_WILL_NOTICE:',
    'WHATS_NEXT:',
    'DEVELOPER_SUMMARY: (detailed — features, modules, PR numbers, infra)',
    'TRADER_SUMMARY: (simple business explanation)',
    'TWEET_VERSION: (under 220 chars)',
    'FEED_VERSION: (feed post with light technical footnote in DEVELOPER_SUMMARY only)',
    'IMPACT: LOW or MEDIUM or HIGH',
    'LAUNCH_READINESS_DELTA: integer 0–15',
  ].join('\n');
}

/** @deprecated Use buildDeepSeekFounderUpdateSystemPrompt / buildCursorFounderUpdateSystemPrompt */
export function buildFounderUpdateSystemPrompt(
  codeAgent?: string | null,
  options?: { forcedLlm?: 'DEEPSEEK' | 'OPENAI' | 'ANTHROPIC' | 'GEMINI' | 'OPENROUTER' | 'PHALA' },
): string {
  if (codeAgent === 'CURSOR' || codeAgent === 'OPENHANDS') {
    return buildCursorFounderUpdateSystemPrompt();
  }
  return buildDeepSeekFounderUpdateSystemPrompt({ forcedLlm: options?.forcedLlm });
}

function extractBlock(raw: string, label: string): string {
  const re = new RegExp(`^${label}:\\s*([\\s\\S]*?)(?=^[A-Z][A-Z0-9_]*:|$)`, 'im');
  return raw.match(re)?.[1]?.trim() ?? '';
}

function parseImpact(raw: string): FounderUpdateImpact {
  const v = raw.trim().toUpperCase();
  if (v === 'HIGH') return 'HIGH';
  if (v === 'LOW') return 'LOW';
  return 'MEDIUM';
}

function parseDelta(raw: string): number {
  const m = raw.match(/([+-]?\d+)/);
  if (!m) return 5;
  const n = parseInt(m[1], 10);
  if (Number.isNaN(n)) return 5;
  return Math.min(15, Math.max(0, Math.abs(n)));
}

export function parseFounderUpdateLlmResponse(text: string): FounderUpdateParsed {
  const raw = text.trim();
  const headline = extractBlock(raw, 'HEADLINE') || 'Founder update';
  const whatShipped = extractBlock(raw, 'WHAT_SHIPPED_TODAY');
  const whyItMatters = extractBlock(raw, 'WHY_IT_MATTERS');
  const whatUsersNotice = extractBlock(raw, 'WHAT_USERS_WILL_NOTICE');
  const whatsNext = extractBlock(raw, 'WHATS_NEXT');
  const developerSummary = extractBlock(raw, 'DEVELOPER_SUMMARY');
  const traderSummary = extractBlock(raw, 'TRADER_SUMMARY');
  const tweetVersion = extractBlock(raw, 'TWEET_VERSION');
  const feedVersion = extractBlock(raw, 'FEED_VERSION');
  const impactLevel = parseImpact(extractBlock(raw, 'IMPACT') || 'MEDIUM');
  const launchReadinessDelta = parseDelta(extractBlock(raw, 'LAUNCH_READINESS_DELTA') || '5');

  if (whatShipped || traderSummary || feedVersion) {
    return {
      headline: headline.slice(0, 200),
      whatShipped: whatShipped.slice(0, 2000),
      whyItMatters: whyItMatters.slice(0, 2000),
      whatUsersNotice: whatUsersNotice.slice(0, 1200),
      whatsNext: whatsNext.slice(0, 800),
      developerSummary: developerSummary.slice(0, 4000),
      traderSummary: traderSummary.slice(0, 2000),
      tweetVersion: (tweetVersion || headline).slice(0, 280),
      feedVersion: feedVersion.slice(0, 6000),
      impactLevel,
      launchReadinessDelta,
    };
  }

  const legacy = parseLegacySocialDraft(raw);
  return {
    headline: legacy.headline,
    whatShipped: legacy.body.split('\n\n')[0]?.trim() ?? legacy.body.slice(0, 500),
    whyItMatters: '',
    whatUsersNotice: '',
    whatsNext: '',
    developerSummary: legacy.body,
    traderSummary: legacy.body.slice(0, 800),
    tweetVersion: legacy.xHook,
    feedVersion: legacy.body,
    impactLevel: 'MEDIUM',
    launchReadinessDelta: 5,
  };
}

export function composeFounderUpdateFeedBody(
  parsed: FounderUpdateParsed,
  platformClosing?: string | null,
): string {
  const blocks = [
    parsed.feedVersion.trim() ||
      [
        parsed.whatShipped.trim(),
        parsed.whyItMatters.trim() ? `**Why it matters**\n${parsed.whyItMatters.trim()}` : '',
        parsed.whatUsersNotice.trim()
          ? `**What you'll notice**\n${parsed.whatUsersNotice.trim()}`
          : '',
        parsed.whatsNext.trim() ? `**What's next**\n${parsed.whatsNext.trim()}` : '',
      ]
        .filter(Boolean)
        .join('\n\n'),
    parsed.launchReadinessDelta > 0
      ? `\n**Launch readiness:** +${parsed.launchReadinessDelta}% · **Impact:** ${parsed.impactLevel}`
      : '',
    platformClosing?.trim() ? `\n---\n${platformClosing.trim()}` : '',
  ];
  return blocks.filter(Boolean).join('').slice(0, 6000);
}

export function buildFounderUpdateXText(input: {
  parsed: FounderUpdateParsed;
  projectName?: string;
  customFooter?: string | null;
}): string {
  const prefix = input.projectName ? `${input.projectName}: ` : '';
  const core =
    input.parsed.tweetVersion.trim() ||
    `${input.parsed.headline}\n\n${input.parsed.traderSummary.split('\n')[0] ?? ''}`.trim();
  const body = `${prefix}${core}`.trim();
  return fitXShareTextWithFooter(body, 280, input.customFooter);
}

export function pickFounderUpdateDisplayBody(
  parsed: FounderUpdateParsed,
  audience: 'trader' | 'developer',
): string {
  if (audience === 'developer' && parsed.developerSummary.trim()) {
    return parsed.developerSummary.trim();
  }
  if (parsed.traderSummary.trim()) return parsed.traderSummary.trim();
  return composeFounderUpdateFeedBody(parsed).replace(/\n---[\s\S]*$/, '').trim();
}

/** Map rule-based fallback into pipeline shape for API + UI. */
export function founderUpdateFromLegacyFallback(input: {
  headline: string;
  body: string;
  xHook: string;
}): FounderUpdateParsed {
  return {
    headline: input.headline,
    whatShipped: input.body.split('\n\n')[0]?.trim() ?? input.body.slice(0, 400),
    whyItMatters: '',
    whatUsersNotice: '',
    whatsNext: '',
    developerSummary: input.body,
    traderSummary: input.body.slice(0, 1200),
    tweetVersion: input.xHook,
    feedVersion: input.body,
    impactLevel: 'MEDIUM',
    launchReadinessDelta: 5,
  };
}
