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
    `I sold $${input.ticker.toUpperCase()} too early.`,
    '',
    `Locked +${input.realizedReturnPct.toFixed(0)}% on exit.`,
    '',
    `Price since exit: +${input.missedAlphaPct.toFixed(0)}% missed alpha.`,
    '',
    'Profit taken — alpha left on the table.',
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
    `Sold $${input.ticker.toUpperCase()}`,
    '',
    `Gain: +${input.realizedReturnPct.toFixed(0)}%`,
    `${formatDdollar(input.investedUsd)} → ${formatDdollar(input.proceedsUsd)}`,
    '',
    'Thesis played out.',
  ];
  if (input.portfolioUrl) lines.push('', input.portfolioUrl);
  return lines.join('\n');
}

export type PostExitStory = {
  postExitPeakPriceUsd: number;
  postExitTroughPriceUsd: number;
  /** Highest move above exit price since sell (peak since exit / exit). */
  pumpAfterExitPct: number;
  /** Largest drawdown below exit since sell (exit - trough / exit). */
  dropAfterExitPct: number;
  /** Current price vs exit — positive = missed upside, negative = avoided loss. */
  currentVsExitPct: number;
  /** max(0, currentVsExitPct) — missed alpha from current price. */
  missedAfterExitPct: number;
  /** max(0, -currentVsExitPct) — loss avoided vs current price. */
  avoidedLossPct: number;
  /** @deprecated use pumpAfterExitPct — kept for API compat */
  whatIfHeldTotalPct: number;
  narrative: 'regret' | 'smart' | 'neutral';
};

const POST_EXIT_THRESHOLD_PCT = 5;

/**
 * Post-exit story — all percentages reference SELL price as baseline (not entry ATH).
 */
export function computePostExitStory(input: {
  exitPriceUsd: number;
  postExitPeakPriceUsd: number;
  postExitTroughPriceUsd: number;
  currentPriceUsd: number;
}): PostExitStory {
  const exit = Math.max(input.exitPriceUsd, 1e-12);
  const current = Math.max(input.currentPriceUsd, 0);
  const postPeak = Math.max(input.postExitPeakPriceUsd, exit, current);
  const postTrough = Math.min(input.postExitTroughPriceUsd, exit, current || exit);

  const pumpAfterExitPct = round1(Math.max(0, ((postPeak - exit) / exit) * 100));
  const currentVsExitPct = round1(((current - exit) / exit) * 100);
  const dropAfterExitPct = round1(Math.max(0, ((exit - postTrough) / exit) * 100));
  const missedAfterExitPct = round1(Math.max(0, currentVsExitPct));
  const avoidedLossPct = round1(Math.max(0, -currentVsExitPct));

  let narrative: PostExitStory['narrative'] = 'neutral';
  if (currentVsExitPct >= POST_EXIT_THRESHOLD_PCT) narrative = 'regret';
  else if (currentVsExitPct <= -POST_EXIT_THRESHOLD_PCT) narrative = 'smart';

  return {
    postExitPeakPriceUsd: postPeak,
    postExitTroughPriceUsd: postTrough,
    pumpAfterExitPct,
    dropAfterExitPct,
    currentVsExitPct,
    missedAfterExitPct,
    avoidedLossPct,
    whatIfHeldTotalPct: pumpAfterExitPct,
    narrative,
  };
}

export function postExitOutcomeLabel(story: Pick<
  PostExitStory,
  'narrative' | 'missedAfterExitPct' | 'avoidedLossPct' | 'currentVsExitPct'
>): { emoji: string; title: string; detail: string } {
  if (story.narrative === 'regret') {
    return {
      emoji: '🚀',
      title: 'Sold Too Early',
      detail: `Missed alpha +${story.missedAfterExitPct.toFixed(0)}%`,
    };
  }
  if (story.narrative === 'smart') {
    return {
      emoji: '🛡',
      title: 'Smart Exit',
      detail: `Avoided loss −${story.avoidedLossPct.toFixed(0)}%`,
    };
  }
  return {
    emoji: '🎯',
    title: 'Neutral Exit',
    detail: 'Price near your exit',
  };
}

export function buildRegretShareText(input: {
  ticker: string;
  realizedReturnPct: number;
  missedAfterExitPct: number;
  pumpAfterExitPct: number;
  exitPriceUsd: number;
  postExitPeakPriceUsd: number;
  portfolioUrl?: string;
}): string {
  const lines = [
    `I sold $${input.ticker.toUpperCase()} too early.`,
    '',
    `Exit: +${input.realizedReturnPct.toFixed(0)}% locked.`,
    '',
    `Missed: +${input.missedAfterExitPct.toFixed(0)}% since exit`,
    `(peaked +${input.pumpAfterExitPct.toFixed(0)}% after ${formatTokenPrice(input.exitPriceUsd)})`,
    '',
    'Track conviction trades with DDollar.',
  ];
  if (input.portfolioUrl) lines.push('', input.portfolioUrl);
  return lines.join('\n');
}

export function buildSmartExitShareText(input: {
  ticker: string;
  exitPriceUsd: number;
  postExitTroughPriceUsd: number;
  avoidedLossPct: number;
  realizedReturnPct: number;
  portfolioUrl?: string;
}): string {
  const lines = [
    `Smart exit on $${input.ticker.toUpperCase()}.`,
    '',
    `Sold at ${formatTokenPrice(input.exitPriceUsd)} (+${input.realizedReturnPct.toFixed(0)}%).`,
    '',
    `Price fell to ${formatTokenPrice(input.postExitTroughPriceUsd)} after exit.`,
    '',
    `Avoided loss −${input.avoidedLossPct.toFixed(0)}%.`,
  ];
  if (input.portfolioUrl) lines.push('', input.portfolioUrl);
  return lines.join('\n');
}

export function buildTimelineBuyShareText(input: {
  ticker: string;
  amountUsd: number;
  thesis?: string | null;
  portfolioUrl?: string;
}): string {
  const lines = [
    `Bought $${input.ticker.toUpperCase()}`,
    '',
    `DDollar position: ${formatDdollar(input.amountUsd)}`,
  ];
  if (input.thesis?.trim()) {
    lines.push('', `Reason:`, `"${input.thesis.trim().slice(0, 120)}"`);
  }
  lines.push('', 'Track builders, not hype.');
  if (input.portfolioUrl) lines.push('', input.portfolioUrl);
  return lines.join('\n');
}

export function buildTimelineSellShareText(input: {
  ticker: string;
  realizedReturnPct: number;
  amountUsd: number;
  thesis?: string | null;
  portfolioUrl?: string;
}): string {
  const lines = [
    `Sold $${input.ticker.toUpperCase()}`,
    '',
    `Gain: ${input.realizedReturnPct >= 0 ? '+' : ''}${input.realizedReturnPct.toFixed(0)}%`,
    `${formatDdollar(input.amountUsd)} proceeds`,
  ];
  if (input.thesis?.trim()) {
    lines.push('', `Thesis: "${input.thesis.trim().slice(0, 100)}"`);
  }
  lines.push('', 'Track conviction trades.');
  if (input.portfolioUrl) lines.push('', input.portfolioUrl);
  return lines.join('\n');
}

export function buildTimelineThesisShareText(input: {
  ticker: string;
  thesis: string;
  portfolioUrl?: string;
}): string {
  const lines = [
    `Updated thesis on $${input.ticker.toUpperCase()}`,
    '',
    `"${input.thesis.trim().slice(0, 200)}"`,
    '',
    'Conviction on record.',
  ];
  if (input.portfolioUrl) lines.push('', input.portfolioUrl);
  return lines.join('\n');
}
