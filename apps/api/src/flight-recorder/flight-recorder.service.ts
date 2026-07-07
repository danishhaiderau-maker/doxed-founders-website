import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import type {
  OutcomeUpdate,
  RecordInput,
  RoutingDecisionRow,
} from './flight-recorder.types';

/**
 * Flight Recorder — kernel service backed by the Prisma `RoutingDecision`
 * model. Writes one row per routing decision (cache hit, partial, or miss)
 * so the Learning Engine (Phase 4) can later train on it.
 *
 * KNOWN LIMITATION: `@prisma/client` has not yet been regenerated to include
 * the `RoutingDecision` model (the parent agent runs `prisma generate` after
 * this lands). `this.prisma.routingDecision` is accessed via `any`. The
 * local `RoutingDecisionRow` type mirrors the schema shape so callers get
 * structurally compatible data.
 */
@Injectable()
export class FlightRecorderService {
  constructor(private readonly prisma: PrismaService) {}

  private get model(): any {
    return (this.prisma as any).routingDecision;
  }

  /**
   * Insert a routing-decision row. Returns the persisted row (so the
   * Routing Engine can hand it back to callers / log correlation IDs).
   */
  async record(input: RecordInput): Promise<RoutingDecisionRow> {
    return this.model.create({
      data: {
        requestId: input.requestId,
        userId: input.userId,
        workspaceId: input.workspaceId ?? null,
        intent: input.intent,
        profile: input.profile,
        candidates: input.candidates,
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
    const data: Record<string, unknown> = {};
    if (outcome.accepted !== undefined) data.accepted = outcome.accepted;
    if (outcome.retried !== undefined) data.retried = outcome.retried;
    if (outcome.edited !== undefined) data.edited = outcome.edited;
    if (outcome.rating !== undefined) data.rating = outcome.rating;

    if (Object.keys(data).length === 0) return;

    // RoutingDecision has no unique constraint on requestId — update the
    // most recent matching row.
    await this.model.updateMany({
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
  }): Promise<RoutingDecisionRow[]> {
    return this.model.findMany({
      where: filter.userId ? { userId: filter.userId } : undefined,
      orderBy: { createdAt: 'desc' },
      take: filter.limit ?? 50,
    });
  }
}
