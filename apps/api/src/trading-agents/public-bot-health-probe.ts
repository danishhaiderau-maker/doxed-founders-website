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
  /** Fly-hosted analyzer report mirror (uploaded via /api/data-sync/analyzer-report). */
  analyzerMirror?: AnalyzerMirrorHealth;
};

/** Freshness window for the uploaded Fly analyzer mirror (research, not live trading). */
export const ANALYZER_MIRROR_FRESH_MAX_AGE_SEC = 24 * 60 * 60;

export type AnalyzerMirrorHealth = {
  available: boolean;
  fresh: boolean;
  /** online = mirror present and within freshness window; stale = present but old; unreachable = no mirror. */
  status: 'online' | 'stale' | 'unreachable';
  uploadedAt?: string | null;
  ageSec?: number | null;
  analyzerGeneratedAt?: string | null;
  generationAgeSec?: number | null;
  sourceRevision?: string | null;
  revisionMatched?: boolean | null;
  size?: number | null;
  source?: string;
};

/**
 * Derive analyzer-mirror chip state from Fly `/api/analyzer/summary` external payload.
 * Never invents online without a real uploaded mirror (mirror_available + uploaded_at).
 */
export function summarizeAnalyzerMirrorHealth(
  summary: Record<string, unknown> | null,
  nowMs = Date.now(),
  maxAgeSec = ANALYZER_MIRROR_FRESH_MAX_AGE_SEC,
  expectedSourceRevision?: string | null,
): AnalyzerMirrorHealth {
  if (!summary) {
    return { available: false, fresh: false, status: 'unreachable' };
  }

  const mirrorStatus =
    summary.mirror_status &&
    typeof summary.mirror_status === 'object' &&
    !Array.isArray(summary.mirror_status)
      ? (summary.mirror_status as Record<string, unknown>)
      : null;
  const uploadedAt =
    typeof mirrorStatus?.uploaded_at === 'string'
      ? mirrorStatus.uploaded_at
      : typeof summary.uploaded_at === 'string'
        ? summary.uploaded_at
        : null;
  const sizeRaw = mirrorStatus?.size ?? summary.size;
  const size = typeof sizeRaw === 'number' && Number.isFinite(sizeRaw) ? sizeRaw : null;
  const analyzerGeneratedAt =
    typeof mirrorStatus?.analyzer_generated_at === 'string'
      ? mirrorStatus.analyzer_generated_at
      : typeof summary.analyzer_generated_at === 'string'
        ? summary.analyzer_generated_at
        : null;
  const sourceRevision =
    typeof mirrorStatus?.source_data_revision === 'string'
      ? mirrorStatus.source_data_revision
      : typeof summary.source_data_revision === 'string'
        ? summary.source_data_revision
        : null;
  const available =
    summary.available === true ||
    summary.mirror_available === true ||
    (typeof size === 'number' && size > 0) ||
    Boolean(uploadedAt);

  if (!available) {
    return {
      available: false,
      fresh: false,
      status: 'unreachable',
      uploadedAt,
      ageSec: null,
      analyzerGeneratedAt,
      generationAgeSec: null,
      sourceRevision,
      revisionMatched: null,
      size,
      source:
        typeof summary.source === 'string'
          ? summary.source
          : 'Fly trading owner + uploaded desktop analyzer mirror',
    };
  }

  const timestampAgeSec = (value: string | null): number | null => {
    const parsed = value ? Date.parse(value) : Number.NaN;
    if (!Number.isFinite(parsed) || parsed > nowMs) return null;
    return (nowMs - parsed) / 1_000;
  };
  const ageSec = timestampAgeSec(uploadedAt);
  const generationAgeSec = timestampAgeSec(analyzerGeneratedAt);
  const normalizeRevision = (value: string | null | undefined): string | null => {
    const normalized = value?.trim().toLowerCase() ?? '';
    return /^[0-9a-f]{12,64}$/.test(normalized) ? normalized : null;
  };
  const expectedRevision = normalizeRevision(expectedSourceRevision);
  const observedRevision = normalizeRevision(sourceRevision);
  const revisionMatched = expectedSourceRevision == null
    ? null
    : expectedRevision != null && observedRevision != null &&
      (expectedRevision === observedRevision ||
        expectedRevision.startsWith(observedRevision) ||
        observedRevision.startsWith(expectedRevision));
  const fresh =
    ageSec != null && ageSec < maxAgeSec &&
    generationAgeSec != null && generationAgeSec < maxAgeSec &&
    (expectedSourceRevision == null || revisionMatched === true);

  return {
    available: true,
    fresh,
    // Presence with invalid/missing timestamps is stale/unknown evidence, not
    // an unreachable mirror. `unreachable` is reserved for no mirror at all.
    status: fresh ? 'online' : 'stale',
    uploadedAt,
    ageSec: ageSec != null ? Math.round(ageSec) : null,
    analyzerGeneratedAt,
    generationAgeSec: generationAgeSec != null ? Math.round(generationAgeSec) : null,
    sourceRevision,
    revisionMatched,
    size,
    source:
      typeof summary.source === 'string'
        ? summary.source
        : 'Fly trading owner + uploaded desktop analyzer mirror',
  };
}

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
