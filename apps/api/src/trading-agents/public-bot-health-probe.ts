export type PublicBotHealthProbe = {
  ok: boolean;
  url: string;
  endpoint?: string;
  status?: number;
  payload?: Record<string, unknown> | null;
  error?: string;
};

export type CanonicalBotHealth = {
  ok: boolean;
  fly: boolean;
  snapshotFresh: boolean;
  botConnected: boolean;
  source: 'fly-direct' | 'signed-snapshot-cache' | 'stale-signed-snapshot' | 'unreachable';
  error?: string;
};

type ProbeResponse = {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
};

export type ProbeFetch = (
  input: string,
  init?: { signal?: AbortSignal; headers?: Record<string, string> },
) => Promise<ProbeResponse>;

/** A stored snapshot is connectivity evidence only while its own timestamp is fresh. */
export function isFreshBotSnapshot(
  snapshot: Record<string, unknown> | null,
  maxAgeSec = 90,
  nowMs = Date.now(),
): boolean {
  if (!snapshot) return false;
  const integrity =
    snapshot.state_integrity &&
    typeof snapshot.state_integrity === 'object' &&
    !Array.isArray(snapshot.state_integrity)
      ? (snapshot.state_integrity as Record<string, unknown>)
      : null;
  if (integrity?.rest_healthy === false) return false;

  const ageValue = integrity?.snapshot_age_sec;
  const reportedAge = ageValue == null ? Number.NaN : Number(ageValue);
  if (Number.isFinite(reportedAge) && reportedAge >= 0) {
    return reportedAge < maxAgeSec;
  }

  const timestamp = integrity?.snapshot_ts ?? snapshot.server_ts;
  const parsed = typeof timestamp === 'string' ? Date.parse(timestamp) : Number.NaN;
  return Number.isFinite(parsed) && Math.max(0, (nowMs - parsed) / 1_000) < maxAgeSec;
}

/**
 * Production connectivity is proved only by the exact Fly host or by the
 * authenticated canonical snapshot path supplied by BotBridgeService.
 * Legacy tunnels are deliberately not an input to this decision.
 */
export function summarizeCanonicalBotHealth(
  flyProbe: PublicBotHealthProbe,
  canonicalSnapshot: Record<string, unknown> | null,
): CanonicalBotHealth {
  const fly = flyProbe.ok;
  const snapshotAvailable = Boolean(canonicalSnapshot);
  const snapshotFresh = isFreshBotSnapshot(canonicalSnapshot);
  const botConnected = fly || snapshotFresh;
  return {
    ok: botConnected,
    fly,
    snapshotFresh,
    botConnected,
    source: fly
      ? 'fly-direct'
      : snapshotFresh
        ? 'signed-snapshot-cache'
        : snapshotAvailable
          ? 'stale-signed-snapshot'
          : 'unreachable',
    ...(!botConnected
      ? { error: 'No fresh canonical Fly or signed-snapshot health evidence is available' }
      : {}),
  };
}

/**
 * Direct server-side reachability probe. A cached database snapshot is not a
 * successful result: callers use this specifically to label the named host.
 */
export async function probePublicBotHealth(
  baseUrl: string,
  fetcher: ProbeFetch = globalThis.fetch as ProbeFetch,
  timeoutMs = 5_000,
): Promise<PublicBotHealthProbe> {
  const base = baseUrl.trim().replace(/\/$/, '');
  if (!base) return { ok: false, url: baseUrl, error: 'missing URL' };

  const attempts = ['/ready', '/api/ping'].map(async (path) => {
    const endpoint = `${base}${path}`;
    const response = await fetcher(endpoint, {
      signal: AbortSignal.timeout(timeoutMs),
      headers: {
        Accept: 'application/json',
        'User-Agent': 'doxxedcrypto-health/1.0',
      },
    });
    if (!response.ok) throw new Error(`${path} HTTP ${response.status}`);
    const body = await response.json().catch(() => null);
    return {
      ok: true,
      url: base,
      endpoint,
      status: response.status,
      payload:
        body && typeof body === 'object' && !Array.isArray(body)
          ? (body as Record<string, unknown>)
          : null,
    } satisfies PublicBotHealthProbe;
  });

  try {
    return await Promise.any(attempts);
  } catch {
    return { ok: false, url: base, error: 'direct health probe failed' };
  }
}
