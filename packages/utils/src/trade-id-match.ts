/** Fuzzy match bot trade_id ↔ relay cycle tradeId (prefix / normalization). */

export type TradeIdMatchKind = 'exact' | 'prefix' | 'normalized' | 'contains' | 'none';

export function tradeIdsMatch(a: string, b: string): boolean {
  if (!a || !b) return false;
  if (a === b) return true;
  if (a.startsWith(b) || b.startsWith(a)) return true;
  const na = a.replace(/[^a-zA-Z0-9]/g, '').toLowerCase();
  const nb = b.replace(/[^a-zA-Z0-9]/g, '').toLowerCase();
  if (na === nb) return true;
  if (na.length >= 8 && nb.length >= 8 && (na.includes(nb) || nb.includes(na))) return true;
  return false;
}

export function classifyTradeIdMatch(a: string, b: string): TradeIdMatchKind {
  if (!a || !b) return 'none';
  if (a === b) return 'exact';
  if (a.startsWith(b) || b.startsWith(a)) return 'prefix';
  const na = a.replace(/[^a-zA-Z0-9]/g, '').toLowerCase();
  const nb = b.replace(/[^a-zA-Z0-9]/g, '').toLowerCase();
  if (na === nb) return 'normalized';
  if (na.length >= 8 && nb.length >= 8 && (na.includes(nb) || nb.includes(na))) return 'contains';
  return 'none';
}

export function pickCanonicalTradeId(preferred: string, matched?: string | null): string {
  if (preferred && matched && tradeIdsMatch(preferred, matched)) {
    return preferred.length >= matched.length ? preferred : matched;
  }
  return preferred || matched || '';
}
