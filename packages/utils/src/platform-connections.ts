/** Phase 2 — infrastructure connect hub (publish / sync / AI context toggles). */

export type PlatformConnectionToggles = {
  publish: boolean;
  syncBack: boolean;
  aiContext: boolean;
};

export type PlatformConnectionState = PlatformConnectionToggles & {
  lastHealthAt?: string;
  healthOk?: boolean;
  healthDetail?: string;
};

export type PlatformConnectionsMap = Record<string, PlatformConnectionState>;

export const DEFAULT_PLATFORM_TOGGLES: PlatformConnectionToggles = {
  publish: false,
  syncBack: false,
  aiContext: true,
};

export type PlatformHubProvider = {
  key: string;
  label: string;
  /** integrationCredential provider key, or special: github | founder_node */
  credentialKey: string;
  connectType: 'repo' | 'token' | 'node';
  description: string;
};

export const PLATFORM_HUB_PROVIDERS: PlatformHubProvider[] = [
  {
    key: 'github',
    label: 'GitHub',
    credentialKey: 'github',
    connectType: 'repo',
    description: 'Commits, PRs, and remote agent repos.',
  },
  {
    key: 'render',
    label: 'Render',
    credentialKey: 'render',
    connectType: 'token',
    description: 'Web + Postgres on one hobby dashboard (recommended starter).',
  },
  {
    key: 'railway',
    label: 'Railway',
    credentialKey: 'railway',
    connectType: 'token',
    description: 'Unified API, web, Postgres, and long-running bots.',
  },
  {
    key: 'vercel',
    label: 'Vercel',
    credentialKey: 'vercel',
    connectType: 'token',
    description: 'Next.js deploy webhooks and production URLs.',
  },
  {
    key: 'neon',
    label: 'Neon',
    credentialKey: 'neon',
    connectType: 'token',
    description: 'Serverless Postgres — stack transparency for supporters.',
  },
  {
    key: 'supabase',
    label: 'Supabase',
    credentialKey: 'supabase',
    connectType: 'token',
    description: 'DB + auth + storage bundle.',
  },
  {
    key: 'founder_node',
    label: 'Founder Node',
    credentialKey: 'founder_node',
    connectType: 'node',
    description: 'Local vault, Ollama, and Founder Cloud mode.',
  },
];

export function normalizePlatformConnections(raw: unknown): PlatformConnectionsMap {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
  const out: PlatformConnectionsMap = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) continue;
    const v = value as Record<string, unknown>;
    out[key] = {
      publish: Boolean(v.publish),
      syncBack: Boolean(v.syncBack),
      aiContext: v.aiContext !== false,
      lastHealthAt: typeof v.lastHealthAt === 'string' ? v.lastHealthAt : undefined,
      healthOk: typeof v.healthOk === 'boolean' ? v.healthOk : undefined,
      healthDetail: typeof v.healthDetail === 'string' ? v.healthDetail : undefined,
    };
  }
  return out;
}

export function getPlatformToggles(
  map: PlatformConnectionsMap,
  providerKey: string,
): PlatformConnectionState {
  return { ...DEFAULT_PLATFORM_TOGGLES, ...map[providerKey] };
}

export function patchPlatformConnections(
  map: PlatformConnectionsMap,
  providerKey: string,
  patch: Partial<PlatformConnectionToggles & { healthOk?: boolean; healthDetail?: string }>,
): PlatformConnectionsMap {
  const prev = getPlatformToggles(map, providerKey);
  const next: PlatformConnectionState = {
    ...prev,
    ...patch,
    ...(patch.healthOk !== undefined || patch.healthDetail !== undefined
      ? { lastHealthAt: new Date().toISOString() }
      : {}),
  };
  return { ...map, [providerKey]: next };
}
