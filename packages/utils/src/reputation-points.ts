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
  /** Founder launches a project on the platform (once per project). */
  FOUNDER_PROJECT_LAUNCH: 25_000,
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
      'Start a project on Founder Den and open community channels. One-time bonus per project.',
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
    description: 'Open a discussion in a founder project room.',
    amount: POINTS.COMMUNITY_THREAD,
    repeatable: true,
  },
  {
    key: 'COMMUNITY_COMMENT',
    label: 'Community reply',
    description: 'Thoughtful reply on a project community thread.',
    amount: POINTS.COMMUNITY_COMMENT,
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

export function extractTwitterHandle(url: string | null | undefined): string | null {
  if (!url?.trim()) return null;
  const match = url.trim().match(/(?:twitter\.com|x\.com)\/([A-Za-z0-9_]+)/i);
  return match?.[1] ?? null;
}
