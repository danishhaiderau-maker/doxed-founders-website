/** In-game currency name — use consistently across the platform. */
export const DDOLLAR_CURRENCY_NAME = 'Ddollar';

/** Display in-game balances — e.g. `$24,965 Ddollar`. */
export function formatDdollar(value: number, decimals = 0): string {
  const amount = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
  return `${amount} ${DDOLLAR_CURRENCY_NAME}`;
}

/** Compact suffix for tight UI (wallet widget). */
export function formatDdollarCompact(value: number, decimals = 0): string {
  const n = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
  return `$${n} ${DDOLLAR_CURRENCY_NAME}`;
}
