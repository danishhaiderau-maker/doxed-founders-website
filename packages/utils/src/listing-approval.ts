export type FounderListingStatus =
  | 'DOXXED'
  | 'VERIFIED'
  | 'UNDOXXED'
  | 'BUILDING_IN_PUBLIC';

export interface ListingApprovalInput {
  dexscreenerUrl?: string | null;
  founderDoxxedStatus?: string | null;
  proofLinkUrl?: string | null;
  founderVideoUrl?: string | null;
  founderInterviewUrl?: string | null;
  founderTwitter?: string | null;
  founderLinkedIn?: string | null;
  founderGithub?: string | null;
  projectGithubUrl?: string | null;
  websiteUrl?: string | null;
  chainSlug?: string | null;
  contractAddress?: string | null;
  founderName?: string | null;
  projectName?: string | null;
}

export function applyProofLinkUrl<T extends ListingApprovalInput>(input: T): T {
  const proof = input.proofLinkUrl?.trim();
  if (!proof) return input;

  const out = { ...input };
  const lower = proof.toLowerCase();

  if ((lower.includes('twitter.com') || lower.includes('x.com')) && !out.founderTwitter?.trim()) {
    out.founderTwitter = proof;
  } else if (lower.includes('linkedin.com') && !out.founderLinkedIn?.trim()) {
    out.founderLinkedIn = proof;
  } else if (
    (lower.includes('youtube.com') ||
      lower.includes('youtu.be') ||
      lower.includes('loom.com') ||
      lower.includes('vimeo.com')) &&
    !out.founderVideoUrl?.trim()
  ) {
    out.founderVideoUrl = proof;
  } else if (!out.founderInterviewUrl?.trim() && !out.founderVideoUrl?.trim()) {
    out.founderInterviewUrl = proof;
  }

  if (lower.includes('github.com') && !out.projectGithubUrl?.trim()) {
    out.projectGithubUrl = proof;
  }

  return out;
}

export function resolveProofLinks(input: ListingApprovalInput): string[] {
  const normalized = applyProofLinkUrl(input);
  return [
    normalized.proofLinkUrl,
    normalized.founderVideoUrl,
    normalized.founderInterviewUrl,
    normalized.founderTwitter,
    normalized.founderLinkedIn,
    normalized.founderGithub,
    normalized.projectGithubUrl,
    normalized.websiteUrl,
  ]
    .map((v) => v?.trim())
    .filter((v): v is string => Boolean(v));
}

export function getPrimaryProofLink(input: ListingApprovalInput): string | null {
  return resolveProofLinks(input)[0] ?? null;
}

export function inferChainFromDexscreenerUrl(url?: string | null): string | null {
  if (!url?.trim()) return null;
  try {
    const segment = new URL(url).pathname.split('/').filter(Boolean)[0]?.toUpperCase();
    if (!segment) return null;
    const map: Record<string, string> = {
      SOLANA: 'SOLANA',
      ETHEREUM: 'ETHEREUM',
      BASE: 'BASE',
      ARBITRUM: 'ARBITRUM',
      POLYGON: 'POLYGON',
      OPTIMISM: 'OPTIMISM',
      AVALANCHE: 'AVALANCHE',
      BSC: 'BNB_CHAIN',
      BNB: 'BNB_CHAIN',
    };
    return map[segment] ?? null;
  } catch {
    return null;
  }
}

export function resolveListingChain(input: ListingApprovalInput): string | null {
  return input.chainSlug?.trim() || inferChainFromDexscreenerUrl(input.dexscreenerUrl) || null;
}

export function founderStatusLabel(status?: string | null): string {
  switch (status) {
    case 'VERIFIED':
      return 'Verified Founder';
    case 'BUILDING_IN_PUBLIC':
      return 'Building in Public';
    case 'UNDOXXED':
      return 'Undoxxed';
    default:
      return 'Doxxed Founder';
  }
}

export function validateListingForApproval(input: ListingApprovalInput): {
  ok: boolean;
  errors: string[];
  warnings: string[];
} {
  const normalized = applyProofLinkUrl(input);
  const errors: string[] = [];
  const warnings: string[] = [];
  const status = (normalized.founderDoxxedStatus ?? 'DOXXED') as FounderListingStatus;

  if (status === 'UNDOXXED') {
    errors.push('Undoxxed founders cannot receive official listings.');
    return { ok: false, errors, warnings };
  }

  if (!normalized.dexscreenerUrl?.trim()) {
    errors.push('DexScreener URL is required.');
  }

  const proofLinks = resolveProofLinks(normalized);

  if (status === 'VERIFIED') {
    const hasVerification = Boolean(
      normalized.founderLinkedIn?.trim() ||
        normalized.founderInterviewUrl?.trim() ||
        normalized.proofLinkUrl?.trim(),
    );
    if (!hasVerification) {
      errors.push('Verified founders require a verification link (LinkedIn or public interview).');
    }
  } else if (proofLinks.length === 0) {
    errors.push(
      'A public proof link is required — X/Twitter, YouTube, interview, podcast, team page, or verification page.',
    );
  }

  if (!resolveListingChain(normalized)) {
    warnings.push('Chain not detected — set chain manually or ensure DexScreener URL includes chain.');
  }

  if (!normalized.founderName?.trim()) {
    warnings.push('Founder name not set — project name will be used on publish.');
  }

  if (!normalized.projectGithubUrl?.trim()) {
    warnings.push(
      'No project GitHub repo — add one to track commits and rank higher in Discover activity.',
    );
  }

  return { ok: errors.length === 0, errors, warnings };
}
