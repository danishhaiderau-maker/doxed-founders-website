/**
 * RK PASS (24 Oct 2026 19:36 AEDT): acct-cmqdk8n9 fence EXCLUSION evidence.
 * Computed from the DB row on EVERY fly-view read. The account is excludable from the all-accounts
 * live_fences view ONLY while ALL of: hire expired, no credentials, PAUSED, cannot arm. Fly re-checks
 * every criterion against the row itself; any flip or a failed read => back in as UNVERIFIED (blocks arming).
 */
export const LIVE_FENCES_EXCLUSION_SCHEMA = 'relay_live_fences_exclusion_v1';
export const LIVE_FENCES_EXCLUDABLE_ACCOUNTS: ReadonlySet<string> = new Set(['acct-cmqdk8n9']);
const NO_CRED_MARKERS = ['credentials-missing', 'no_credentials', 'credentials unavailable'];

export type ExclusionRow = {
  status: string; relayArmedAt: string | null; expiresAt?: Date | string | null; relayExchangeMirror: unknown;
};

export function liveFencesExclusion(account: string, row: ExclusionRow, nowMs: number): Record<string, unknown> | null {
  if (!LIVE_FENCES_EXCLUDABLE_ACCOUNTS.has(account)) return null;
  try {
    const m = row.relayExchangeMirror && typeof row.relayExchangeMirror === 'object'
      ? (row.relayExchangeMirror as Record<string, unknown>) : null;
    const err = m && typeof m.error === 'string' ? m.error.toLowerCase() : '';
    const expMs = row.expiresAt ? new Date(row.expiresAt).getTime() : NaN;
    if (row.expiresAt && !Number.isFinite(expMs)) throw new Error('expiresAt-unreadable');
    const criteria = {
      hire_expired: Number.isFinite(expMs) && expMs <= nowMs,
      no_credentials: NO_CRED_MARKERS.some((k) => err.includes(k)),
      paused: row.status === 'PAUSED',
      cannot_arm: row.status !== 'ACTIVE' && !row.relayArmedAt,
    };
    const excluded = Object.values(criteria).every((v) => v === true);
    return {
      schema: LIVE_FENCES_EXCLUSION_SCHEMA, read_ok: true, excluded,
      reason: excluded ? 'HIRE_EXPIRED+NO_CREDENTIALS+PAUSED+CANNOT_ARM' : 'CRITERIA_NOT_ALL_MET_UNVERIFIED',
      criteria,
      values: {
        status: row.status, hire_expires_at: Number.isFinite(expMs) ? new Date(expMs).toISOString() : null,
        relay_armed_at: row.relayArmedAt ?? null, mirror_error: err ? err.slice(0, 120) : null,
      },
    };
  } catch (e) {
    return { schema: LIVE_FENCES_EXCLUSION_SCHEMA, read_ok: false, excluded: false,
      reason: `READ_FAILED:${(e as Error)?.message ?? 'unknown'}`.slice(0, 120) };
  }
}

/** RK 19:36: attach exclusion evidence (computed from the DB row on every read) and log each exclusion. */
export function withFenceExclusion(
  logger: { warn: (m: string) => void } | undefined, account: string, r: ExclusionRow,
  mirror: Record<string, unknown> | null, nowMs: number,
): Record<string, unknown> | null {
  const fe = liveFencesExclusion(account, r, nowMs);
  if (!fe) return mirror;
  if (fe.excluded === true) {
    logger?.warn(`[LIVE-FENCES-EXCLUSION] ${account} excluded reason=${String(fe.reason)} values=${JSON.stringify(fe.values)}`);
  }
  return { ...(mirror ?? { ok: false, error: 'NO_MIRROR_YET' }), fence_exclusion: fe };
}
