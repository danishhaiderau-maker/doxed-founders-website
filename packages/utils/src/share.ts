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
  pnl?: number,
): string {
  const sign = roi >= 0 ? '+' : '';
  const emoji = roi >= 0 ? '🚀' : '📉';
  const pnlSign = pnl != null && pnl >= 0 ? '+' : '−';
  const pnlPart =
    pnl != null
      ? `\nP&L: ${pnlSign}${formatUsd(Math.abs(pnl), 0)} · ROI ${sign}${roi.toFixed(1)}%`
      : `\nROI: ${sign}${roi.toFixed(1)}%`;
  return `${emoji} ${displayName} — paper portfolio ${formatUsd(totalValue)}${pnlPart}\n#ProofOfConviction @DoxxedCrypto`;
}

export function buildTraderRankShareMessage(input: {
  displayName: string;
  roi: number;
  totalValue: number;
  pnl: number;
  rank?: number;
  isLoser?: boolean;
  isBusted?: boolean;
}): string {
  const rankPart = input.rank != null ? `#${input.rank} ` : '';
  const bustedPart = input.isBusted ? ' · BUSTED account' : '';
  const base = buildPortfolioShareMessage(
    input.displayName,
    input.roi,
    input.totalValue,
    input.pnl,
  );
  if (input.isLoser) {
    return `📉 ${rankPart}${input.displayName} — down ${formatUsd(Math.abs(input.pnl), 0)} (${input.roi.toFixed(1)}% ROI)${bustedPart} · See the paper portfolio on Doxxed Crypto`;
  }
  return `${rankPart}${base}`;
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
  pnlUsd?: number;
  investedUsd?: number;
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

export const PLATFORM_X_SHARE_FOOTER = [
  '',
  'Built for shrimps, not whales.',
  '',
  '🎁 10,000 DDollar free at signup',
  '📈 Trade any token for free',
  '🏆 Build a following through skill',
  '',
  'No scams. No pump & dumps. No extractors.',
  '',
  'Back real builders. Bring back HODL.',
].join('\n');

export function appendPlatformXShareFooter(text: string, customFooter?: string | null): string {
  const base = text.trimEnd();
  const footer = (customFooter?.trim() || PLATFORM_X_SHARE_FOOTER).trim();
  if (!footer) return base;
  if (base.includes(footer.split('\n')[0] ?? '')) return base;
  return `${base}\n\n${footer}`;
}

/** Keep marketing footer on single tweets — trim body if needed (280 char X limit). */
export function fitXShareTextWithFooter(body: string, maxLen = 280, customFooter?: string | null): string {
  const withFooter = appendPlatformXShareFooter(body, customFooter);
  if (withFooter.length <= maxLen) return withFooter;

  const compactFooter = [
    '',
    'Built for shrimps, not whales.',
    '🎁 10,000 DDollar free · 📈 Trade free · 🏆 Skill > hype',
    'Back real builders. Bring back HODL.',
  ].join('\n');

  const trimmedBody = body.trim();
  const room = maxLen - compactFooter.length - 1;
  const clipped =
    trimmedBody.length <= room
      ? trimmedBody
      : `${trimmedBody.slice(0, Math.max(32, room - 1)).replace(/\s+\S*$/, '')}…`;
  return `${clipped}${compactFooter}`.slice(0, maxLen);
}

export function buildTwitterIntentUrl(text: string, url?: string, customFooter?: string | null): string {
  const fullText = appendPlatformXShareFooter(text, customFooter);
  const params = new URLSearchParams({ text: fullText });
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

function paraphraseShareSnippet(text: string, maxLen = 72): string {
  const flat = text
    .replace(/https?:\/\/\S+/gi, '')
    .replace(/\s+/g, ' ')
    .trim();
  if (!flat) return '';
  if (flat.length <= maxLen) return flat;
  const cut = flat.slice(0, maxLen - 1);
  const lastSpace = cut.lastIndexOf(' ');
  return `${(lastSpace > 40 ? cut.slice(0, lastSpace) : cut).trim()}…`;
}

export function pickHotBuyThesis(input: {
  scoutHighlight?: string | null;
  scoutThesis?: string | null;
  summary?: string | null;
  projectName?: string;
}): string {
  if (input.scoutHighlight?.trim()) {
    return paraphraseShareSnippet(input.scoutHighlight, 90);
  }
  if (input.scoutThesis?.trim()) {
    return paraphraseShareSnippet(input.scoutThesis, 90);
  }
  if (input.summary?.trim() && input.summary.trim().length >= 24) {
    return paraphraseShareSnippet(input.summary, 90);
  }
  return '';
}

export function buildHotBuyShareMessage(input: {
  ticker: string;
  buyerNames: string[];
  projectName?: string;
  pctOfActive?: number;
  detailLine?: string;
  scoutHighlight?: string | null;
  scoutThesis?: string | null;
  summary?: string | null;
  communitySnippets?: string[];
}): string {
  const names =
    input.buyerNames.length === 0
      ? 'Traders'
      : input.buyerNames.length <= 2
        ? input.buyerNames.join(', ')
        : `${input.buyerNames.slice(0, 2).join(', ')} +${input.buyerNames.length - 2} more`;
  const label = input.projectName
    ? formatShareProjectLine(input.projectName, input.ticker)
    : formatShareCashtag(input.ticker);
  const pct =
    input.pctOfActive != null && input.pctOfActive > 0
      ? ` · ${Math.round(input.pctOfActive * 100)}% of active in 24h`
      : '';
  const detail = input.detailLine?.trim() ? `\n${input.detailLine.trim()}` : '';
  const thesis = pickHotBuyThesis(input);
  const thesisLine = thesis ? `\nThesis: ${thesis}` : '';
  const snippets = (input.communitySnippets ?? [])
    .map((s) => paraphraseShareSnippet(s, 64))
    .filter(Boolean)
    .slice(0, 2);
  const communityLine =
    snippets.length > 0 ? `\nTraders say: ${snippets.join(' · ')}` : '';
  const body = `🔥 ${names} bought ${label}${pct}${detail}${thesisLine}${communityLine}`;
  const maxBody = 240;
  const trimmed =
    body.length <= maxBody
      ? body
      : `${body.slice(0, maxBody - 1).replace(/\s+\S*$/, '')}…`;
  return `${trimmed}\nLive on Doxxed Crypto 👇\n#Crypto #FounderOS #ProofOfConviction @DoxxedCrypto`;
}

export type GrowthHotBuyShareInput = {
  ticker: string;
  projectName: string;
  projectSlug: string;
  buyerNames: string[];
  origin: string;
  pctOfActive?: number;
  detailLine?: string;
  scoutHighlight?: string | null;
  scoutThesis?: string | null;
  summary?: string | null;
  communitySnippets?: string[];
};

/** SAID-style growth thread — named buyers, thesis, multi-link story for X composer. */
export function buildGrowthHotBuyThread(input: GrowthHotBuyShareInput): string {
  const base = input.origin.replace(/\/$/, '');
  const names =
    input.buyerNames.length === 0
      ? 'Traders'
      : input.buyerNames.length <= 3
        ? input.buyerNames.join(', ')
        : `${input.buyerNames.slice(0, 2).join(', ')} +${input.buyerNames.length - 2} more`;
  const thesis = pickHotBuyThesis(input);
  const pct =
    input.pctOfActive != null && input.pctOfActive > 0
      ? `${Math.round(input.pctOfActive * 100)}% of active traders in 24h`
      : null;

  const lines = [
    '🔥 Hot paper buy on Doxxed Crypto',
    '',
    `${names} bought $${input.ticker.toUpperCase()} (${input.projectName})`,
  ];
  if (pct) lines.push(pct);
  if (input.detailLine?.trim()) lines.push(input.detailLine.trim());
  if (thesis) {
    lines.push('', 'Scout thesis:', `"${thesis}"`);
  }
  const snippets = (input.communitySnippets ?? [])
    .map((s) => paraphraseShareSnippet(s, 80))
    .filter(Boolean)
    .slice(0, 2);
  if (snippets.length > 0) {
    lines.push('', 'Traders say:', snippets.map((s) => `• ${s}`).join('\n'));
  }
  lines.push(
    '',
    'Explore:',
    `${base}/project/${input.projectSlug}`,
    `${base}/leaderboard`,
    `${base}/founder-node`,
    '',
    '#Crypto #FounderOS #ProofOfConviction @DoxxedCrypto',
  );
  return lines.join('\n');
}

/** Single-tweet growth post (≤280) with project URL for intent. */
export function buildGrowthHotBuyTweet(input: GrowthHotBuyShareInput): string {
  const thread = buildGrowthHotBuyThread(input);
  const firstBlock = thread.split('\n\nExplore:')[0]?.trim() ?? thread;
  const withLinks = `${firstBlock}\n\nSee who bought · leaderboard · founder vault 👇\n#ProofOfConviction @DoxxedCrypto`;
  if (withLinks.length <= 280) return withLinks;
  return buildHotBuyShareMessage(input);
}

export function shareImageFilename(pnlOrRoi: number): string {
  const side = pnlOrRoi >= 0 ? 'pump' : 'dump';
  return `dcf-${side}-flex.png`;
}
