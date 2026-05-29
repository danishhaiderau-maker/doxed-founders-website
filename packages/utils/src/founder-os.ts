/** Founder OS economy — credits vs reputation points */
export const FOUNDER_LAUNCH_CREDITS = 25_000;
export const FOUNDER_LAUNCH_REPUTATION_POINTS = 500;
export const COMMUNITY_REWARD_POOL_DEFAULT = 10_000;
export const CURSOR_BUILD_SESSION_CREDITS = 50;

export const HELPFUL_MARK_POINTS = 75;
export const HELPFUL_MARK_POOL_CREDITS = 250;

export const EARLY_SCOUT_FOLLOWER_THRESHOLD = 50;
export const EARLY_SCOUT_POINTS = 200;

export {
  INTEGRATION_PROVIDERS,
  CONNECTED_APP_PROVIDERS,
  type IntegrationProviderConfig,
} from './integration-providers';

export type ConnectedAppKey = (typeof import('./integration-providers').INTEGRATION_PROVIDERS)[number]['key'];
