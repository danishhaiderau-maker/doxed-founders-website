export const POINTS = {
  REGISTER: 50,
  PAPER_TRADE: 10,
  FEED_COMMENT: 5,
  WATCHLIST_ADD: 2,
  /** Scout submits a listing for community vote (logged-in user). */
  LISTING_SUBMIT: 50,
  /** Cast a community vote on a scout listing. */
  LISTING_VOTE: 15,
  /** Scout bonus when admin approves and project goes live — highest reward. */
  LISTING_SCOUT_APPROVED: 1000,
  /** Founder launches a project — reputation points (credits granted separately). */
  FOUNDER_PROJECT_LAUNCH: 500,
  /** Founder build-in-public post. */
  FOUNDER_BUILD_POST: 100,
  /** Founder community thread or announcement. */
  FOUNDER_COMMUNITY_POST: 75,
  /** Founder video update. */
  FOUNDER_VIDEO: 150,
  /** Community member thread on a project. */
  COMMUNITY_THREAD: 25,
  /** Reply on a project community thread. */
  COMMUNITY_COMMENT: 15,
  /** Vote on a founder demand poll. */
  DEMAND_POLL_VOTE: 10,
  /** Allocate paper cash to a simulated raise. */
  RAISE_ALLOCATE: 20,
  /** Follow a founder project. */
  PROJECT_FOLLOW: 5,
  /** Founder marked your community reply helpful (anti-spam quality reward). */
  HELPFUL_MARK: 75,
  /** Early scout — backed a project before 50 followers. */
  EARLY_SCOUT: 200,
} as const;

export type PointAction = {
  key: keyof typeof POINTS;
  label: string;
  description: string;
  amount: number;
  repeatable: boolean;
};

export const POINT_ACTIONS: PointAction[] = [
  {
    key: 'FOUNDER_PROJECT_LAUNCH',
    label: 'Launch a founder project',
    description:
      'Start a project on Founder OS — earn 25,000 Founder Credits + 500 reputation points (once per project).',
    amount: POINTS.FOUNDER_PROJECT_LAUNCH,
    repeatable: true,
  },
  {
    key: 'LISTING_SCOUT_APPROVED',
    label: 'Scout a verified listing',
    description:
      'Submit a doxxed founder project, pass community vote, and get admin approval. The scout who submitted earns the largest reward.',
    amount: POINTS.LISTING_SCOUT_APPROVED,
    repeatable: true,
  },
  {
    key: 'FOUNDER_BUILD_POST',
    label: 'Founder build update',
    description: 'Share progress in the build feed — ship logs, GitHub links, day counts.',
    amount: POINTS.FOUNDER_BUILD_POST,
    repeatable: true,
  },
  {
    key: 'FOUNDER_VIDEO',
    label: 'Founder video update',
    description: 'Post an intro, deep dive, or monthly update video.',
    amount: POINTS.FOUNDER_VIDEO,
    repeatable: true,
  },
  {
    key: 'FOUNDER_COMMUNITY_POST',
    label: 'Founder community post',
    description: 'Thread or announcement in your project community room.',
    amount: POINTS.FOUNDER_COMMUNITY_POST,
    repeatable: true,
  },
  {
    key: 'REGISTER',
    label: 'Create account',
    description: 'One-time welcome bonus when you sign up.',
    amount: POINTS.REGISTER,
    repeatable: false,
  },
  {
    key: 'LISTING_SUBMIT',
    label: 'Submit listing for vote',
    description: 'Submit a project you scouted into community voting (requires sign-in).',
    amount: POINTS.LISTING_SUBMIT,
    repeatable: true,
  },
  {
    key: 'COMMUNITY_THREAD',
    label: 'Start community thread',
    description: 'Community threads earn points when founders mark replies helpful — not for spam.',
    amount: 0,
    repeatable: true,
  },
  {
    key: 'COMMUNITY_COMMENT',
    label: 'Community reply',
    description: 'Earn +75 pts when a founder marks your reply Helpful. Raw comments do not auto-reward.',
    amount: 0,
    repeatable: true,
  },
  {
    key: 'HELPFUL_MARK',
    label: 'Marked helpful by founder',
    description: 'Founder verified your reply was useful — quality over spam.',
    amount: POINTS.HELPFUL_MARK,
    repeatable: true,
  },
  {
    key: 'EARLY_SCOUT',
    label: 'Early scout badge',
    description: 'Backed a project in simulated raise before it hit 50 followers.',
    amount: POINTS.EARLY_SCOUT,
    repeatable: true,
  },
  {
    key: 'LISTING_VOTE',
    label: 'Vote on scout listing',
    description: 'Cast a YES/NO vote with your thesis on why it should (or should not) list.',
    amount: POINTS.LISTING_VOTE,
    repeatable: true,
  },
  {
    key: 'RAISE_ALLOCATE',
    label: 'Simulated raise allocation',
    description: 'Back a founder with paper cash in a simulated raise.',
    amount: POINTS.RAISE_ALLOCATE,
    repeatable: true,
  },
  {
    key: 'PAPER_TRADE',
    label: 'Paper trade',
    description: 'Each buy or sell on the paper trading desk.',
    amount: POINTS.PAPER_TRADE,
    repeatable: true,
  },
  {
    key: 'DEMAND_POLL_VOTE',
    label: 'Demand poll vote',
    description: 'Vote on a founder demand-validation poll.',
    amount: POINTS.DEMAND_POLL_VOTE,
    repeatable: true,
  },
  {
    key: 'FEED_COMMENT',
    label: 'Feed comment',
    description: 'Thoughtful comment on a feed post or trade thesis.',
    amount: POINTS.FEED_COMMENT,
    repeatable: true,
  },
  {
    key: 'PROJECT_FOLLOW',
    label: 'Follow a project',
    description: 'Track a founder project in Founder Den.',
    amount: POINTS.PROJECT_FOLLOW,
    repeatable: true,
  },
  {
    key: 'WATCHLIST_ADD',
    label: 'Add to watchlist',
    description: 'Track a verified project.',
    amount: POINTS.WATCHLIST_ADD,
    repeatable: true,
  },
];

export const LEVEL_THRESHOLDS = [
  { level: 1, minPoints: 0, label: 'Newcomer' },
  { level: 2, minPoints: 250, label: 'Contributor' },
  { level: 3, minPoints: 800, label: 'Analyst' },
  { level: 4, minPoints: 2000, label: 'Signal' },
  { level: 5, minPoints: 5000, label: 'Believer' },
] as const;

export function contributorLevelFromPoints(points: number): number {
  if (points >= 5000) return 5;
  if (points >= 2000) return 4;
  if (points >= 800) return 3;
  if (points >= 250) return 2;
  return 1;
}

export function contributorLevelLabel(level: number): string {
  const labels: Record<number, string> = {
    1: 'Newcomer',
    2: 'Contributor',
    3: 'Analyst',
    4: 'Signal',
    5: 'Believer',
  };
  return labels[level] ?? 'Newcomer';
}

export function pointsToNextLevel(points: number): {
  currentLevel: number;
  nextLevel: number | null;
  pointsNeeded: number;
} {
  const currentLevel = contributorLevelFromPoints(points);
  const next = LEVEL_THRESHOLDS.find((t) => t.level === currentLevel + 1);
  if (!next) {
    return { currentLevel, nextLevel: null, pointsNeeded: 0 };
  }
  return {
    currentLevel,
    nextLevel: next.level,
    pointsNeeded: Math.max(0, next.minPoints - points),
  };
}

const ACTION_LABELS: Record<string, string> = Object.fromEntries(
  POINT_ACTIONS.map((a) => [a.key, a.label]),
);

/** Human-readable label for a point action key (supports composite keys like RAISE_ALLOCATE:abc). */
export function pointActionLabel(actionKey: string): string {
  const base = actionKey.split(':')[0] ?? actionKey;
  if (ACTION_LABELS[base]) return ACTION_LABELS[base];
  if (base === 'HELPFUL') return 'Helpful reply marked by founder';
  if (base === 'BOUNTY') return 'Founder bounty awarded';
  if (base === 'EARLY_SCOUT') return 'Early scout badge';
  if (base === 'SCOUT_STAKE') return 'Scout market stake';
  return base.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function extractTwitterHandle(url: string | null | undefined): string | null {
  if (!url?.trim()) return null;
  const match = url.trim().match(/(?:twitter\.com|x\.com)\/([A-Za-z0-9_]+)/i);
  return match?.[1] ?? null;
}
