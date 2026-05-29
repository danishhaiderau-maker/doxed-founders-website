export function slugify(text: string): string {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^\w\s-]/g, '')
    .replace(/[\s_-]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export function formatUsd(value: number, decimals = 2): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}

export function formatPercent(value: number, decimals = 2): string {
  const sign = value >= 0 ? '+' : '';
  return `${sign}${value.toFixed(decimals)}%`;
}

export function cn(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(' ');
}

export * from './founder-verification';
export * from './gecko-terminal';
export * from './display-name';
export * from './linkify';
export * from './share';
export * from './reputation-points';
export * from './listing-voting';
export * from './airdrop';
export * from './token-price';
export * from './token-input';
export * from './risk-score';
export * from './founder-presence';
export * from './virtual-economy';
export * from './engagement-rewards';
export * from './founder-os';
export * from './github-translate';
export * from './quality-rewards';
export * from './conviction-share';
export * from './publish-everywhere';
export * from './cursor-build-room';
export * from './integration-providers';
export * from './security-score';
export * from './founder-agents';
export * from './build-queue';
export * from './ai-providers';
export * from './event-bus';
export * from './project-memory';
export * from './raise-room';
export * from './scout-markets';
export * from './founder-brain';
