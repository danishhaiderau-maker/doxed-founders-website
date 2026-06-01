import { formatTokenPrice } from './token-price';

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

/** Viral X thread for "sold too early" moments. Footer appended by ShareOnXButton. */
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
    '#PaperTrading',
  ];
  if (input.portfolioUrl) lines.push(input.portfolioUrl);
  return lines.join('\n');
}

export type PostExitStory = {
  postExitPeakPriceUsd: number;
  postExitTroughPriceUsd: number;
  pumpAfterExitPct: number;
  dropAfterExitPct: number;
  whatIfHeldTotalPct: number;
  missedAfterExitPct: number;
  narrative: 'regret' | 'smart' | 'neutral';
};

export function computePostExitStory(input: {
  entryPriceUsd: number;
  exitPriceUsd: number;
  inTradePeakPriceUsd: number;
  postExitPeakPriceUsd: number;
  postExitTroughPriceUsd: number;
  realizedReturnPct: number;
}): PostExitStory {
  const entry = Math.max(input.entryPriceUsd, 1e-12);
  const exit = Math.max(input.exitPriceUsd, 0);
  const postPeak = Math.max(input.postExitPeakPriceUsd, exit, entry);
  const postTrough = Math.min(input.postExitTroughPriceUsd, exit);
  const truePeak = Math.max(input.inTradePeakPriceUsd, postPeak, exit);

  const pumpAfterExitPct =
    exit > 0 ? Math.max(0, round1(((postPeak - exit) / exit) * 100)) : 0;
  const dropAfterExitPct =
    exit > 0 ? Math.max(0, round1(((exit - postTrough) / exit) * 100)) : 0;
  const whatIfHeldTotalPct = round1(((truePeak - entry) / entry) * 100);
  const missedAfterExitPct = Math.max(0, round1(whatIfHeldTotalPct - input.realizedReturnPct));

  let narrative: PostExitStory['narrative'] = 'neutral';
  if (pumpAfterExitPct >= 8) narrative = 'regret';
  else if (dropAfterExitPct >= 8) narrative = 'smart';

  return {
    postExitPeakPriceUsd: postPeak,
    postExitTroughPriceUsd: postTrough,
    pumpAfterExitPct,
    dropAfterExitPct,
    whatIfHeldTotalPct,
    missedAfterExitPct,
    narrative,
  };
}

export function buildRegretShareText(input: {
  ticker: string;
  investedUsd: number;
  proceedsUsd: number;
  realizedReturnPct: number;
  whatIfHeldTotalPct: number;
  missedAfterExitPct: number;
  postExitPeakPriceUsd: number;
  exitPriceUsd: number;
  portfolioUrl?: string;
}): string {
  const lines = [
    `Sold $${input.ticker.toUpperCase()} for +${input.realizedReturnPct.toFixed(0)}% (${formatDdollar(input.investedUsd)} → ${formatDdollar(input.proceedsUsd)}).`,
    '',
    `Then it pumped to ${formatTokenPrice(input.postExitPeakPriceUsd)} after my exit (sold at ${formatTokenPrice(input.exitPriceUsd)}).`,
    '',
    `What If I Held? +${input.whatIfHeldTotalPct.toFixed(0)}%`,
    `(Missed +${input.missedAfterExitPct.toFixed(0)}% after I sold) 💀`,
  ];
  if (input.portfolioUrl) lines.push('', input.portfolioUrl);
  return lines.join('\n');
}

export function buildSmartExitShareText(input: {
  ticker: string;
  exitPriceUsd: number;
  postExitTroughPriceUsd: number;
  dropAfterExitPct: number;
  realizedReturnPct: number;
  portfolioUrl?: string;
}): string {
  const lines = [
    `Sold $${input.ticker.toUpperCase()} at ${formatTokenPrice(input.exitPriceUsd)} (+${input.realizedReturnPct.toFixed(0)}%).`,
    '',
    `Price fell to ${formatTokenPrice(input.postExitTroughPriceUsd)} after my exit (−${input.dropAfterExitPct.toFixed(0)}%).`,
    '',
    'Called the top. Skill > luck on @DoxxedCrypto.',
  ];
  if (input.portfolioUrl) lines.push('', input.portfolioUrl);
  return lines.join('\n');
}
