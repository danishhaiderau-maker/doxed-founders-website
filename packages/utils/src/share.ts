function formatUsd(value: number, decimals = 2): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}

export const SHARE_PUMP_COUNT = 18;
export const SHARE_DUMP_COUNT = 16;

export function pickShareImagePath(pnlOrRoi: number): string {
  const side = pnlOrRoi >= 0 ? 'pump' : 'dump';
  const count = side === 'pump' ? SHARE_PUMP_COUNT : SHARE_DUMP_COUNT;
  const n = Math.floor(Math.random() * count) + 1;
  return `/share/${side}/${side}_${String(n).padStart(2, '0')}.png`;
}

export function buildPortfolioPath(userId: string): string {
  return `/portfolio/${userId}`;
}

export function buildPortfolioShareUrl(origin: string, userId: string): string {
  const base = origin.replace(/\/$/, '');
  return `${base}${buildPortfolioPath(userId)}`;
}

export function buildPortfolioShareMessage(
  displayName: string,
  roi: number,
  totalValue: number,
): string {
  const sign = roi >= 0 ? '+' : '';
  const emoji = roi >= 0 ? '🚀' : '📉';
  return `${emoji} ${displayName} · ${sign}${roi.toFixed(1)}% paper ROI · ${formatUsd(totalValue)} on @DoxxedCrypto #ProofOfConviction`;
}

export type PositionShareInput = {
  displayName: string;
  ticker: string;
  projectName: string;
  investedUsd: number;
  pnlUsd: number;
  pnlPercent: number;
  thesis?: string | null;
  entryPrice?: number;
  currentPrice?: number;
  catalyst?: string | null;
  targetPrice?: number | null;
  timeHorizon?: string | null;
  recordedAt?: string | null;
  positionOpenedAt?: string | null;
  daysHeld?: number;
  portfolioRoi?: number;
};

export type ProofOfConvictionInput = {
  ticker: string;
  entryPrice: number;
  currentPrice: number;
  returnPct: number;
  thesis?: string | null;
  catalyst?: string | null;
  targetPrice?: number | null;
  timeHorizon?: string | null;
  recordedAt?: string | null;
  portfolioRoi?: number | null;
  proofUrl?: string;
};

export function buildPositionShareMessage(input: PositionShareInput): string {
  const win = input.pnlPercent >= 0;
  const emoji = win ? '🚀' : '📉';
  const sign = win ? '+' : '−';
  const lines = [
    `${emoji} ${input.displayName} on $${input.ticker}`,
    `${input.projectName}`,
    `💰 ${formatUsd(input.investedUsd, 0)} on this position`,
    `${sign}${Math.abs(Math.round(input.pnlPercent))}% (${sign}${formatUsd(Math.abs(input.pnlUsd), 0)}) on this trade`,
    `(Position P&L — not whole portfolio)`,
  ];
  if (input.thesis?.trim()) {
    const t = input.thesis.trim().replace(/\s+/g, ' ');
    lines.push(`💬 "${t.length > 100 ? `${t.slice(0, 99)}…` : t}"`);
  }
  lines.push('#ProofOfConviction @DoxxedCrypto');
  return lines.join('\n');
}

export function buildTwitterIntentUrl(text: string, url?: string): string {
  const params = new URLSearchParams({ text });
  if (url) {
    params.set('url', url);
  }
  return `https://twitter.com/intent/tweet?${params.toString()}`;
}

export function shareImageFilename(pnlOrRoi: number): string {
  const side = pnlOrRoi >= 0 ? 'pump' : 'dump';
  return `dcf-${side}-flex.png`;
}
