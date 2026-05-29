import { formatTokenPrice } from './token-price';
import { type ProofOfConvictionInput } from './share';

function formatConvictionDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function formatPlPct(returnPct: number): string {
  const sign = returnPct >= 0 ? '+' : '';
  return `${sign}${returnPct.toFixed(1)}%`;
}

function clip(text: string, max: number): string {
  const t = text.replace(/\s+/g, ' ').trim();
  if (t.length <= max) return t;
  return `${t.slice(0, max - 1)}…`;
}

/** Full story thread for X composer — not limited to 280 chars */
export function buildProofOfConvictionThread(input: ProofOfConvictionInput): string {
  const lines = ['🚨 Proof of Conviction', '', `Bought $${input.ticker}`];

  if (input.recordedAt) {
    lines.push('', 'Date:', formatConvictionDate(input.recordedAt));
  }

  lines.push(
    '',
    'Entry:',
    formatTokenPrice(input.entryPrice),
    '',
    'Current:',
    formatTokenPrice(input.currentPrice),
    '',
    'Current P/L:',
    formatPlPct(input.returnPct),
  );

  if (input.thesis?.trim()) {
    lines.push('', 'Original Thesis:', `"${input.thesis.trim()}"`);
  }
  if (input.catalyst?.trim()) {
    lines.push('', 'Catalyst:', input.catalyst.trim());
  }
  if (input.targetPrice != null && input.targetPrice > 0) {
    lines.push('', 'Target:', formatTokenPrice(input.targetPrice));
  }
  if (input.timeHorizon?.trim()) {
    lines.push('', 'Time horizon:', input.timeHorizon.trim());
  }
  if (input.proofUrl) {
    lines.push('', 'Proof:', input.proofUrl);
  }

  lines.push('', '#ProofOfConviction');
  return lines.join('\n');
}

/** Single-tweet instant post (≤280 chars) with story beats */
export function buildProofOfConvictionMessage(input: ProofOfConvictionInput): string {
  const parts = [
    `🚨 Proof of Conviction · $${input.ticker}`,
    `Entry ${formatTokenPrice(input.entryPrice)} → ${formatTokenPrice(input.currentPrice)}`,
    `P/L ${formatPlPct(input.returnPct)}`,
  ];

  if (input.thesis?.trim()) {
    parts.push(`"${clip(input.thesis.trim(), 72)}"`);
  }
  if (input.proofUrl) {
    parts.push(input.proofUrl);
  }
  parts.push('#ProofOfConviction');

  let text = parts.join('\n');
  if (text.length > 280) {
    text = [
      `🚨 $${input.ticker} · ${formatPlPct(input.returnPct)}`,
      `Entry ${formatTokenPrice(input.entryPrice)} → ${formatTokenPrice(input.currentPrice)}`,
      input.proofUrl ?? '#ProofOfConviction',
    ]
      .filter(Boolean)
      .join('\n')
      .slice(0, 280);
  }
  return text;
}

export type { ProofOfConvictionInput };
