import { Injectable } from '@nestjs/common';
import { CapabilityRegistryService } from '../capability-registry/capability-registry.service';
import type {
  AiRuntimeIntent,
  CapabilityRow,
  ExecutionProfile,
} from '../capability-registry/capability-registry.types';
import { FlightRecorderService } from '../flight-recorder/flight-recorder.service';
import { RoutingEngineCache } from './routing-engine.cache';
import { ExecutionProfileService } from './execution-profile.service';
import type {
  RoutingDecision,
  RoutingRequest,
} from './routing-engine.types';
import { PROFILE_WEIGHTS } from './routing-engine.types';

/**
 * Routing Engine v2 — the 3-layer pipeline (docs/KERNEL.md §6):
 *
 *   Layer 1: Cache lookup (RoutingEngineCache)
 *   Layer 2: Capability gate (CapabilityRegistryService.findBestForIntent)
 *   Layer 3: Intent + cost-latency scoring (this.score, weighted by profile)
 *
 * Every decision is logged to the Flight Recorder regardless of cache state,
 * so the Learning Engine (Phase 4) can later refine the reputation fields
 * on the Capability rows.
 *
 * This replaces the legacy ModelRouterService v1 once the AI Gateway is
 * rewired to call here (handled by the parent agent / app.module.ts owner).
 */
@Injectable()
export class RoutingEngineService {
  constructor(
    private readonly capabilityRegistry: CapabilityRegistryService,
    private readonly flightRecorder: FlightRecorderService,
    private readonly profileService: ExecutionProfileService,
    private readonly cache: RoutingEngineCache,
  ) {}

  async route(request: RoutingRequest): Promise<RoutingDecision> {
    // Layer 1: cache lookup.
    const cacheKey = this.cache.computeKey(request.prompt);
    const cached = this.cache.get(cacheKey);
    if (cached) {
      const resolvedProfile = await this.profileService.getProfile(
        request.workspaceId,
      );
      const decision: RoutingDecision = {
        ...cached,
        requestId: request.requestId,
        cacheLevel: 'hit',
        cacheKey,
      };
      // Fire-and-forget; the gateway should not block on the recorder.
      void this.flightRecorder.record({
        requestId: request.requestId,
        userId: request.userId,
        workspaceId: request.workspaceId ?? null,
        intent: request.intent,
        profile: resolvedProfile,
        candidates: cached.candidates,
        chosenProvider: cached.chosenProvider,
        chosenModel: cached.chosenModel,
        cacheLevel: 'hit',
        cacheKey,
        promptHash: cacheKey,
      });
      return decision;
    }

    // Layer 2: capability gate + Layer 3: scoring.
    const profile = request.profile ?? (await this.profileService.getProfile(request.workspaceId));
    const candidates = await this.capabilityRegistry.findBestForIntent(
      request.intent,
      request.requirements ?? [],
    );

    if (candidates.length === 0) {
      // No candidate satisfied the capability requirements. We throw a
      // structured error so the AI Gateway can map it to a 4xx without
      // panicking — the cache must NOT be poisoned with an empty decision.
      throw new RoutingInfeasibleError(
        `No capability satisfies intent=${request.intent} requirements=${JSON.stringify(request.requirements ?? [])}`,
      );
    }

    const scored = candidates
      .map((c) => ({
        provider: c.provider,
        model: c.model,
        score: this.score(c, profile, request.intent),
      }))
      .sort((a, b) => b.score - a.score);
    const chosen = scored[0];

    const decision: RoutingDecision = {
      requestId: request.requestId,
      chosenProvider: chosen.provider,
      chosenModel: chosen.model,
      score: chosen.score,
      cacheLevel: 'miss',
      candidates: scored,
    };

    this.cache.set(cacheKey, decision);
    void this.flightRecorder.record({
      requestId: request.requestId,
      userId: request.userId,
      workspaceId: request.workspaceId ?? null,
      intent: request.intent,
      profile,
      candidates: scored,
      chosenProvider: chosen.provider,
      chosenModel: chosen.model,
      cacheLevel: 'miss',
      cacheKey,
      promptHash: cacheKey,
    });

    return { ...decision, cacheKey };
  }

  /**
   * Layer 3 scoring — weighted sum over four normalized axes.
   * Each axis produces a 0..1 number; the profile weights combine them.
   */
  private score(
    c: CapabilityRow,
    profile: ExecutionProfile,
    intent: AiRuntimeIntent,
  ): number {
    const intentScore = this.intentScoreFor(c, intent); // 0..1
    const costScore = Math.max(0, 1 - c.inputCostPer1M / 5); // normalize against $5/1M
    const latencyScore = Math.max(0, 1 - c.latencyP50Ms / 5000); // normalize against 5s
    const reputation = Math.max(0, c.successRate - c.retryRate);
    const w = PROFILE_WEIGHTS[profile];
    return (
      w.intent * intentScore +
      w.cost * costScore +
      w.latency * latencyScore +
      w.reputation * reputation
    );
  }

  private intentScoreFor(c: CapabilityRow, intent: AiRuntimeIntent): number {
    switch (intent) {
      case 'code':
        return c.codeScore;
      case 'reasoning':
        return c.reasoningScore;
      case 'simple_qa':
        return c.simpleQaScore;
      case 'agent':
        return c.agentScore;
      case 'vision':
        return c.visionScore;
    }
  }
}

/**
 * Thrown when Layer 2 (capability gate) filters out every candidate. The
 * AI Gateway should map this to a 422 — the request as written cannot be
 * served by any currently-registered capability.
 */
export class RoutingInfeasibleError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'RoutingInfeasibleError';
  }
}
