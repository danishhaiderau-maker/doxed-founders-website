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

export function buildSiteUrl(origin: string, path: string): string {
  const base = origin.replace(/\/$/, '');
  return `${base}${path.startsWith('/') ? path : `/${path}`}`;
}

/** X cashtag — always `$TICKER` with a leading space in prose, never `($TICKER)`. */
export function formatShareCashtag(ticker: string): string {
  const sym = ticker.replace(/^\$/, '').trim().toUpperCase();
  return sym ? `$${sym}` : '';
}

export function formatShareProjectLine(projectName: string, ticker: string): string {
  const tag = formatShareCashtag(ticker);
  return tag ? `${projectName} ${tag}` : projectName;
}

function collapseShareText(text: string, maxLen = 200): string {
  const flat = text
    .replace(/\r\n/g, '\n')
    .split('\n')
    .map((line) => line.replace(/^[\s•·\->]+/, '').trim())
    .filter(Boolean)
    .join(' · ')
    .replace(/\s+/g, ' ')
    .trim();
  if (flat.length <= maxLen) return flat;
  const cut = flat.slice(0, maxLen - 1);
  const lastSpace = cut.lastIndexOf(' ');
  return `${(lastSpace > 80 ? cut.slice(0, lastSpace) : cut).trim()}…`;
}

function isGenericListingSummary(summary: string): boolean {
  const s = summary.toLowerCase();
  return s.includes('listed via dexscreener') || s.includes('on solana. listed') || s.length < 24;
}

export function pickListingShareBlurb(input: {
  scoutHighlight?: string | null;
  scoutThesis?: string | null;
  whyDoxxed?: string | null;
  summary?: string | null;
  projectName?: string;
}): string {
  if (input.scoutHighlight?.trim()) {
    return collapseShareText(input.scoutHighlight);
  }
  if (input.scoutThesis?.trim()) {
    return collapseShareText(input.scoutThesis);
  }
  const why = input.whyDoxxed?.trim() ?? '';
  if (/building in public/i.test(why)) {
    const note = why.replace(/^\[Building in public[^\]]*\]\s*/i, '').trim();
    if (note.length >= 20) return collapseShareText(note);
  }
  if (input.summary?.trim() && !isGenericListingSummary(input.summary)) {
    return collapseShareText(input.summary);
  }
  return `${input.projectName ?? 'This project'} is live — predict, paper trade, scout the founder`;
}

export function buildListingShareMessage(input: {
  projectName: string;
  ticker: string;
  scoutHighlight?: string | null;
  scoutThesis?: string | null;
  whyDoxxed?: string | null;
  summary?: string | null;
}): string {
  const line = formatShareProjectLine(input.projectName, input.ticker);
  const blurb = pickListingShareBlurb({
    scoutHighlight: input.scoutHighlight,
    scoutThesis: input.scoutThesis,
    whyDoxxed: input.whyDoxxed,
    summary: input.summary,
    projectName: input.projectName,
  });
  return `🚀 New listing: ${line}\n${blurb}\nPredict · paper trade · scout the founder 👇\n#Crypto #FounderOS @DoxxedCrypto`;
}

export function buildPredictionShareMessage(input: {
  projectName: string;
  ticker: string;
  question: string;
  poolUsd?: number;
}): string {
  const pool =
    input.poolUsd != null && input.poolUsd > 0
      ? ` · Pool ${formatUsd(input.poolUsd, 0)} paper $`
      : '';
  const line = formatShareProjectLine(input.projectName, input.ticker);
  const q = input.question.trim().slice(0, 140);
  return `🔮 Prediction market open: ${line}${pool}\n"${q}"\nStake YES/NO with paper $ on Doxxed Crypto 👇\n#Crypto #Predict @DoxxedCrypto`;
}

export function buildFeedShareMessage(input: {
  headline: string;
  detail?: string | null;
}): string {
  const detail = input.detail?.trim() ? `\n${input.detail.trim().slice(0, 120)}` : '';
  return `${input.headline}${detail}\nLive on Doxxed Crypto 👇\n#Crypto #FounderOS @DoxxedCrypto`;
}

export function buildHotBuyShareMessage(input: {
  ticker: string;
  buyerNames: string[];
  projectName?: string;
}): string {
  const names =
    input.buyerNames.length === 0
      ? 'Traders'
      : input.buyerNames.length <= 3
        ? input.buyerNames.join(', ')
        : `${input.buyerNames.slice(0, 2).join(', ')} +${input.buyerNames.length - 2} more`;
  const label = input.projectName
    ? formatShareProjectLine(input.projectName, input.ticker)
    : formatShareCashtag(input.ticker);
  return `🔥 ${names} paper-traded ${label} on Doxxed Crypto\nSee who bought · follow top traders · stake predictions 👇\n#ProofOfConviction @DoxxedCrypto`;
}

export function shareImageFilename(pnlOrRoi: number): string {
  const side = pnlOrRoi >= 0 ? 'pump' : 'dump';
  return `dcf-${side}-flex.png`;
}
