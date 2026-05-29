/** Raise Room — paper dollar allocation economics */
export const RAISE_ALLOCATION_FEE_PERCENT = 1;
export const TOKEN_LAUNCH_FEE_PERCENT = 0.2;
export const WEEKLY_STIPEND_USD = 500;

export function computeRaiseAllocationFee(deltaUsd: number): number {
  if (deltaUsd <= 0) return 0;
  return Math.round(deltaUsd * RAISE_ALLOCATION_FEE_PERCENT) / 100;
}

export function computeTokenLaunchFee(saleUsd: number): number {
  if (saleUsd <= 0) return 0;
  return Math.round(saleUsd * TOKEN_LAUNCH_FEE_PERCENT) / 100;
}

export type RaiseParticipantExport = {
  userId: string;
  displayName: string;
  amountUsd: number;
  burnedUsd: number;
  walletAddress: string | null;
  slotReserved: boolean;
  allocationSharePercent: number;
};

export function buildParticipantExport(
  allocations: {
    userId: string;
    displayName: string;
    amountUsd: number;
    burnedUsd: number;
    walletAddress: string | null;
    slotReserved: boolean;
  }[],
  communityTokenPercent: number,
): RaiseParticipantExport[] {
  const total = allocations.reduce((s, a) => s + a.amountUsd, 0);
  return allocations
    .filter((a) => a.amountUsd > 0 && a.slotReserved)
    .sort((a, b) => b.amountUsd - a.amountUsd)
    .map((a) => ({
      ...a,
      allocationSharePercent:
        total > 0 ? Math.round((a.amountUsd / total) * communityTokenPercent * 100) / 100 : 0,
    }));
}

export function formatRaiseMomentum(totalAllocated: number, goalUsd: number, participantCount: number): number {
  const fillPct = goalUsd > 0 ? totalAllocated / goalUsd : 0;
  const crowd = Math.min(1, participantCount / 100);
  return Math.min(100, Math.round(fillPct * 70 + crowd * 30));
}
