/** Compare a listing application to an existing curated project for admin relist review */

export function normalizeContractAddress(addr: string | null | undefined): string | null {
  if (!addr?.trim()) return null;
  return addr.trim().toLowerCase();
}

export type ListingRelistMatchType = 'contract' | 'ticker' | 'slug';

export type ListingRelistField = {
  key: string;
  label: string;
  previous: string | null;
  next: string | null;
  changed: boolean;
};

export type ListingRelistSnapshot = {
  projectName: string | null;
  ticker: string | null;
  websiteUrl: string | null;
  docsUrl: string | null;
  whitepaperUrl: string | null;
  contractAddress: string | null;
  dexscreenerUrl: string | null;
  logoUrl: string | null;
  telegramUrl: string | null;
  founderName: string | null;
  founderLinkedIn: string | null;
  founderTwitter: string | null;
  founderGithub: string | null;
  projectGithubUrl: string | null;
  founderVideoUrl: string | null;
  founderInterviewUrl: string | null;
  companyDetails: string | null;
  auditUrl: string | null;
  summary: string | null;
  marketCap: string | null;
  priceUsd: string | null;
};

const FIELD_DEFS: { key: keyof ListingRelistSnapshot; label: string }[] = [
  { key: 'projectName', label: 'Project name' },
  { key: 'ticker', label: 'Ticker' },
  { key: 'contractAddress', label: 'Contract' },
  { key: 'dexscreenerUrl', label: 'DexScreener' },
  { key: 'websiteUrl', label: 'Website' },
  { key: 'logoUrl', label: 'Logo URL' },
  { key: 'summary', label: 'Summary' },
  { key: 'companyDetails', label: 'Description / details' },
  { key: 'founderName', label: 'Founder name' },
  { key: 'founderTwitter', label: 'Founder X' },
  { key: 'founderLinkedIn', label: 'LinkedIn' },
  { key: 'founderGithub', label: 'Founder GitHub' },
  { key: 'projectGithubUrl', label: 'Project GitHub repo' },
  { key: 'founderVideoUrl', label: 'Proof video' },
  { key: 'founderInterviewUrl', label: 'Interview' },
  { key: 'telegramUrl', label: 'Telegram' },
  { key: 'docsUrl', label: 'Docs' },
  { key: 'whitepaperUrl', label: 'Whitepaper' },
  { key: 'auditUrl', label: 'Audit' },
  { key: 'marketCap', label: 'Market cap' },
  { key: 'priceUsd', label: 'Price' },
];

function displayVal(v: string | null | undefined): string | null {
  if (v == null) return null;
  const t = String(v).trim();
  return t || null;
}

function truncate(s: string, max = 120): string {
  return s.length <= max ? s : `${s.slice(0, max)}…`;
}

export function snapshotFromApplication(input: {
  projectName?: string | null;
  ticker?: string | null;
  websiteUrl?: string | null;
  docsUrl?: string | null;
  whitepaperUrl?: string | null;
  contractAddress?: string | null;
  dexscreenerUrl?: string | null;
  logoUrl?: string | null;
  telegramUrl?: string | null;
  founderName?: string | null;
  founderLinkedIn?: string | null;
  founderTwitter?: string | null;
  founderGithub?: string | null;
  projectGithubUrl?: string | null;
  founderVideoUrl?: string | null;
  founderInterviewUrl?: string | null;
  companyDetails?: string | null;
  auditUrl?: string | null;
  summary?: string | null;
  marketPreview?: Record<string, unknown> | null;
}): ListingRelistSnapshot {
  const mp = input.marketPreview;
  return {
    projectName: displayVal(input.projectName),
    ticker: displayVal(input.ticker)?.toUpperCase() ?? null,
    websiteUrl: displayVal(input.websiteUrl),
    docsUrl: displayVal(input.docsUrl),
    whitepaperUrl: displayVal(input.whitepaperUrl),
    contractAddress: displayVal(input.contractAddress),
    dexscreenerUrl: displayVal(input.dexscreenerUrl),
    logoUrl: displayVal(input.logoUrl),
    telegramUrl: displayVal(input.telegramUrl),
    founderName: displayVal(input.founderName),
    founderLinkedIn: displayVal(input.founderLinkedIn),
    founderTwitter: displayVal(input.founderTwitter),
    founderGithub: displayVal(input.founderGithub),
    projectGithubUrl: displayVal(input.projectGithubUrl),
    founderVideoUrl: displayVal(input.founderVideoUrl),
    founderInterviewUrl: displayVal(input.founderInterviewUrl),
    companyDetails: displayVal(input.companyDetails),
    auditUrl: displayVal(input.auditUrl),
    summary: displayVal(input.summary),
    marketCap:
      mp && typeof mp.marketCap === 'number' ? String(mp.marketCap) : null,
    priceUsd:
      mp && mp.priceUsd != null ? String(mp.priceUsd) : null,
  };
}

export function buildListingRelistDiff(
  previous: ListingRelistSnapshot,
  next: ListingRelistSnapshot,
): ListingRelistField[] {
  return FIELD_DEFS.map(({ key, label }) => {
    const prev = displayVal(previous[key]);
    const nxt = displayVal(next[key]);
    const changed = prev !== nxt && !(prev == null && nxt == null);
    return {
      key,
      label,
      previous: prev ? truncate(prev) : null,
      next: nxt ? truncate(nxt) : null,
      changed,
    };
  });
}

export function countChangedFields(fields: ListingRelistField[]): number {
  return fields.filter((f) => f.changed).length;
}
