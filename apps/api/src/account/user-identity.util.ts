import {
  formatPublicAccountLabel,
  parseMessagingLookupQuery,
  resolvePublicIdentity,
  userHasTwitterConnected,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';

export type UserIdentityRow = {
  id: string;
  name?: string | null;
  email?: string | null;
  platformHandle?: string | null;
  twitterHandle?: string | null;
  oauthAccounts?: { provider: string }[];
};

export function buildUserIdentity(user: UserIdentityRow) {
  const hasTwitter = userHasTwitterConnected({
    twitterHandle: user.twitterHandle,
    oauthAccounts: user.oauthAccounts,
  });
  const identity = resolvePublicIdentity({
    userId: user.id,
    name: user.name,
    email: user.email,
    platformHandle: user.platformHandle,
    twitterHandle: user.twitterHandle,
    hasTwitterConnected: hasTwitter,
  });
  return { identity, hasTwitter };
}

export function labelForUser(user: UserIdentityRow): string {
  const { identity } = buildUserIdentity(user);
  return identity.primaryLabel;
}

/** Legacy helper — prefer labelForUser / buildUserIdentity. */
export function formatUserPublicLabel(
  name?: string | null,
  email?: string | null,
  platformHandle?: string | null,
  twitterHandle?: string | null,
  hasTwitterConnected?: boolean,
): string {
  return formatPublicAccountLabel(name, email, platformHandle, twitterHandle, {
    hasTwitterConnected,
  });
}

const identitySelect = {
  id: true,
  name: true,
  email: true,
  platformHandle: true,
  twitterHandle: true,
  oauthAccounts: { select: { provider: true }, take: 3 },
} as const;

export async function findUserByMessagingQuery(prisma: PrismaService, raw: string) {
  const parsed = parseMessagingLookupQuery(raw);
  if (!parsed.value && parsed.kind !== 'userId') return null;

  if (parsed.kind === 'userId') {
    return prisma.user.findUnique({ where: { id: parsed.value }, select: identitySelect });
  }

  if (parsed.kind === 'shortTag') {
    const tag = parsed.value.toLowerCase();
    const matches = await prisma.user.findMany({
      where: { id: { endsWith: tag } },
      select: identitySelect,
      take: 5,
    });
    return matches.length === 1 ? matches[0]! : null;
  }

  if (parsed.kind === 'twitter' || parsed.kind === 'twitterWithTag') {
    const handle = parsed.value.toLowerCase();
    const candidates = await prisma.user.findMany({
      where: {
        OR: [
          { twitterHandle: { equals: handle, mode: 'insensitive' } },
          { twitterHandle: handle },
        ],
      },
      select: identitySelect,
      take: 10,
    });
    if (parsed.kind === 'twitterWithTag' && parsed.shortTag) {
      const tag = parsed.shortTag.toLowerCase();
      const narrowed = candidates.filter((u) => u.id.toLowerCase().endsWith(tag));
      if (narrowed.length === 1) return narrowed[0]!;
      if (narrowed.length > 1) return null;
    }
    return candidates.length === 1 ? candidates[0]! : candidates[0] ?? null;
  }

  return prisma.user.findUnique({
    where: { platformHandle: parsed.value },
    select: identitySelect,
  });
}
