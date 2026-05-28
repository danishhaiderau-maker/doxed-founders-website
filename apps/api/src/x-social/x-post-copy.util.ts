import { siteUrl } from './x-oauth1';

export type TraderConvictionInput = {
  displayName: string;
  userId: string;
  projectName: string;
  ticker: string;
  slug: string;
  investedUsd: number;
  pnlUsd: number;
  pnlPercent: number;
  thesis: string | null;
  founderName: string | null;
  founderHandle: string | null;
};

function fmtUsd(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1000) return `$${abs.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
  return `$${abs.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

function fmtSignedUsd(value: number): string {
  const sign = value >= 0 ? '+' : '−';
  return `${sign}${fmtUsd(value)}`;
}

function fmtSignedPercent(value: number): string {
  const sign = value >= 0 ? '+' : '−';
  return `${sign}${Math.abs(Math.round(value))}%`;
}

function clip(text: string, max: number): string {
  const t = text.replace(/\s+/g, ' ').trim();
  if (t.length <= max) return t;
  return `${t.slice(0, max - 1)}…`;
}

export function formatTraderConvictionPost(input: TraderConvictionInput): string {
  const win = input.pnlPercent >= 0;
  const header = win ? '🚀 Conviction rewarded' : '📉 Conviction under pressure';
  const moveEmoji = win ? '📈🟢' : '📉🔴';
  const tail = win ? '🚀' : '⬇️';

  const lines = [
    header,
    '',
    `$${input.ticker} · ${input.projectName}`,
    `💰 ${fmtUsd(input.investedUsd)} deployed on this trade`,
    `${moveEmoji} ${fmtSignedPercent(input.pnlPercent)} (${fmtSignedUsd(input.pnlUsd)}) on this position ${tail}`,
    `(Not whole portfolio — this position only)`,
    '',
    `👤 ${input.displayName}`,
  ];

  if (input.founderName) {
    lines.push(`✅ Doxxed founder: ${input.founderName}`);
  }
  if (input.founderHandle) {
    lines.push(`🐦 Founder signal quoted below ↓`);
  }

  if (input.thesis?.trim()) {
    lines.push('', '💬 Trader thesis:', `"${clip(input.thesis, 110)}"`);
  }

  lines.push(
    '',
    `${siteUrl()}/portfolio/${input.userId}`,
    `${siteUrl()}/project/${input.slug}`,
    'Proof of Conviction · @Bitbro4crypto',
  );

  return lines.join('\n');
}

export function formatFounderRepostPost(meta: {
  founderName: string;
  projectName: string;
  projectSlug: string;
  ticker?: string | null;
}): string {
  const tickerLine = meta.ticker ? `$${meta.ticker} · ` : '';
  return [
    '🎯 Doxxed founder update',
    '',
    `${tickerLine}${meta.projectName}`,
    `${meta.founderName} just shared an update — quoted below 👇`,
    '',
    `Track live conviction → ${siteUrl()}/project/${meta.projectSlug}`,
    '#ProofOfConviction',
  ].join('\n');
}

export function formatTrendingBuysPost(input: {
  brandHandle: string;
  projectName: string;
  ticker: string;
  slug: string;
  founderName: string | null;
  buyerCount: number;
  windowHours: number;
  totalInvestedUsd: number;
}): string {
  const lines = [
    '🐋 CONVICTION CLUSTER',
    '',
    `$${input.ticker} · ${input.projectName}`,
    `${input.buyerCount} traders paper-bought in ${input.windowHours}h`,
  ];
  if (input.totalInvestedUsd > 0) {
    lines.push(`💰 ~${fmtUsd(input.totalInvestedUsd)} paper capital deployed`);
  }
  if (input.founderName) {
    lines.push(`✅ Doxxed founder: ${input.founderName}`);
  }
  lines.push(
    '',
    `@${input.brandHandle} whale tracker`,
    `${siteUrl()}/project/${input.slug}`,
    '#ProofOfConviction #crypto',
  );
  return lines.join('\n');
}

export function trimTweet(text: string, max = 270): string {
  const cleaned = text.replace(/\r\n/g, '\n').trim();
  if (cleaned.length <= max) return cleaned;
  return `${cleaned.slice(0, max - 1)}…`;
}
