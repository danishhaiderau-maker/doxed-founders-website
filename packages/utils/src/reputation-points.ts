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
    key: 'LISTING_SCOUT_APPROVED',
    label: 'Scout a verified listing',
    description:
      'Submit a doxxed founder project, pass community vote, and get admin approval. The scout who submitted earns the largest reward.',
    amount: POINTS.LISTING_SCOUT_APPROVED,
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
    key: 'LISTING_VOTE',
    label: 'Vote on scout listing',
    description: 'Cast a YES/NO vote with your thesis on why it should (or should not) list.',
    amount: POINTS.LISTING_VOTE,
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
    key: 'FEED_COMMENT',
    label: 'Feed comment',
    description: 'Thoughtful comment on a feed post or trade thesis.',
    amount: POINTS.FEED_COMMENT,
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
