export function isPhase15TrustLayerEnabled(): boolean {
  return process.env.PHASE_15_TRUST_LAYER_ENABLED === 'true';
}

export function isObservatoryEnabled(): boolean {
  return process.env.OBSERVATORY_ENABLED === 'true';
}

export const BLOCKED_REGULATORY_CLASSES = ['CAPITAL_RAISE', 'RESTRICTED'] as const;

export const CLEARED_REGULATORY_CLASSES = ['COMMUNITY', 'UTILITY', 'GOVERNANCE'] as const;

export function isRegulatoryClassBlocked(regulatoryClass: string | null | undefined): boolean {
  if (!regulatoryClass) return true;
  return (BLOCKED_REGULATORY_CLASSES as readonly string[]).includes(regulatoryClass);
}

export function isRegulatoryClassCleared(regulatoryClass: string | null | undefined): boolean {
  if (!regulatoryClass) return false;
  return (CLEARED_REGULATORY_CLASSES as readonly string[]).includes(regulatoryClass);
}
