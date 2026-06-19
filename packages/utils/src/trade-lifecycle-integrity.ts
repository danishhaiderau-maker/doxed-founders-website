/** Copy relay trade lifecycle completeness — validation metrics for production soak. */

export const LIFECYCLE_CLOSED_REQUIRED = ['ORDER_PLACED', 'FILLED', 'EXIT'] as const;
export const LIFECYCLE_OPEN_REQUIRED = ['ORDER_PLACED', 'FILLED'] as const;

export type TradeLifecycleGap = {
  participantId: string;
  tradeId: string;
  status: string;
  missingStages: string[];
};

export type TradeLifecycleIntegritySnapshot = {
  sampleSize: number;
  completeCount: number;
  incompleteCount: number;
  integrityPct: number;
  recentGaps: TradeLifecycleGap[];
  updatedAt: string;
};

export type CopyRelayLimitChainSnapshot = {
  configuredLimit: number | null;
  relayLimit: number | null;
  executionOpen: number;
  executionPending: number;
  executionTotal: number;
  aligned: boolean;
  source: string | null;
  updatedAt: string;
};

type ParticipantLike = {
  id: string;
  status: string;
  events: Array<{ eventType: string }>;
  cycle?: { tradeId?: string | null } | null;
};

export function assessParticipantLifecycle(p: ParticipantLike): string[] {
  const types = new Set(p.events.map((e) => e.eventType));
  const required =
    p.status === 'OPEN' || p.status === 'PENDING_ENTRY'
      ? LIFECYCLE_OPEN_REQUIRED
      : LIFECYCLE_CLOSED_REQUIRED;
  return required.filter((stage) => !types.has(stage));
}

export function buildTradeLifecycleIntegrity(
  participants: ParticipantLike[],
  maxGaps = 5,
): TradeLifecycleIntegritySnapshot {
  const sample = participants.slice(0, 100);
  const gaps: TradeLifecycleGap[] = [];
  let completeCount = 0;

  for (const p of sample) {
    const missing = assessParticipantLifecycle(p);
    if (!missing.length) {
      completeCount += 1;
      continue;
    }
    if (gaps.length < maxGaps) {
      gaps.push({
        participantId: p.id,
        tradeId: p.cycle?.tradeId?.slice(0, 12) ?? p.id.slice(0, 12),
        status: p.status,
        missingStages: [...missing],
      });
    }
  }

  const sampleSize = sample.length;
  const incompleteCount = sampleSize - completeCount;
  const integrityPct = sampleSize ? Math.round((completeCount / sampleSize) * 1000) / 10 : 100;

  return {
    sampleSize,
    completeCount,
    incompleteCount,
    integrityPct,
    recentGaps: gaps,
    updatedAt: new Date().toISOString(),
  };
}

export function buildCopyRelayLimitChain(input: {
  showcaseMaxActiveSignals?: number | null;
  capacityLimit?: number | null;
  activeOpen?: number;
  activePending?: number;
  source?: string | null;
}): CopyRelayLimitChainSnapshot {
  const configuredLimit = input.showcaseMaxActiveSignals ?? null;
  const relayLimit = input.capacityLimit ?? configuredLimit ?? null;
  const executionOpen = input.activeOpen ?? 0;
  const executionPending = input.activePending ?? 0;
  const executionTotal = executionOpen + executionPending;
  const limit = relayLimit ?? configuredLimit ?? 3;
  const aligned =
    (configuredLimit == null || relayLimit == null || configuredLimit === relayLimit) &&
    executionTotal <= limit;

  return {
    configuredLimit,
    relayLimit,
    executionOpen,
    executionPending,
    executionTotal,
    aligned,
    source: input.source ?? null,
    updatedAt: new Date().toISOString(),
  };
}
