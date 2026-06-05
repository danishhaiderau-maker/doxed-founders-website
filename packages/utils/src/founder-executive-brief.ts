import type { FounderQueueItem } from './founder-queue';
import type { MissionIntelligence } from './founder-brain-context';

/** Platform market signals for Chief-of-Staff mode. */
export type MarketIntelligenceSnapshot = {
  openScoutMarkets: number;
  expiringScoutQuestion?: string | null;
  scoutStakes24h: number;
  ddollarBalance?: number | null;
  paperTrades24h: number;
  paperVolume24hUsd: number;
  openPredictionMarkets: number;
  hotMarketQuestion?: string | null;
};

export type ExecutiveBriefInput = {
  founderFirstName: string;
  projectName: string;
  sinceYesterday: {
    commits: number;
    deploys: number;
    highlights: string[];
    predictionMarketsCreated: number;
    scoutStakes24h: number;
  };
  missionIntelligence?: MissionIntelligence | null;
  queueItems: FounderQueueItem[];
  market?: MarketIntelligenceSnapshot | null;
  progressPercent: number;
  suggestedNext: string;
  openTaskTitles: string[];
};

/** Urgency 0–100 — higher = act sooner. */
export function computeQueueUrgencyScore(item: FounderQueueItem, now = Date.now()): number {
  let score = 40;
  switch (item.kind) {
    case 'DEPLOY_CHECK':
      score = 100;
      break;
    case 'AGENT_REVIEW':
      score = 88;
      break;
    case 'REVIEW_PR':
      score = 62;
      if (item.createdAt) {
        const ageDays = (now - new Date(item.createdAt).getTime()) / 86400000;
        if (ageDays >= 1) score += Math.min(38, Math.floor(ageDays * 10));
      }
      break;
    case 'CONNECT_STACK':
      score = item.priority <= 1 ? 75 : 55;
      break;
    case 'SCOUT_ACTION':
      score = 72;
      break;
    case 'PUBLISH_UPDATE':
      score = 58;
      break;
    case 'RUN_BUILD':
      score = item.detail?.startsWith('Blocked:') ? 80 : 45;
      break;
    case 'SYNC_GITHUB':
      score = 50;
      break;
    case 'MISSION_EDIT':
      score = 42;
      break;
    default:
      score = 40;
  }
  return Math.min(100, Math.max(1, score));
}

/** Impact 0–100 — higher = more product/platform effect. */
export function computeQueueImpactScore(item: FounderQueueItem): number {
  switch (item.kind) {
    case 'PUBLISH_UPDATE':
      return 85;
    case 'REVIEW_PR':
      return 78;
    case 'DEPLOY_CHECK':
      return 82;
    case 'RUN_BUILD':
      return 70;
    case 'AGENT_REVIEW':
      return 65;
    case 'SCOUT_ACTION':
      return 68;
    case 'CONNECT_STACK':
      return 60;
    case 'SYNC_GITHUB':
      return 55;
    default:
      return 45;
  }
}

export function enrichFounderQueueItem(item: FounderQueueItem, now = Date.now()): FounderQueueItem {
  const urgencyScore = computeQueueUrgencyScore(item, now);
  const impactScore = computeQueueImpactScore(item);
  return { ...item, urgencyScore, impactScore };
}

export function enrichFounderQueueItems(items: FounderQueueItem[], now = Date.now()): FounderQueueItem[] {
  return items.map((item) => enrichFounderQueueItem(item, now));
}

/** Titles that are user/copilot chatter — not real product highlights. */
export function isFounderEventBriefNoise(title: string): boolean {
  const t = title.trim();
  if (!t) return true;
  if (/^what('s| is| am i| are we| should i| broke)/i.test(t)) return true;
  if (/^(status|progress|continue|resume|run platform)/i.test(t)) return true;
  if (/^cursor:/i.test(t)) return true;
  if (/^chore\(founder-os\):\s*sync/i.test(t)) return true;
  return false;
}

/** Proactive one-liners for chat / brief (Chief of Staff). */
export function buildProactiveNudges(
  items: FounderQueueItem[],
  mission?: MissionIntelligence | null,
): string[] {
  const nudges: string[] = [];
  const scored = enrichFounderQueueItems(items).sort(
    (a, b) => (b.urgencyScore ?? 0) - (a.urgencyScore ?? 0),
  );

  const seenPublish = new Set<string>();
  for (const item of scored.slice(0, 6)) {
    if (item.kind === 'REVIEW_PR' && (item.urgencyScore ?? 0) >= 72) {
      const days =
        item.createdAt != null
          ? Math.max(1, Math.floor((Date.now() - new Date(item.createdAt).getTime()) / 86400000))
          : null;
      nudges.push(
        days != null && days >= 2
          ? `${item.title} has been waiting ${days} day(s). Approve or request changes?`
          : `${item.title} — review when you have a minute.`,
      );
    } else if (item.kind === 'PUBLISH_UPDATE') {
      const key = item.title.trim().toLowerCase();
      if (seenPublish.has(key)) continue;
      seenPublish.add(key);
      nudges.push(`Ship ready: ${item.title}. Publish to Feed and X?`);
    } else if (item.kind === 'DEPLOY_CHECK') {
      nudges.push('Deployment health needs attention — run platform autopilot sync.');
    } else if (item.kind === 'SCOUT_ACTION') {
      nudges.push(`Scout market closing soon — ${item.title.replace(/^Scout market closing soon: /, '')}`);
    } else if (item.kind === 'AGENT_REVIEW') {
      nudges.push(`Builder run in progress — ${item.detail ?? 'review steps in chat'}.`);
    }
  }

  if (nudges.length === 0 && mission?.blocker) {
    nudges.push(`Blocker: ${mission.blocker}`);
  }
  if (nudges.length === 0 && mission?.recommendedNextStep) {
    nudges.push(`Suggested: ${mission.recommendedNextStep}`);
  }

  return nudges.slice(0, 4);
}

export function formatMarketIntelligenceForPrompt(market: MarketIntelligenceSnapshot): string {
  const lines = [
    '## Platform market intelligence',
    market.openScoutMarkets > 0
      ? `- Open scout / prediction markets: ${market.openScoutMarkets}`
      : '- No open scout markets on your project',
    market.expiringScoutQuestion
      ? `- Scout closing soon: ${market.expiringScoutQuestion.slice(0, 120)}`
      : '',
    market.hotMarketQuestion
      ? `- Hot market question: ${market.hotMarketQuestion.slice(0, 120)}`
      : '',
    market.scoutStakes24h > 0 ? `- Scout stakes (24h): ${market.scoutStakes24h}` : '',
    market.ddollarBalance != null
      ? `- DDollar paper balance: ${Math.round(market.ddollarBalance)}`
      : '',
    market.paperTrades24h > 0
      ? `- Paper trades (24h): ${market.paperTrades24h} · ~$${Math.round(market.paperVolume24hUsd)} volume`
      : '',
    '- Use scout votes, DDollar flow, and prediction markets when recommending product priorities.',
  ];
  return lines.filter(Boolean).join('\n');
}

export function formatMarketIntelligenceBriefLine(market: MarketIntelligenceSnapshot): string | null {
  const parts: string[] = [];
  if (market.paperTrades24h > 0) {
    parts.push(`DDollar activity: ${market.paperTrades24h} trade(s) (~$${Math.round(market.paperVolume24hUsd)})`);
  }
  if (market.scoutStakes24h > 0) {
    parts.push(`${market.scoutStakes24h} scout stake(s)`);
  }
  if (market.openScoutMarkets > 0) {
    parts.push(`${market.openScoutMarkets} open market(s)`);
  }
  return parts.length > 0 ? parts.join(' · ') : null;
}

/** Full morning brief — shown on Mission Control open (Chief of Staff). */
export function buildExecutiveBrief(input: ExecutiveBriefInput): string {
  const name = input.founderFirstName.trim() || 'Founder';
  const hour = new Date().getHours();
  const greeting =
    hour < 12 ? `Good morning, ${name}.` : hour < 17 ? `Good afternoon, ${name}.` : `Good evening, ${name}.`;

  const sinceLines: string[] = [];
  if (input.sinceYesterday.commits > 0) {
    sinceLines.push(`• ${input.sinceYesterday.commits} commit(s) shipped`);
  }
  if (input.sinceYesterday.deploys > 0) {
    sinceLines.push(`• ${input.sinceYesterday.deploys} deployment(s) successful`);
  }
  for (const h of input.sinceYesterday.highlights.slice(0, 4)) {
    sinceLines.push(`• ${h}`);
  }
  if (input.sinceYesterday.predictionMarketsCreated > 0) {
    sinceLines.push(`• ${input.sinceYesterday.predictionMarketsCreated} prediction market(s) created`);
  }
  if (input.sinceYesterday.scoutStakes24h > 0) {
    sinceLines.push(`• ${input.sinceYesterday.scoutStakes24h} scout stake(s) in the last day`);
  }
  const marketLine = input.market ? formatMarketIntelligenceBriefLine(input.market) : null;
  if (marketLine) {
    sinceLines.push(`• ${marketLine}`);
  }
  if (sinceLines.length === 0) {
    sinceLines.push('• Quiet day — good time to ship the next initiative.');
  }

  const nudges = buildProactiveNudges(input.queueItems, input.missionIntelligence);
  const attention =
    nudges.length > 0
      ? nudges.map((n) => `• ${n}`).join('\n')
      : input.missionIntelligence?.blocker
        ? `• ${input.missionIntelligence.blocker}`
        : '• Inbox clear — ask me to research, build, or publish.';

  const shipped =
    input.missionIntelligence?.shippedRecently?.slice(0, 3).map((s) => `• ${s}`) ?? [];
  const initiative = input.missionIntelligence?.currentInitiative ?? input.projectName;
  const nextStep =
    input.missionIntelligence?.recommendedNextStep?.trim() ||
    input.suggestedNext ||
    input.openTaskTitles[0] ||
    'Sync GitHub and pick the top initiative.';

  return [
    greeting,
    '',
    '**Since yesterday:**',
    sinceLines.join('\n'),
    '',
    '**Current initiative:**',
    initiative,
    shipped.length > 0 ? ['', '**Recently shipped:**', shipped.join('\n')].join('\n') : '',
    '',
    '**Attention needed:**',
    attention,
    '',
    '**Recommended next step:**',
    nextStep,
    '',
    `Progress: ${input.progressPercent}% · Reply in chat or use the CEO inbox on the right.`,
  ]
    .filter(Boolean)
    .join('\n');
}
