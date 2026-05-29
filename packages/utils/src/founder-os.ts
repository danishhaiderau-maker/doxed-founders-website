/** Founder OS economy — credits vs reputation points */
export const FOUNDER_LAUNCH_CREDITS = 25_000;
export const FOUNDER_LAUNCH_REPUTATION_POINTS = 500;
export const COMMUNITY_REWARD_POOL_DEFAULT = 10_000;

export const HELPFUL_MARK_POINTS = 75;
export const HELPFUL_MARK_POOL_CREDITS = 250;

export const EARLY_SCOUT_FOLLOWER_THRESHOLD = 50;
export const EARLY_SCOUT_POINTS = 200;

export const CONNECTED_APP_PROVIDERS = [
  { key: 'github', label: 'GitHub', reputationBoost: 5 },
  { key: 'cursor', label: 'Cursor', reputationBoost: 8 },
  { key: 'x', label: 'X', reputationBoost: 3 },
  { key: 'vercel', label: 'Vercel', reputationBoost: 4 },
  { key: 'supabase', label: 'Supabase', reputationBoost: 3 },
  { key: 'discord', label: 'Discord', reputationBoost: 2 },
] as const;

export type ConnectedAppKey = (typeof CONNECTED_APP_PROVIDERS)[number]['key'];
