import { formatTokenPrice } from './token-price';
import {
  buildPortfolioShareUrl,
  type PositionShareInput,
  type ProofOfConvictionInput,
} from './share';

function formatUsd(value: number, decimals = 0): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}

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

/** Full story thread for X composer — footer appended via buildTwitterIntentUrl. */
export function buildProofOfConvictionThread(input: ProofOfConvictionInput): string {
  const ticker = input.ticker.toUpperCase();
  const lines = [
    '🚨 Proof of Conviction',
    '',
    `$${ticker} · paper trade on @DoxxedCrypto`,
  ];

  if (input.recordedAt) {
    lines.push(`Opened: ${formatConvictionDate(input.recordedAt)}`);
  }

  lines.push(
    '',
    `Entry ${formatTokenPrice(input.entryPrice)} → Now ${formatTokenPrice(input.currentPrice)}`,
    `P/L ${formatPlPct(input.returnPct)}${
      input.pnlUsd != null
        ? ` (${input.pnlUsd >= 0 ? '+' : '−'}${formatUsd(Math.abs(input.pnlUsd), 0)})`
        : ''
    }`,
  );

  if (input.investedUsd != null && input.investedUsd > 0) {
    lines.push(`Size: ${formatUsd(input.investedUsd, 0)}`);
  }

  if (input.thesis?.trim()) {
    lines.push('', 'Thesis:', `"${input.thesis.trim()}"`);
  }
  if (input.catalyst?.trim()) {
    lines.push(`Catalyst: ${input.catalyst.trim()}`);
  }
  if (input.targetPrice != null && input.targetPrice > 0) {
    lines.push(`Target: ${formatTokenPrice(input.targetPrice)}`);
  }
  if (input.timeHorizon?.trim()) {
    lines.push(`Horizon: ${input.timeHorizon.trim()}`);
  }
  if (input.proofUrl) {
    lines.push('', 'Verify:', input.proofUrl);
  }

  lines.push('', '#ProofOfConviction #PaperTrading');
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

  return parts.join('\n');
}

export type { ProofOfConvictionInput };

/** Map paper position fields to proof-of-conviction share payload. */
export function positionShareInputToProof(
  input: PositionShareInput & { userId: string; origin: string },
): ProofOfConvictionInput {
  const entryPrice =
    input.entryPrice ??
    (input.currentPrice != null ? input.currentPrice / (1 + input.pnlPercent / 100) : 0);
  const currentPrice =
    input.currentPrice ?? entryPrice * (1 + input.pnlPercent / 100);

  return {
    ticker: input.ticker,
    entryPrice,
    currentPrice,
    returnPct: input.pnlPercent,
    pnlUsd: input.pnlUsd,
    investedUsd: input.investedUsd,
    thesis: input.thesis,
    catalyst: input.catalyst,
    targetPrice: input.targetPrice,
    timeHorizon: input.timeHorizon,
    recordedAt: input.recordedAt ?? input.positionOpenedAt,
    portfolioRoi: input.portfolioRoi ?? null,
    proofUrl: buildPortfolioShareUrl(input.origin, input.userId),
  };
}

/** Rich X intent text: thesis, entry, current, P/L — footer added by buildTwitterIntentUrl. */
export function buildPositionXShareText(
  input: PositionShareInput & { userId: string; origin: string },
): string {
  return buildProofOfConvictionThread(positionShareInputToProof(input));
}
