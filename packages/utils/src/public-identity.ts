import { hasTradingDisplayName, maskEmail, normalizeTwitterHandle } from './display-name';

export type PublicIdentityInput = {
  userId: string;
  name?: string | null;
  email?: string | null;
  platformHandle?: string | null;
  twitterHandle?: string | null;
  hasTwitterConnected?: boolean;
};

export type PublicIdentity = {
  userId: string;
  primaryLabel: string;
  secondaryLabel: string | null;
  messagingAddress: string;
  shortTag: string;
  twitterHandle: string | null;
  twitterUrl: string | null;
  platformHandle: string | null;
  hasTwitterConnected: boolean;
};

/** Stable 4-char disambiguator from account id (shown as #A7F2). */
export function messagingShortTag(userId: string): string {
  const clean = userId.replace(/[^a-z0-9]/gi, '');
  const slice = clean.slice(-4);
  return (slice || userId.slice(0, 4)).toUpperCase();
}

export function twitterProfileUrl(handle?: string | null): string | null {
  const normalized = normalizeTwitterHandle(handle);
  if (!normalized) return null;
  return `https://x.com/${normalized}`;
}

export function resolvePublicIdentity(input: PublicIdentityInput): PublicIdentity {
  const twitter = normalizeTwitterHandle(input.twitterHandle);
  const hasTwitter = Boolean(input.hasTwitterConnected ?? twitter);
  const platformHandle = input.platformHandle?.trim() || null;
  const tag = messagingShortTag(input.userId);
  const twitterUrl = twitter ? twitterProfileUrl(twitter) : null;

  let primaryLabel: string;
  let secondaryLabel: string | null = null;
  let messagingAddress: string;

  if (twitter && hasTwitter) {
    primaryLabel = `@${twitter}`;
    messagingAddress = `@${twitter}#${tag}`;
    if (platformHandle && platformHandle !== primaryLabel) {
      secondaryLabel = platformHandle;
    }
  } else if (platformHandle) {
    primaryLabel = platformHandle;
    messagingAddress = `${platformHandle}#${tag}`;
  } else if (hasTradingDisplayName(input.name, input.email)) {
    primaryLabel = input.name!.trim();
    messagingAddress = `${primaryLabel}#${tag}`;
  } else {
    primaryLabel = maskEmail(input.email);
    messagingAddress = `${primaryLabel}#${tag}`;
  }

  return {
    userId: input.userId,
    primaryLabel,
    secondaryLabel,
    messagingAddress,
    shortTag: tag,
    twitterHandle: twitter,
    twitterUrl,
    platformHandle,
    hasTwitterConnected: hasTwitter,
  };
}

/** Parse user lookup queries: @handle, handle#TAG, platform handle, or cuid. */
export function parseMessagingLookupQuery(raw: string): {
  kind: 'userId' | 'shortTag' | 'twitter' | 'platformHandle' | 'twitterWithTag';
  value: string;
  shortTag?: string;
} {
  const trimmed = raw.trim();
  if (!trimmed) return { kind: 'platformHandle', value: '' };

  const hashIdx = trimmed.indexOf('#');
  if (hashIdx > 0) {
    const left = trimmed.slice(0, hashIdx).trim();
    const tag = trimmed.slice(hashIdx + 1).trim().toUpperCase();
    const twitter = normalizeTwitterHandle(left);
    if (twitter && tag.length >= 4) {
      return { kind: 'twitterWithTag', value: twitter, shortTag: tag.slice(0, 8) };
    }
  }

  if (/^cl[a-z0-9]{20,}$/i.test(trimmed)) {
    return { kind: 'userId', value: trimmed };
  }

  const twitter = normalizeTwitterHandle(trimmed);
  if (twitter && (trimmed.startsWith('@') || /^[a-z0-9_]{1,15}$/i.test(trimmed))) {
    return { kind: 'twitter', value: twitter };
  }

  if (/^[A-Z0-9]{4}$/i.test(trimmed) && trimmed.length === 4) {
    return { kind: 'shortTag', value: trimmed.toUpperCase() };
  }

  return { kind: 'platformHandle', value: trimmed };
}

export function buildPaperTradeDeepLink(params: {
  dexscreenerUrl?: string | null;
  amountUsd?: number;
  thesis?: string | null;
  catalyst?: string | null;
  side?: 'BUY' | 'SELL';
  copyFromUserId?: string | null;
}): string {
  const qs = new URLSearchParams();
  if (params.dexscreenerUrl?.trim()) qs.set('dex', params.dexscreenerUrl.trim());
  if (params.amountUsd != null && params.amountUsd > 0) {
    qs.set('amount', String(Math.round(params.amountUsd)));
  }
  if (params.thesis?.trim()) qs.set('thesis', params.thesis.trim().slice(0, 280));
  if (params.catalyst?.trim()) qs.set('catalyst', params.catalyst.trim().slice(0, 120));
  if (params.side) qs.set('side', params.side);
  if (params.copyFromUserId) qs.set('copyFrom', params.copyFromUserId);
  const q = qs.toString();
  return q ? `/paper-trading?${q}` : '/paper-trading';
}
