/** Copy + factor weights for Discover bubble size / visibility — founder-facing. */

export type DiscoverVisibilityFactor = {
  key: string;
  label: string;
  maxPoints: number;
  description: string;
  founderAction: string;
};

/** How activity score (0–100) maps to bubble diameter on /discover */
export const DISCOVER_BUBBLE_SIZE_TIERS = [
  { minScore: 76, label: 'Largest', diameterPx: 120 },
  { minScore: 51, label: 'Large', diameterPx: 96 },
  { minScore: 26, label: 'Medium', diameterPx: 72 },
  { minScore: 0, label: 'Starter', diameterPx: 56 },
] as const;

/** Weights aligned with computeDiscoverActivityScore in discover-universe.ts */
export const DISCOVER_ACTIVITY_FACTORS: DiscoverVisibilityFactor[] = [
  {
    key: 'build_posts',
    label: 'Build-in-public posts',
    maxPoints: 24,
    description: 'Founder OS feed posts, Social Hub publishes, and shipped updates.',
    founderAction: 'Post from Mission Control → Share to Feed / X / Community.',
  },
  {
    key: 'github',
    label: 'GitHub commits & deploys',
    maxPoints: 18,
    description: 'Synced commits and deploy events from your linked repo.',
    founderAction: 'Connect GitHub, push real commits, run Autopilot sync.',
  },
  {
    key: 'dd_inflow',
    label: 'DDollar paper inflow',
    maxPoints: 20,
    description: 'Traders allocating simulated DDollar to your project ticker.',
    founderAction: 'Ship visible progress so scouts and traders follow conviction.',
  },
  {
    key: 'trade_volume',
    label: 'DDollar trade volume',
    maxPoints: 10,
    description: 'Total buy/sell activity on your project in the selected timeframe.',
    founderAction: 'Keep launch readiness and updates fresh so paper traders engage.',
  },
  {
    key: 'followers',
    label: 'New followers',
    maxPoints: 12,
    description: 'Accounts following your project on the platform.',
    founderAction: 'Consistent updates + listing quality → more follows.',
  },
  {
    key: 'scout_stake',
    label: 'Scout market stake',
    maxPoints: 8,
    description: 'DDollar staked on scout/prediction markets for your listing.',
    founderAction: 'Encourage community scouts; pass listing votes with proof.',
  },
  {
    key: 'community',
    label: 'Community threads',
    maxPoints: 8,
    description: 'Discussions and signals on your project hub.',
    founderAction: 'Reply in community; share milestones traders can verify.',
  },
  {
    key: 'bubble_score',
    label: 'Long-term bubble score',
    maxPoints: 10,
    description: 'Followers, build posts, raise demand, and launch readiness combined.',
    founderAction:
      'Raise launch readiness: videos, followers, GitHub, simulated raise demand, streak.',
  },
];

export const DISCOVER_BUBBLE_SCORE_FORMULA =
  'bubbleScore ≈ min(1000, followers×3 + buildPosts×5 + round(raiseDemand/1000) + launchReadiness%)';

export const DISCOVER_VISIBILITY_SUMMARY =
  'Bigger bubbles and higher sort order mean more real traction in the window you select (1h–7d). Traders see DDollar flow on the bubble, activity score on the badge, and conviction in the table — not hype alone.';

/** Short copy for the transparent rules panel above the bubble map on /discover */
export const DISCOVER_RANKING_RULES_HEADLINE =
  'How ranking & bubble size work (transparent)';

export const DISCOVER_RANKING_RULES_INTRO =
  'Projects are sorted by activity score in your selected window (1h, 6h, 24h, or 7d). The number on each bubble is that score (0–100). More real traction in that window → higher score → larger bubble and higher placement.';

export const DISCOVER_RING_LEGEND_NOTE =
  'Outer ring color = project stage. Live tokens on-chain always use a green ring, even if listed recently.';
