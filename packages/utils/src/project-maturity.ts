/**
 * Trust-first project maturity stages (product vision).
 * Maps from legacy `ProjectLifecycleStage` until schema migration.
 */
export const PROJECT_MATURITY_STAGES = [
  'IDEA',
  'BUILDING',
  'VALIDATED',
  'COMMUNITY',
  'READY',
  'LAUNCHING',
  'TRADING',
  'GROWING',
] as const;

export type ProjectMaturity = (typeof PROJECT_MATURITY_STAGES)[number];

export const PROJECT_MATURITY_META: Record<
  ProjectMaturity,
  { label: string; shortLabel: string; description: string }
> = {
  IDEA: {
    label: 'Idea',
    shortLabel: 'Idea',
    description: 'Concept listed — not yet building in public',
  },
  BUILDING: {
    label: 'Building',
    shortLabel: 'Building',
    description: 'Shipping prototypes, MVP, or beta in Founder OS',
  },
  VALIDATED: {
    label: 'Validated',
    shortLabel: 'Validated',
    description: 'Trust Center review and weighted community signals underway',
  },
  COMMUNITY: {
    label: 'Community',
    shortLabel: 'Community',
    description: 'Followers, scouts, and validators engaged — demand forming',
  },
  READY: {
    label: 'Launch Ready',
    shortLabel: 'Ready',
    description: 'Launch qualification gates passed — eligible for Proof Raise',
  },
  LAUNCHING: {
    label: 'Founder Graduation',
    shortLabel: 'Graduating',
    description: 'Proof Raise window open — allocation registration, not a token sale',
  },
  TRADING: {
    label: 'Trading',
    shortLabel: 'Trading',
    description: 'Graduated on Founder Exchange — live swap with trust metadata',
  },
  GROWING: {
    label: 'Growing',
    shortLabel: 'Growing',
    description: 'Post-graduation traction — builders, scouts, and liquidity expanding',
  },
};

/** Map legacy Prisma lifecycle stage → vision maturity stage. */
export function mapLifecycleToMaturity(
  lifecycleStage: string,
  opts?: { isLiveToken?: boolean; hasActiveRaise?: boolean },
): ProjectMaturity {
  const { isLiveToken, hasActiveRaise } = opts ?? {};

  if (isLiveToken && (lifecycleStage === 'LIVE_TRADING' || lifecycleStage === 'TOKEN_LAUNCH')) {
    return lifecycleStage === 'LIVE_TRADING' ? 'GROWING' : 'TRADING';
  }
  if (hasActiveRaise || lifecycleStage === 'SIMULATED_RAISE') return 'LAUNCHING';
  if (lifecycleStage === 'LAUNCH_READY') return 'READY';
  if (lifecycleStage === 'DEMAND_VALIDATION') return 'COMMUNITY';
  if (lifecycleStage === 'TOKEN_LAUNCH') return 'TRADING';
  if (lifecycleStage === 'LIVE_TRADING') return 'GROWING';
  if (['BETA', 'MVP'].includes(lifecycleStage)) return 'VALIDATED';
  if (['PROTOTYPE', 'BRAINSTORMING'].includes(lifecycleStage)) return 'BUILDING';
  return 'IDEA';
}

export function getProjectMaturityLabel(maturity: ProjectMaturity): string {
  return PROJECT_MATURITY_META[maturity].label;
}
