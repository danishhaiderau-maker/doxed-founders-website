/** Live copy relay capacity — OPEN + PENDING must stay <= showcase max_active_signals. */

export type CopyRelayCapacitySnapshot = {
  activeOpen: number;
  activePending: number;
  totalActive: number;
  capacityLimit: number;
  slotsRemaining: number;
  source: 'showcase_dashboard' | 'env_override' | 'default';
  showcaseMaxActiveSignals: number | null;
  updatedAt: string;
  lastRejectReason: string | null;
  lastRejectAt: string | null;
};

export function emptyCopyRelayCapacity(limit = 3): CopyRelayCapacitySnapshot {
  return {
    activeOpen: 0,
    activePending: 0,
    totalActive: 0,
    capacityLimit: limit,
    slotsRemaining: limit,
    source: 'default',
    showcaseMaxActiveSignals: null,
    updatedAt: new Date().toISOString(),
    lastRejectReason: null,
    lastRejectAt: null,
  };
}

export function buildCopyRelayCapacity(input: {
  open: number;
  pending: number;
  capacityLimit: number;
  source: CopyRelayCapacitySnapshot['source'];
  showcaseMaxActiveSignals?: number | null;
  lastRejectReason?: string | null;
  lastRejectAt?: string | null;
}): CopyRelayCapacitySnapshot {
  const totalActive = input.open + input.pending;
  return {
    activeOpen: input.open,
    activePending: input.pending,
    totalActive,
    capacityLimit: input.capacityLimit,
    slotsRemaining: Math.max(0, input.capacityLimit - totalActive),
    source: input.source,
    showcaseMaxActiveSignals: input.showcaseMaxActiveSignals ?? null,
    updatedAt: new Date().toISOString(),
    lastRejectReason: input.lastRejectReason ?? null,
    lastRejectAt: input.lastRejectAt ?? null,
  };
}

export function isCapacityViolation(
  open: number,
  pending: number,
  limit: number,
): boolean {
  return open + pending > limit;
}
