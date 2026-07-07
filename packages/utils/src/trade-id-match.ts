/** Fuzzy match bot trade_id ↔ relay cycle tradeId (prefix / normalization). */

export type TradeIdMatchKind = 'exact' | 'prefix' | 'normalized' | 'contains' | 'none';

/**
 * F5 (2026-07-07 incident hardening) — Extract the lane prefix from a trade_id.
 * Trade IDs are `<lane-prefix>-<12-hex>` per services/btc-conservative-agent/bot.py
 * allocate_lane_trade_id. Known prefixes: vc603-, cont-, scan-, a160v2-.
 * Returns the lowercase prefix (including trailing `-`) or '' when no prefix.
 */
function extractLanePrefix(tradeId: string): string {
  // Strip the trailing 12-hex-uuid suffix first (or any 8+ hex tail) so we get
  // just the lane label. Tolerate bare-uuid input (no prefix) → ''.
  const m = /^([a-zA-Z][a-zA-Z0-9]*?)-(?=[0-9a-fA-F]{8,})/.exec(tradeId);
  return m ? `${m[1].toLowerCase()}-` : '';
}

/**
 * F5 — Two trade_ids from DIFFERENT lanes must NEVER match, even if their hex
 * suffixes substring-collide. The legacy `tradeIdsMatch` `includes` rule would
 * otherwise pair a `vc603-deadbeef1234` cycle with a `cont-deadbeef1234`
 * showcase position if the suffixes happened to overlap by ≥8 chars. Lane
 * cross-matching is the exact condition that produces orphans on the wrong
 * side of a mirror, so we reject it explicitly here.
 *
 * Returns true when both have prefixes AND they agree, OR when at least one
 * has no recognizable prefix (legacy compatibility — bare uuids etc).
 */
function lanePrefixesCompatible(a: string, b: string): boolean {
  const pa = extractLanePrefix(a);
  const pb = extractLanePrefix(b);
  if (!pa || !pb) return true; // bare-uuid / unknown — fall through to fuzzy
  return pa === pb;
}

export function tradeIdsMatch(a: string, b: string): boolean {
  if (!a || !b) return false;
  if (a === b) return true;
  // F5 — different lanes never match, period.
  if (!lanePrefixesCompatible(a, b)) return false;
  if (a.startsWith(b) || b.startsWith(a)) return true;
  const na = a.replace(/[^a-zA-Z0-9]/g, '').toLowerCase();
  const nb = b.replace(/[^a-zA-Z0-9]/g, '').toLowerCase();
  if (na === nb) return true;
  // F5 — also gate the substring rule on lane compatibility (already done above
  // but defensive): two different lanes' 12-hex tails must not substring-match.
  if (na.length >= 8 && nb.length >= 8 && (na.includes(nb) || nb.includes(na))) return true;
  return false;
}

export function classifyTradeIdMatch(a: string, b: string): TradeIdMatchKind {
  if (!a || !b) return 'none';
  if (a === b) return 'exact';
  if (!lanePrefixesCompatible(a, b)) return 'none';
  if (a.startsWith(b) || b.startsWith(a)) return 'prefix';
  const na = a.replace(/[^a-zA-Z0-9]/g, '').toLowerCase();
  const nb = b.replace(/[^a-zA-Z0-9]/g, '').toLowerCase();
  if (na === nb) return 'normalized';
  if (na.length >= 8 && nb.length >= 8 && (na.includes(nb) || nb.includes(na))) return 'contains';
  return 'none';
}

/**
 * F6 (2026-07-07 incident hardening) — Paper-lane trade IDs that must NEVER be
 * mirrored by the live copy relay. The showcase bot's research lanes
 * (services/btc-conservative-agent/bot.py) maintain a separate `paper_book`
 * for shadow-sim trades (`a160v2-*` and any future paper lane) — these are
 * paper P&L only and have no real Bitfinex fill. bot.py:10745 docstring
 * explicitly warns "Live Copy may mirror" them; this function enforces the
 * opposite so a Tile 2 toggle can never spill paper trades into a real money
 * account.
 *
 * Returns true when the trade_id belongs to a paper/research-only lane.
 */
export function isPaperLaneTradeId(tradeId: string | null | undefined): boolean {
  if (!tradeId) return false;
  const lc = tradeId.toLowerCase();
  // a160v2-* — Tile 2 independent paper lane (SHADOW SIM ONLY per dashboard).
  if (lc.startsWith('a160v2-')) return true;
  // Future-proof: any explicit paper-* / sim-* / shadow-* prefix.
  if (lc.startsWith('paper-') || lc.startsWith('sim-') || lc.startsWith('shadow-')) return true;
  return false;
}

export function pickCanonicalTradeId(preferred: string, matched?: string | null): string {
  if (preferred && matched && tradeIdsMatch(preferred, matched)) {
    return preferred.length >= matched.length ? preferred : matched;
  }
  return preferred || matched || '';
}
