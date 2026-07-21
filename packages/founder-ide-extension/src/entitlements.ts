export const FOUNDER_FREE_MANAGED_TOKEN_CAP = 5_000_000;

export interface FounderIdeEntitlements {
  plan: 'free' | 'founder_pro' | 'launch_partner';
  managedTokens: {
    cap: number;
    used: number;
    remaining: number;
    eligible: boolean;
    resetsOrExpiresAt: string | null;
    daysRemaining: number | null;
  };
  personalProviders: {
    used: number | null;
    limit: number | null;
    localModelsCountTowardLimit: false;
  };
  message: string | null;
}

export interface EntitlementCredentials {
  apiBaseUrl: string;
  nodeId: string;
  nodeToken: string;
}

export type FounderEntitlementState = {
  source: 'live' | 'offline' | 'signed-out';
  value: FounderIdeEntitlements;
};

export function defaultFounderEntitlements(
  source: FounderEntitlementState['source'] = 'offline',
): FounderEntitlementState {
  return {
    source,
    value: {
      plan: 'free',
      managedTokens: {
        cap: FOUNDER_FREE_MANAGED_TOKEN_CAP,
        used: 0,
        remaining: FOUNDER_FREE_MANAGED_TOKEN_CAP,
        eligible: false,
        resetsOrExpiresAt: null,
        daysRemaining: null,
      },
      personalProviders: {
        used: null,
        limit: null,
        localModelsCountTowardLimit: false,
      },
      message:
        source === 'signed-out'
          ? 'Sign in to view your live Founder Free allowance.'
          : 'Live usage is temporarily unavailable. Your personal and local models remain available.',
    },
  };
}

export async function fetchFounderIdeEntitlements(
  credentials: EntitlementCredentials | null,
  fetchFn: typeof fetch = fetch,
): Promise<FounderEntitlementState> {
  if (!credentials) return defaultFounderEntitlements('signed-out');

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8_000);
  try {
    const response = await fetchFn(
      `${credentials.apiBaseUrl.replace(/\/$/, '')}/api/founder-node/ide-entitlements`,
      {
        headers: {
          Authorization: `FounderNode ${credentials.nodeId}:${credentials.nodeToken}`,
        },
        signal: controller.signal,
      },
    );
    if (!response.ok) return defaultFounderEntitlements('offline');
    const body = await response.json();
    if (!isFounderIdeEntitlements(body)) {
      return defaultFounderEntitlements('offline');
    }
    return { source: 'live', value: body };
  } catch {
    return defaultFounderEntitlements('offline');
  } finally {
    clearTimeout(timeout);
  }
}

function isFounderIdeEntitlements(value: unknown): value is FounderIdeEntitlements {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<FounderIdeEntitlements>;
  const managed = candidate.managedTokens;
  return (
    (candidate.plan === 'free' ||
      candidate.plan === 'founder_pro' ||
      candidate.plan === 'launch_partner') &&
    Boolean(managed) &&
    Number.isFinite(managed?.cap) &&
    Number.isFinite(managed?.used) &&
    Number.isFinite(managed?.remaining) &&
    typeof managed?.eligible === 'boolean'
  );
}
