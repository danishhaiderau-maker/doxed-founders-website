/** Post-trade "What If I Held?" / missed alpha calculations. */

export type MissedAlphaInput = {
  entryPriceUsd: number;
  exitPriceUsd: number;
  /** Highest price observed while the position was open. */
  peakPriceUsd: number;
  investedUsd: number;
  proceedsUsd: number;
};

export type MissedAlphaResult = {
  realizedReturnPct: number;
  whatIfHeldReturnPct: number;
  missedAlphaPct: number;
  alphaLeftOnTableUsd: number;
  convictionScore: number;
};

export function computeMissedAlpha(input: MissedAlphaInput): MissedAlphaResult {
  const entry = Math.max(input.entryPriceUsd, 1e-12);
  const exit = Math.max(input.exitPriceUsd, 0);
  const peak = Math.max(input.peakPriceUsd, exit, entry);

  const realizedReturnPct = ((exit - entry) / entry) * 100;
  const whatIfHeldReturnPct = ((peak - entry) / entry) * 100;
  const missedAlphaPct = Math.max(0, whatIfHeldReturnPct - realizedReturnPct);

  const peakValue = input.investedUsd * (peak / entry);
  const alphaLeftOnTableUsd = Math.max(0, peakValue - input.proceedsUsd);

  const exitQuality =
    whatIfHeldReturnPct > 0
      ? Math.min(100, Math.round((realizedReturnPct / whatIfHeldReturnPct) * 100))
      : realizedReturnPct >= 0
        ? 100
        : Math.max(0, 100 + Math.round(realizedReturnPct));

  const convictionScore = Math.max(0, Math.min(100, exitQuality));

  return {
    realizedReturnPct: round1(realizedReturnPct),
    whatIfHeldReturnPct: round1(whatIfHeldReturnPct),
    missedAlphaPct: round1(missedAlphaPct),
    alphaLeftOnTableUsd: round2(alphaLeftOnTableUsd),
    convictionScore,
  };
}

function round1(n: number) {
  return Math.round(n * 10) / 10;
}

function round2(n: number) {
  return Math.round(n * 100) / 100;
}

function formatDdollar(value: number): string {
  return `${Math.round(value).toLocaleString()} DDollar`;
}

export type WhatIfIHeldShareInput = {
  ticker: string;
  investedUsd: number;
  proceedsUsd: number;
  realizedReturnPct: number;
  whatIfHeldReturnPct: number;
  missedAlphaPct: number;
  displayName?: string;
  portfolioUrl?: string;
};

/** Viral X thread for "sold too early" moments. */
export function buildWhatIfIHeldShareText(input: WhatIfIHeldShareInput): string {
  const lines = [
    `I turned ${formatDdollar(input.investedUsd)} into ${formatDdollar(input.proceedsUsd)} on $${input.ticker.toUpperCase()}.`,
    '',
    `Thought I nailed the trade (+${input.realizedReturnPct.toFixed(0)}%).`,
    '',
    'Turns out I sold too early.',
    '',
    `What If I Held? +${input.whatIfHeldReturnPct.toFixed(0)}%`,
    '',
    `(Missed alpha: +${input.missedAlphaPct.toFixed(0)}%) 💀`,
    '',
    'Track missed opportunities on Doxxed Crypto.',
    'Built for shrimps, not whales.',
  ];
  if (input.portfolioUrl) lines.push('', input.portfolioUrl);
  return lines.join('\n');
}

export function buildShareWinText(input: {
  ticker: string;
  investedUsd: number;
  proceedsUsd: number;
  realizedReturnPct: number;
  portfolioUrl?: string;
}): string {
  const lines = [
    `Closed $${input.ticker.toUpperCase()} paper trade on @DoxxedCrypto`,
    `${formatDdollar(input.investedUsd)} → ${formatDdollar(input.proceedsUsd)} (+${input.realizedReturnPct.toFixed(0)}%)`,
    '#PaperTrading · Built for shrimps, not whales.',
  ];
  if (input.portfolioUrl) lines.push(input.portfolioUrl);
  return lines.join('\n');
}
