export type ReductionPhase = 'CLAIMED' | 'SUBMITTING' | 'ACKNOWLEDGED' | 'CONFIRMED';

export type SignedReduction = {
  eventId: string; reductionId: string; eventSeq: number;
  priorQty: number; reducedQty: number; remainingQty: number;
};

export type ParticipantReductionFence = {
  id: string; participantId: string; sourceEventId: string; reductionId: string;
  sourceEventSeq: number; phase: ReductionPhase; beforeQty: number;
  targetQty: number; reduceQty: number; requestToken: string;
  exchangeOrderId?: string | null;
};

export type ReductionFenceRepository = {
  findCollision(participantId: string, source: SignedReduction): Promise<ParticipantReductionFence | null>;
  latestSequence(participantId: string): Promise<number | null>;
  claim(input: Omit<ParticipantReductionFence, 'id' | 'phase' | 'exchangeOrderId'>): Promise<ParticipantReductionFence>;
  transition(id: string, from: ReductionPhase[], to: ReductionPhase, patch?: { exchangeOrderId?: string }): Promise<boolean>;
};

export type DormantReductionVenue = {
  authenticatedPositionQty(): Promise<number>;
  submitReduceOnly(qty: number, requestToken: string): Promise<{ orderId: string }>;
  /** Submit replacement protection first; retire predecessor only after acknowledgement. */
  replaceReduceOnlyProtection(targetQty: number): Promise<boolean>;
  updateConfirmedRemainingQty(targetQty: number): Promise<void>;
};

export function floorVenueQty(qty: number, step: number): number {
  if (!(qty > 0) || !(step > 0)) return 0;
  const units = Math.floor((qty + 1e-12) / step);
  return Number((units * step).toFixed(12));
}

function normalizedQty(qty: number): number {
  return Number(qty.toFixed(12));
}

export function participantReductionPlan(
  source: SignedReduction, liveQty: number, venueStep: number,
): { beforeQty: number; reduceQty: number; targetQty: number } {
  if (!(source.priorQty > 0) || !(source.reducedQty > 0)
    || source.reducedQty > source.priorQty
    || Math.abs(source.priorQty - source.reducedQty - source.remainingQty) > 1e-8
    || !(liveQty > 0)) throw new Error('INVALID_REDUCTION_FRACTION');
  const reduceQty = floorVenueQty(liveQty * (source.reducedQty / source.priorQty), venueStep);
  if (!(reduceQty > 0) || reduceQty > liveQty) throw new Error('VENUE_REDUCTION_ROUNDS_TO_INVALID_QTY');
  return { beforeQty: liveQty, reduceQty, targetQty: normalizedQty(liveQty - reduceQty) };
}

function sameFence(fence: ParticipantReductionFence, source: SignedReduction): boolean {
  return fence.sourceEventId === source.eventId && fence.reductionId === source.reductionId
    && fence.sourceEventSeq === source.eventSeq
    && Math.abs((fence.reduceQty / fence.beforeQty) - (source.reducedQty / source.priorQty)) <= 1e-8
    && Math.abs(fence.beforeQty - fence.reduceQty - fence.targetQty) <= 1e-8;
}

/**
 * Dormant subscriber processor. Nothing wires this into relay ingestion or a
 * Tile allowlist. A future executor may call it only after live-copy approval.
 */
export async function processDormantPositionReduction(input: {
  participantId: string; source: SignedReduction; venueStep: number;
  requestToken: string; repo: ReductionFenceRepository; venue: DormantReductionVenue;
}): Promise<{ phase: ReductionPhase; submitted: boolean; reason: string }> {
  const liveQty = await input.venue.authenticatedPositionQty();
  let fence = await input.repo.findCollision(input.participantId, input.source);
  if (fence && !sameFence(fence, input.source)) throw new Error('REDUCTION_FENCE_CONFLICT');
  if (!fence) {
    const plan = participantReductionPlan(input.source, liveQty, input.venueStep);
    const latest = await input.repo.latestSequence(input.participantId);
    if (latest != null && input.source.eventSeq <= latest) throw new Error('REDUCTION_OUT_OF_ORDER');
    fence = await input.repo.claim({
      participantId: input.participantId, sourceEventId: input.source.eventId,
      reductionId: input.source.reductionId, sourceEventSeq: input.source.eventSeq,
      beforeQty: plan.beforeQty, targetQty: plan.targetQty, reduceQty: plan.reduceQty,
      requestToken: input.requestToken,
    });
  }
  if (fence.phase === 'CONFIRMED') return { phase: 'CONFIRMED', submitted: false, reason: 'IDEMPOTENT_CONFIRMED' };
  if (fence.phase === 'SUBMITTING' || fence.phase === 'ACKNOWLEDGED') {
    if (Math.abs(liveQty - fence.targetQty) <= input.venueStep / 2) {
      if (!await input.venue.replaceReduceOnlyProtection(fence.targetQty)) {
        return { phase: fence.phase, submitted: false, reason: 'TARGET_REACHED_PROTECTION_REPLACEMENT_FAILED' };
      }
      if (!await input.repo.transition(fence.id, ['SUBMITTING', 'ACKNOWLEDGED'], 'CONFIRMED')) {
        return { phase: fence.phase, submitted: false, reason: 'CONFIRM_PERSISTENCE_UNCERTAIN_NO_RESUBMIT' };
      }
      await input.venue.updateConfirmedRemainingQty(fence.targetQty);
      return { phase: 'CONFIRMED', submitted: false, reason: 'RECOVERED_FROM_AUTHENTICATED_TARGET' };
    }
    return { phase: fence.phase, submitted: false, reason: 'UNKNOWN_IN_FLIGHT_NO_RESUBMIT' };
  }
  if (!await input.repo.transition(fence.id, ['CLAIMED'], 'SUBMITTING')) {
    return { phase: 'CLAIMED', submitted: false, reason: 'CLAIM_LOST_NO_SUBMIT' };
  }
  const submitted = await input.venue.submitReduceOnly(fence.reduceQty, fence.requestToken);
  if (!await input.repo.transition(fence.id, ['SUBMITTING'], 'ACKNOWLEDGED', { exchangeOrderId: submitted.orderId })) {
    return { phase: 'SUBMITTING', submitted: true, reason: 'ACK_PERSISTENCE_UNCERTAIN_NO_RESUBMIT' };
  }
  const confirmedQty = await input.venue.authenticatedPositionQty();
  if (Math.abs(confirmedQty - fence.targetQty) > input.venueStep / 2) {
    return { phase: 'ACKNOWLEDGED', submitted: true, reason: 'ACCOUNT_TARGET_UNCONFIRMED' };
  }
  if (!await input.venue.replaceReduceOnlyProtection(fence.targetQty)) {
    return { phase: 'ACKNOWLEDGED', submitted: true, reason: 'TARGET_REACHED_PROTECTION_REPLACEMENT_FAILED' };
  }
  if (!await input.repo.transition(fence.id, ['ACKNOWLEDGED'], 'CONFIRMED')) {
    return { phase: 'ACKNOWLEDGED', submitted: true, reason: 'CONFIRM_PERSISTENCE_UNCERTAIN_NO_RESUBMIT' };
  }
  await input.venue.updateConfirmedRemainingQty(fence.targetQty);
  return { phase: 'CONFIRMED', submitted: true, reason: 'AUTHENTICATED_TARGET_CONFIRMED' };
}

export function terminalCloseQty(confirmedRemainingQty: number, venueStep: number): number {
  return floorVenueQty(confirmedRemainingQty, venueStep);
}
