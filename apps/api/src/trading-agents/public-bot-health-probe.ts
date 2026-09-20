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
  epochBound: boolean;
  /** Fresh/stale describes analyzer age only after exact collection-epoch binding. */
  status:
    | 'epoch_bound_fresh'
    | 'epoch_bound_stale'
    | 'waiting_first_publication'
    | 'unbound'
    | 'unreachable';
  uploadedAt?: string | null;
  generatedAt?: string | null;
  ageSec?: number | null;
  size?: number | null;
  collectionEpochId?: string | null;
  reason?: string;
  source?: string;
};

/** An authenticated receipt is authoritative even when it proves no publication. */
export function selectAnalyzerMirrorInput(
  receipt: Record<string, unknown> | null,
  legacySummary: Record<string, unknown> | null,
): Record<string, unknown> | null {
  return receipt ?? legacySummary;
}

/**
 * Derive analyzer-mirror chip state from Fly `/api/analyzer/summary` external payload.
 * Never invents online without a real uploaded mirror (mirror_available + uploaded_at).
 */
export function summarizeAnalyzerMirrorHealth(
  summary: Record<string, unknown> | null,
  nowMs = Date.now(),
  maxAgeSec = ANALYZER_MIRROR_FRESH_MAX_AGE_SEC,
  currentCollectionEpochId?: string | null,
): AnalyzerMirrorHealth {
  if (!summary) {
    return { available: false, fresh: false, epochBound: false, status: 'unreachable' };
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
  const generatedAt =
    typeof mirrorStatus?.analyzer_generated_at === 'string'
      ? mirrorStatus.analyzer_generated_at
      : typeof summary.analyzer_generated_at === 'string'
        ? summary.analyzer_generated_at
        : null;
  const collectionEpochId =
    typeof mirrorStatus?.collection_epoch_id === 'string'
      ? mirrorStatus.collection_epoch_id.trim() || null
      : typeof summary.collection_epoch_id === 'string'
        ? summary.collection_epoch_id.trim() || null
        : null;
  const sizeRaw = mirrorStatus?.size ?? summary.size;
  const size = typeof sizeRaw === 'number' && Number.isFinite(sizeRaw) ? sizeRaw : null;
  const available =
    summary.mirror_available === true ||
    (typeof size === 'number' && size > 0) ||
    Boolean(uploadedAt);

  if (!available) {
    return {
      available: false,
      fresh: false,
      epochBound: false,
      status: 'waiting_first_publication',
      uploadedAt,
      generatedAt,
      ageSec: null,
      size,
      collectionEpochId,
      reason:
        typeof summary.reason === 'string'
          ? summary.reason
          : 'no complete validated analyzer publication is installed',
      source:
        typeof summary.source === 'string'
          ? summary.source
          : 'Fly trading owner + uploaded desktop analyzer mirror',
    };
  }

  const expectedEpoch = currentCollectionEpochId?.trim() || null;
  const bundleComplete = mirrorStatus?.complete ?? summary.complete;
  const bundleSchema = mirrorStatus?.schema ?? summary.schema;
  const completeBundle =
    bundleComplete === true && bundleSchema === 'analyzer_mirror_bundle_v2';
  const epochBound = Boolean(
    completeBundle && expectedEpoch && collectionEpochId === expectedEpoch,
  );
  const generatedMs = generatedAt ? Date.parse(generatedAt) : Number.NaN;
  const ageSec = Number.isFinite(generatedMs)
    ? Math.max(0, (nowMs - generatedMs) / 1_000)
    : null;
  const fresh = epochBound && ageSec != null && ageSec < maxAgeSec;

  if (!epochBound || ageSec == null) {
    return {
      available: true,
      fresh: false,
      epochBound: false,
      status: 'unbound',
      uploadedAt,
      generatedAt,
      ageSec,
      size,
      collectionEpochId,
      reason: !completeBundle
        ? 'mirror is not a complete validated bundle-v2 publication'
        : !expectedEpoch
          ? 'current collection epoch is unavailable'
          : !collectionEpochId
            ? 'mirror publication has no collection epoch binding'
            : collectionEpochId !== expectedEpoch
              ? 'mirror publication belongs to a different collection epoch'
              : 'mirror analyzer generation timestamp is invalid',
      source:
        typeof summary.source === 'string'
          ? summary.source
          : 'Fly trading owner + uploaded desktop analyzer mirror',
    };
  }

  return {
    available: true,
    fresh,
    epochBound: true,
    status: fresh ? 'epoch_bound_fresh' : 'epoch_bound_stale',
    uploadedAt,
    generatedAt,
    ageSec: ageSec != null ? Math.round(ageSec) : null,
    size,
    collectionEpochId,
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
