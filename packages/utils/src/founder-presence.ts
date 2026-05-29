export type FounderPresenceLevel =
  | 'UNVERIFIED'
  | 'PUBLIC_FOUNDER'
  | 'VERIFIED_BUILDER'
  | 'TRANSPARENT_FOUNDER'
  | 'PROVEN_FOUNDER';

export const PRESENCE_LEVEL_META: Record<
  FounderPresenceLevel,
  { label: string; emoji: string; color: string; description: string }
> = {
  UNVERIFIED: {
    label: 'Unverified',
    emoji: '⚪',
    color: 'zinc',
    description: 'No public founder presence verified yet.',
  },
  PUBLIC_FOUNDER: {
    label: 'Public Founder',
    emoji: '🟡',
    color: 'amber',
    description: 'Public video introduction and project explanation.',
  },
  VERIFIED_BUILDER: {
    label: 'Verified Builder',
    emoji: '🟢',
    color: 'emerald',
    description: 'Build updates, GitHub connected, consistent activity.',
  },
  TRANSPARENT_FOUNDER: {
    label: 'Transparent Founder',
    emoji: '🔵',
    color: 'sky',
    description: 'Public Q&A, community engagement, roadmap updates.',
  },
  PROVEN_FOUNDER: {
    label: 'Proven Founder',
    emoji: '🟣',
    color: 'violet',
    description: 'Successfully shipped products with historical execution.',
  },
};

export type FounderReputationInput = {
  videoCount: number;
  buildPostCount: number;
  githubConnected: boolean;
  hasPublicQa: boolean;
  roadmapDoneCount: number;
  simulatedDemandUsd: number;
  daysBuilding: number;
  buildStreakDays: number;
};

export type FounderReputationBreakdown = {
  total: number;
  videoActivity: number;
  githubActivity: number;
  communityTrust: number;
  productDelivery: number;
  consistency: number;
};

export function computeFounderReputation(input: FounderReputationInput): FounderReputationBreakdown {
  const videoActivity = Math.min(
    100,
    input.videoCount * 25 + (input.hasPublicQa ? 25 : 0),
  );
  const githubActivity = input.githubConnected
    ? Math.min(100, 40 + input.buildPostCount * 5)
    : Math.min(60, input.buildPostCount * 8);
  const communityTrust = Math.min(100, input.buildPostCount * 6 + (input.hasPublicQa ? 20 : 0));
  const productDelivery = Math.min(100, input.roadmapDoneCount * 20 + (input.simulatedDemandUsd > 100_000 ? 30 : 0));
  const consistency = Math.min(100, input.buildStreakDays * 2 + Math.min(input.daysBuilding, 60));

  const total = Math.round(
    videoActivity * 0.25 +
      githubActivity * 0.25 +
      communityTrust * 0.2 +
      productDelivery * 0.2 +
      consistency * 0.1,
  );

  return {
    total,
    videoActivity: Math.round(videoActivity),
    githubActivity: Math.round(githubActivity),
    communityTrust: Math.round(communityTrust),
    productDelivery: Math.round(productDelivery),
    consistency: Math.round(consistency),
  };
}

export type PresenceComputeInput = {
  videoCount: number;
  buildPostCount: number;
  githubConnected: boolean;
  hasPublicQa: boolean;
  roadmapDoneCount: number;
  shippedProducts: number;
};

export function computePresenceLevel(input: PresenceComputeInput): FounderPresenceLevel {
  if (input.shippedProducts >= 1 && input.roadmapDoneCount >= 3) {
    return 'PROVEN_FOUNDER';
  }
  if (input.hasPublicQa && input.buildPostCount >= 5) {
    return 'TRANSPARENT_FOUNDER';
  }
  if (input.githubConnected && input.buildPostCount >= 2) {
    return 'VERIFIED_BUILDER';
  }
  if (input.videoCount >= 1) {
    return 'PUBLIC_FOUNDER';
  }
  return 'UNVERIFIED';
}

export const JOURNEY_STAGES = [
  { key: 'IDEA', label: 'Idea' },
  { key: 'PROTOTYPE', label: 'Prototype' },
  { key: 'MVP', label: 'MVP' },
  { key: 'BETA', label: 'Beta' },
  { key: 'DEMAND_VALIDATED', label: 'Demand validated' },
  { key: 'LAUNCH', label: 'Launch' },
  { key: 'REVENUE', label: 'Revenue' },
] as const;
