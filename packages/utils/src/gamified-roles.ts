export type GamifiedRoleId =
  | 'admin'
  | 'architect'
  | 'operator'
  | 'builder'
  | 'trader'
  | 'scout'
  | 'explorer';

export type GamifiedRole = {
  id: GamifiedRoleId;
  label: string;
  description: string;
  color: string;
  badge?: string;
};

export const GAMIFIED_ROLES: Record<GamifiedRoleId, GamifiedRole> = {
  admin: {
    id: 'admin',
    label: 'Platform Admin',
    description: 'Full platform administration access.',
    color: 'rose',
  },
  architect: {
    id: 'architect',
    label: 'Architect',
    description: 'Elite founder with proven execution and high reputation.',
    color: 'violet',
    badge: 'Builder Verified',
  },
  operator: {
    id: 'operator',
    label: 'Operator',
    description: 'High-reputation founder shipping in public.',
    color: 'sky',
    badge: 'Builder Verified',
  },
  builder: {
    id: 'builder',
    label: 'Builder',
    description: 'Verified founder with video and build activity.',
    color: 'emerald',
    badge: 'Builder Verified',
  },
  trader: {
    id: 'trader',
    label: 'Trader',
    description: 'Active conviction-based paper trader.',
    color: 'amber',
  },
  scout: {
    id: 'scout',
    label: 'Scout',
    description: 'Active community participant and listing scout.',
    color: 'cyan',
  },
  explorer: {
    id: 'explorer',
    label: 'Explorer',
    description: 'Default role — discover projects and earn your path.',
    color: 'zinc',
  },
};

export type ResolveGamifiedRoleInput = {
  platformRole?: 'USER' | 'ADMIN' | string;
  progressTier?: string;
  reputationPoints?: number;
  paperTradeCount?: number;
  listingVoteCount?: number;
  founder?: {
    presenceLevel?: string;
    videoUrl?: string | null;
    reputationScore?: number;
    buildPostCount?: number;
  } | null;
};

export function resolveGamifiedRole(input: ResolveGamifiedRoleInput): GamifiedRole {
  if (input.platformRole === 'ADMIN') {
    return GAMIFIED_ROLES.admin;
  }

  const founder = input.founder;
  const presence = founder?.presenceLevel ?? 'UNVERIFIED';
  const founderRep = founder?.reputationScore ?? 0;
  const hasVideo = Boolean(founder?.videoUrl);
  const isVerifiedBuilder =
    presence === 'VERIFIED_BUILDER' ||
    presence === 'TRANSPARENT_FOUNDER' ||
    presence === 'PROVEN_FOUNDER';

  if (founder && (isVerifiedBuilder || (hasVideo && (founder.buildPostCount ?? 0) > 0))) {
    if (presence === 'PROVEN_FOUNDER' || founderRep >= 80) {
      return GAMIFIED_ROLES.architect;
    }
    if (founderRep >= 50 || presence === 'TRANSPARENT_FOUNDER') {
      return GAMIFIED_ROLES.operator;
    }
    return GAMIFIED_ROLES.builder;
  }

  if (founder && hasVideo) {
    return { ...GAMIFIED_ROLES.builder, badge: 'Public Founder' };
  }

  if ((input.paperTradeCount ?? 0) >= 5 || input.progressTier === 'TRADER') {
    return GAMIFIED_ROLES.trader;
  }

  if (
    (input.listingVoteCount ?? 0) >= 3 ||
    input.progressTier === 'COMMUNITY_CONTRIBUTOR' ||
    (input.reputationPoints ?? 0) >= 200
  ) {
    return GAMIFIED_ROLES.scout;
  }

  return GAMIFIED_ROLES.explorer;
}

export function gamifiedRoleShortLabel(role: GamifiedRole): string {
  return role.label;
}
