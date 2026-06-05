import type { MarketIntelligenceSnapshot } from './founder-executive-brief.js';

/** Live proactive message from Founder Brain (Chief of Staff mode). */
export type ChiefOfStaffNudge = {
  id: string;
  kind:
    | 'deploy_publish'
    | 'publish_ready'
    | 'scout_expiry'
    | 'market_surge'
    | 'stale_pr'
    | 'pr_merged'
    | 'builder_complete';
  urgencyScore: number;
  message: string;
  prompt?: string;
  href?: string;
  createdAt: string;
};

export type ContinuousNudgeInput = {
  now?: number;
  recentDeploys: {
    id: string;
    title: string;
    at: string;
    suggestionId?: string;
    hasPendingPublish?: boolean;
  }[];
  pendingPublishes: { id: string; headline: string }[];
  openPrs: { number: number; title: string; createdAt?: string }[];
  recentMerges: { number: number; title: string; at: string }[];
  expiringScouts: { id: string; question: string; resolvesAt: string }[];
  market: MarketIntelligenceSnapshot | null;
  tradesYesterday?: number;
};

function hoursUntil(iso: string, now: number): number {
  return (new Date(iso).getTime() - now) / 3600000;
}

function dedupePendingPublishes(
  items: ContinuousNudgeInput['pendingPublishes'],
): ContinuousNudgeInput['pendingPublishes'] {
  const seen = new Set<string>();
  const out: ContinuousNudgeInput['pendingPublishes'] = [];
  for (const item of items) {
    const key = item.headline.trim().toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}

/** Event-driven nudges — poll while Mission Control is open. */
export function buildContinuousChiefOfStaffNudges(input: ContinuousNudgeInput): ChiefOfStaffNudge[] {
  const now = input.now ?? Date.now();
  const at = new Date(now).toISOString();
  const nudges: ChiefOfStaffNudge[] = [];

  for (const d of input.recentDeploys) {
    if (!d.hasPendingPublish && !d.suggestionId) continue;
    const ageH = (now - new Date(d.at).getTime()) / 3600000;
    if (ageH > 72) continue;
    nudges.push({
      id: `deploy-publish-${d.suggestionId ?? d.id}`,
      kind: 'deploy_publish',
      urgencyScore: ageH < 6 ? 88 : ageH < 24 ? 72 : 58,
      message: `Deploy completed: **${d.title.slice(0, 80)}**. Would you like me to publish an update to Feed and X?`,
      prompt: `Publish the deploy update: ${d.title.slice(0, 120)}`,
      createdAt: at,
    });
  }

  for (const p of dedupePendingPublishes(input.pendingPublishes).slice(0, 3)) {
    const headlineKey = p.headline.trim().toLowerCase().slice(0, 80);
    nudges.push({
      id: `publish-ready-${headlineKey}`,
      kind: 'publish_ready',
      urgencyScore: 70,
      message: `Publish draft ready: **${p.headline.slice(0, 72)}**. Ship to Feed, X, and community?`,
      prompt: `Publish update: ${p.headline.slice(0, 120)}`,
      createdAt: at,
    });
  }

  for (const m of input.recentMerges.slice(0, 2)) {
    const ageH = (now - new Date(m.at).getTime()) / 3600000;
    if (ageH > 48) continue;
    nudges.push({
      id: `pr-merged-${m.number}`,
      kind: 'pr_merged',
      urgencyScore: ageH < 12 ? 75 : 60,
      message: `PR #${m.number} merged: **${m.title.slice(0, 72)}**. Publish a ship note or run deploy sync?`,
      prompt: `We merged PR #${m.number}. Draft a public ship update.`,
      createdAt: at,
    });
  }

  for (const pr of input.openPrs) {
    if (!pr.createdAt) continue;
    const ageDays = (now - new Date(pr.createdAt).getTime()) / 86400000;
    if (ageDays < 2) continue;
    nudges.push({
      id: `stale-pr-${pr.number}`,
      kind: 'stale_pr',
      urgencyScore: Math.min(95, 65 + Math.floor(ageDays * 8)),
      message: `PR #${pr.number} has been waiting **${Math.floor(ageDays)} day(s)**. It may block your current initiative — review or merge?`,
      prompt: `Review PR #${pr.number}: ${pr.title.slice(0, 100)}`,
      createdAt: at,
    });
  }

  for (const s of input.expiringScouts) {
    const h = hoursUntil(s.resolvesAt, now);
    if (h > 48 || h < 0) continue;
    const label = h <= 6 ? `${Math.max(1, Math.round(h))} hour(s)` : `${Math.round(h)} hours`;
    nudges.push({
      id: `scout-expiry-${s.id}`,
      kind: 'scout_expiry',
      urgencyScore: h <= 6 ? 92 : h <= 24 ? 78 : 65,
      message: `Scout vote expires in **${label}**: ${s.question.slice(0, 90)}`,
      prompt: `Review scout market before it closes: ${s.question.slice(0, 120)}`,
      href: '/predict',
      createdAt: at,
    });
  }

  const market = input.market;
  if (market) {
    const prev = input.tradesYesterday ?? 0;
    const cur = market.paperTrades24h;
    if (cur >= 3 && (prev === 0 || cur >= prev * 1.5)) {
      const pct = prev > 0 ? Math.round(((cur - prev) / prev) * 100) : 100;
      nudges.push({
        id: `market-surge-${new Date(now).toISOString().slice(0, 10)}`,
        kind: 'market_surge',
        urgencyScore: 55,
        message:
          prev > 0
            ? `DDollar activity is up **${pct}%** (${cur} trades in 24h). Consider prioritizing features traders are engaging with.`
            : `**${cur} paper trade(s)** in the last day — platform demand is picking up.`,
        prompt: 'What product work should we prioritize based on recent DDollar and scout activity?',
        createdAt: at,
      });
    }

    if (market.scoutStakes24h >= 5) {
      nudges.push({
        id: `scout-stakes-${new Date(now).toISOString().slice(0, 10)}`,
        kind: 'market_surge',
        urgencyScore: 52,
        message: `**${market.scoutStakes24h} scout stake(s)** in the last 24h — conviction is building on your markets.`,
        prompt: 'Summarize scout voting trends and what founders should ship next.',
        createdAt: at,
      });
    }
  }

  return nudges.sort((a, b) => b.urgencyScore - a.urgencyScore).slice(0, 8);
}

export function formatChiefOfStaffNudgeForChat(nudge: ChiefOfStaffNudge): string {
  const lines = [nudge.message];
  if (nudge.prompt) {
    lines.push('', `_Reply "yes" or ask me to: ${nudge.prompt.slice(0, 100)}_`);
  }
  return lines.join('\n');
}
