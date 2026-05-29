import { type ProofOfConvictionInput } from './share';

function formatUsd(value: number, decimals = 2): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}

function formatTokenPrice(value: number): string {
  if (value >= 1) return formatUsd(value, 2);
  if (value >= 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(8).replace(/0+$/, '').replace(/\.$/, '')}`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

/** Rich thread-style copy for Proof of Conviction shares */
export function buildProofOfConvictionThread(input: ProofOfConvictionInput): string {
  const win = input.returnPct >= 0;
  const sign = win ? '+' : '';
  const lines = [
    `I opened $${input.ticker} at ${formatTokenPrice(input.entryPrice)}.`,
    '',
    'Current Price:',
    formatTokenPrice(input.currentPrice),
    '',
    'Return:',
    `${sign}${Math.round(input.returnPct)}%`,
  ];

  if (input.thesis?.trim()) {
    lines.push('', 'Reason I Bought:', `"${input.thesis.trim()}"`);
  }
  if (input.catalyst?.trim()) {
    lines.push('', 'Catalyst:', input.catalyst.trim());
  }
  if (input.targetPrice != null && input.targetPrice > 0) {
    lines.push('', 'Target:', formatTokenPrice(input.targetPrice));
  }
  if (input.recordedAt) {
    lines.push('', 'Conviction Recorded:', formatDate(input.recordedAt));
  }
  if (input.portfolioRoi != null) {
    lines.push('', 'Portfolio ROI:', `${input.portfolioRoi >= 0 ? '+' : ''}${input.portfolioRoi.toFixed(1)}%`);
  }
  if (input.proofUrl) {
    lines.push('', 'Proof:', input.proofUrl);
  }
  lines.push('', '#ProofOfConviction @DoxxedCrypto');
  return lines.join('\n');
}

export type { ProofOfConvictionInput };
