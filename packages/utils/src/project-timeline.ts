import { groupCommitsByInitiative, type CommitSignal } from './commit-intelligence.js';

/** Unified founder narrative entry (chronological, not raw commit spam). */
export type ProjectTimelineEntry = {
  id: string;
  at: string;
  kind: 'theme' | 'merge' | 'deploy' | 'publish' | 'ship' | 'agent' | 'update' | 'decision' | 'scout' | 'trade' | 'listing';
  title: string;
  detail?: string;
  commitCount?: number;
};

const TIMELINE_FOUNDER_EVENT_TYPES = new Set([
  'GITHUB_PR_MERGED',
  'DEPLOY_SUCCESS',
  'BUILD_PUBLISHED',
  'AGENT_RUN_COMPLETE',
  'CURSOR_BUILD_SESSION',
  'SCOUT_MARKET_STAKE',
  'RAISE_ALLOCATION',
]);

export function isTimelineFounderEventType(type: string): boolean {
  return TIMELINE_FOUNDER_EVENT_TYPES.has(type);
}

export function buildProjectTimeline(input: {
  events: { id: string; type: string; title: string; createdAt: Date | string; payload?: unknown }[];
  commits: CommitSignal[];
  buildPosts: { id: string; headline: string; publishedAt: Date | string }[];
  founderUpdates: { id: string; headline: string; publishedAt: Date | string }[];
  decisions?: { id: string; decision: string; date: string }[];
  paperTrades?: { id: string; side: string; ticker: string; totalUsd: number; at: string }[];
  listings?: { id: string; title: string; at: string }[];
}): ProjectTimelineEntry[] {
  const entries: ProjectTimelineEntry[] = [];

  for (const e of input.events) {
    if (!isTimelineFounderEventType(e.type)) continue;
    const at =
      typeof e.createdAt === 'string' ? e.createdAt : e.createdAt.toISOString();
    const kind =
      e.type === 'GITHUB_PR_MERGED'
        ? 'merge'
        : e.type === 'DEPLOY_SUCCESS'
          ? 'deploy'
          : e.type === 'BUILD_PUBLISHED'
            ? 'publish'
            : e.type === 'SCOUT_MARKET_STAKE'
              ? 'scout'
              : e.type === 'RAISE_ALLOCATION'
                ? 'listing'
                : e.type === 'AGENT_RUN_COMPLETE' || e.type === 'CURSOR_BUILD_SESSION'
                  ? 'agent'
                  : 'ship';
    entries.push({
      id: `event-${e.id}`,
      at,
      kind,
      title: e.title.slice(0, 160),
      detail: timelineDetailFromPayload(e.payload),
    });
  }

  for (const p of input.buildPosts) {
    const at =
      typeof p.publishedAt === 'string' ? p.publishedAt : p.publishedAt.toISOString();
    entries.push({
      id: `post-${p.id}`,
      at,
      kind: 'publish',
      title: p.headline.slice(0, 160),
      detail: 'Build update published',
    });
  }

  for (const u of input.founderUpdates) {
    const at =
      typeof u.publishedAt === 'string' ? u.publishedAt : u.publishedAt.toISOString();
    entries.push({
      id: `update-${u.id}`,
      at,
      kind: 'update',
      title: u.headline.slice(0, 160),
      detail: 'Founder update',
    });
  }

  for (const d of input.decisions ?? []) {
    entries.push({
      id: `decision-${d.id}`,
      at: d.date,
      kind: 'decision',
      title: d.decision.slice(0, 160),
      detail: 'Founder decision',
    });
  }

  for (const t of input.paperTrades ?? []) {
    entries.push({
      id: `trade-${t.id}`,
      at: t.at,
      kind: 'trade',
      title: `${t.side} $${t.ticker} · $${Math.round(t.totalUsd)}`,
      detail: 'Paper trade',
    });
  }

  for (const l of input.listings ?? []) {
    entries.push({
      id: `listing-${l.id}`,
      at: l.at,
      kind: 'listing',
      title: l.title.slice(0, 160),
      detail: 'Listing / raise activity',
    });
  }

  const themes = groupCommitsByInitiative(input.commits);
  for (const t of themes.filter((x) => x.commitCount >= 3).slice(0, 4)) {
    const sample = t.samples[0];
    const date = input.commits.find((c) => sample && c.message === sample)?.date;
    entries.push({
      id: `theme-${t.key}`,
      at: date ?? new Date().toISOString(),
      kind: 'theme',
      title: `${t.label} (${t.commitCount} commits)`,
      detail: t.samples[0]?.slice(0, 120),
      commitCount: t.commitCount,
    });
  }

  entries.sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());

  const seen = new Set<string>();
  return entries.filter((e) => {
    const key = `${e.kind}:${e.title.slice(0, 48)}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function timelineDetailFromPayload(payload: unknown): string | undefined {
  if (!payload || typeof payload !== 'object') return undefined;
  const p = payload as Record<string, unknown>;
  if (typeof p.summary === 'string') return p.summary.slice(0, 200);
  if (typeof p.provider === 'string') return `via ${p.provider}`;
  return undefined;
}

export function formatProjectTimelineExcerpt(entries: ProjectTimelineEntry[], max = 14): string {
  if (entries.length === 0) return '- No timeline entries in window — sync GitHub and ship';
  return entries
    .slice(0, max)
    .map((e) => {
      const day = e.at.slice(0, 10);
      const suffix = e.commitCount ? ` (${e.commitCount} commits)` : '';
      return `- ${day} · ${e.title}${suffix}${e.detail ? ` — ${e.detail}` : ''}`;
    })
    .join('\n');
}

export function formatTimelineLine(entry: ProjectTimelineEntry): string {
  const day = entry.at.slice(0, 10);
  const suffix = entry.commitCount ? ` (${entry.commitCount} commits)` : '';
  return `${day} — ${entry.title}${suffix}`;
}
