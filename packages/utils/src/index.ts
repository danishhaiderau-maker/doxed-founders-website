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

export {
  DDOLLAR_CURRENCY_NAME,
  formatDdollar,
  formatDdollarCompact,
} from './ddollar';

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
export * from './trust-weight';
export * from './missed-alpha';
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
export * from './integration-connect-guide';
export * from './security-score';
export * from './founder-agents';
export * from './build-queue';
export * from './ai-providers';
export * from './event-bus';
export * from './founder-autopilot';
export * from './control-plane';
export * from './project-memory';
export * from './founder-os-memory-files';
export * from './raise-room';
export * from './scout-markets';
export * from './founder-brain';
export * from './founder-memory-graph';
export * from './mission-state';
export * from './openhands';
export * from './cursor-cloud';
export * from './workspace-activity';
export * from './unified-feed';
export * from './gamified-roles';
export * from './notification-preferences';
export * from './prediction-markets';
export * from './project-name';
export * from './repo-starter-templates';
export * from './trading-agents';
export * from './trading-agent-adapters';
export * from './exchange-adapters';
export * from './github-repo';
export * from './listing-approval';
export * from './listing-relist';
export * from './platform-handle';
export * from './discover-universe';
export * from './discover-visibility';
export * from './llm-tokens';
export * from './feed-terminal';
export * from './task-router';
