import { Injectable } from '@nestjs/common';
import { Prisma, RoutingDecision } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import type {
  OutcomeUpdate,
  RecordInput,
} from './flight-recorder.types';

/**
 * Flight Recorder — kernel service backed by the Prisma `RoutingDecision`
 * model. Writes one row per routing decision (cache hit, partial, or miss)
 * so the Learning Engine (Phase 4) can later train on it.
 */
@Injectable()
export class FlightRecorderService {
  constructor(private readonly prisma: PrismaService) {}

  /**
   * Insert a routing-decision row. Returns the persisted row (so the
   * Routing Engine can hand it back to callers / log correlation IDs).
   */
  async record(input: RecordInput): Promise<RoutingDecision> {
    return this.prisma.routingDecision.create({
      data: {
        requestId: input.requestId,
        userId: input.userId,
        workspaceId: input.workspaceId ?? null,
        intent: input.intent,
        profile: input.profile,
        candidates: input.candidates as Prisma.JsonArray,
        chosenProvider: input.chosenProvider,
        chosenModel: input.chosenModel,
        cacheLevel: input.cacheLevel,
        cacheKey: input.cacheKey ?? null,
        promptHash: input.promptHash,
        tokenCountPrompt: input.tokenCountPrompt ?? null,
        tokenCountCompletion: input.tokenCountCompletion ?? null,
        latencyMs: input.latencyMs ?? null,
        costUsd: input.costUsd ?? null,
      },
    });
  }

  /**
   * Patch the outcome-signal columns on an existing row. Called async after
   * the response is consumed (accept / retry / edit / rating).
   */
  async updateOutcome(
    requestId: string,
    outcome: OutcomeUpdate,
  ): Promise<void> {
    const data: Prisma.RoutingDecisionUpdateManyMutationInput = {};
    if (outcome.accepted !== undefined) data.accepted = outcome.accepted;
    if (outcome.retried !== undefined) data.retried = outcome.retried;
    if (outcome.edited !== undefined) data.edited = outcome.edited;
    if (outcome.rating !== undefined) data.rating = outcome.rating;

    if (Object.keys(data).length === 0) return;

    // RoutingDecision has no unique constraint on requestId — update the
    // most recent matching row(s).
    await this.prisma.routingDecision.updateMany({
      where: { requestId },
      data,
    });
  }

  /**
   * Patch the usage columns (latency / cost / token counts) onto the row
   * that the Routing Engine wrote at decision time. Used by the AI Proxy
   * on the v2 path so we don't write a duplicate row after the request
   * completes — the engine's row gets enriched in place.
   */
  async updateUsage(
    requestId: string,
    usage: {
      latencyMs?: number;
      costUsd?: number | null;
      tokenCountPrompt?: number;
      tokenCountCompletion?: number;
    },
  ): Promise<void> {
    const data: Prisma.RoutingDecisionUpdateManyMutationInput = {};
    if (usage.latencyMs !== undefined) data.latencyMs = usage.latencyMs;
    if (usage.costUsd !== undefined) data.costUsd = usage.costUsd;
    if (usage.tokenCountPrompt !== undefined) data.tokenCountPrompt = usage.tokenCountPrompt;
    if (usage.tokenCountCompletion !== undefined) data.tokenCountCompletion = usage.tokenCountCompletion;

    if (Object.keys(data).length === 0) return;

    await this.prisma.routingDecision.updateMany({
      where: { requestId },
      data,
    });
  }

  /**
   * Read recent decisions, most-recent-first. Used by the Learning Engine
   * and by admin / observability surfaces.
   */
  async findRecent(filter: {
    userId?: string;
    limit?: number;
  }): Promise<RoutingDecision[]> {
    return this.prisma.routingDecision.findMany({
      where: filter.userId ? { userId: filter.userId } : undefined,
      orderBy: { createdAt: 'desc' },
      take: filter.limit ?? 50,
    });
  }
}
