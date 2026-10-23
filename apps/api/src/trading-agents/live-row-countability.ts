/**
 * HM B4: website/relay live rows carry `non_countable` for forced closes.
 *
 * A force-flattened or maintenance close is not a strategy outcome. The bot already excludes these
 * from paper stats (services/btc-conservative-agent/paper_pnl_canon.py FORCED_EXIT_REASONS); live
 * rows rendered by the API must carry the same flag so no live/paper stat counts them.
 */

/** Mirrors paper_pnl_canon.FORCED_EXIT_REASONS (spec asserts this is a superset) plus relay-only forced closes. */
export const LIVE_FORCED_EXIT_REASONS: ReadonlySet<string> = new Set([
  'ADMIN_MANUAL_CLOSE',
  'ADMIN_FORCE_FLAT',
  'CIRCUIT_BREAKER_ADMIN_MANUAL',
  'DEPLOY_FORCE_FLAT',
  // Relay-side: the user stopped the relay and it flattened the copy.
  'USER_RELAY_STOP',
]);

/** Any reason naming a force-flat or a maintenance close is forced (e.g. ADMIN_PAUSE:DEPLOY_MAINTENANCE). */
const FORCED_PATTERN = /FORCE_FLAT|MAINTENANCE/;

const DISPLAY_PREFIXES = ['SOURCE_CONFIRMED_', 'LATE_ENTRY_BETTER_ONLY_CONTINUATION: ', 'LATE_ENTRY_BETTER_ONLY_CONTINUATION'];

export type LiveRowCountability = { nonCountable: boolean; nonCountableReason: string | null };

/** Strip display prefixes the mapper adds, so `SOURCE_CONFIRMED_DEPLOY_FORCE_FLAT` classifies as forced. */
function canonicalReason(raw: string | null | undefined): string {
  let s = String(raw ?? '').trim().toUpperCase();
  for (const p of DISPLAY_PREFIXES) {
    if (s.startsWith(p)) s = s.slice(p.length).replace(/^:\s*/, '');
  }
  return s;
}

/** First forced reason among the candidates (row exit reason, showcase exit reason, terminal reason). */
export function liveRowCountability(...reasons: Array<string | null | undefined>): LiveRowCountability {
  for (const r of reasons) {
    const s = canonicalReason(r);
    if (!s) continue;
    if (LIVE_FORCED_EXIT_REASONS.has(s) || FORCED_PATTERN.test(s)) {
      return { nonCountable: true, nonCountableReason: `FORCED_CLOSE:${s}` };
    }
  }
  return { nonCountable: false, nonCountableReason: null };
}
