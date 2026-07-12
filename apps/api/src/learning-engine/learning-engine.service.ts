import { Injectable, Logger } from '@nestjs/common';
import { CapabilityRegistryService } from '../capability-registry/capability-registry.service';
import { PrismaService } from '../prisma/prisma.service';
import type { RoutingDecisionRow } from './learning-engine.types';

/**
 * Learning Engine — Phase 4 kernel service (docs/KERNEL.md §3.6).
 *
 * Consumes Flight Recorder outcomes and adapts routing weights via the
 * Capability Registry's `updateReputation` EMA. The moat:
 *
 *   GLM ↓ founder immediately retries ↓ negative signal
 *     → over time the router stops picking GLM for that founder
 *
 * Phase 4 (this) learns GLOBALLY across all founders. Phase 4.5 will add
 * per-founder personalization; intentionally out of scope here.
 *
 * State: the singleton LearningEngineState row stores the watermark. A local
 * file is not safe on Railway's ephemeral filesystem or when more than one
 * API instance is running.
 */
@Injectable()
export class LearningEngineService {
  private readonly logger = new Logger(LearningEngineService.name);

  private static readonly STATE_ID = 'global';

  /** Treat outcome as a success unless we saw a retry or a heavy edit. */
  static isSuccess(row: {
    retried: boolean | null;
    edited: boolean | null;
  }): boolean {
    return !row.retried && !row.edited;
  }

  constructor(
    private readonly prisma: PrismaService,
    private readonly capabilityRegistry: CapabilityRegistryService,
  ) {}

  /**
   * Read new RoutingDecision rows created since the last rollup, group by
   * `chosenProvider + chosenModel`, compute a per-group outcome score, and
   * push one EMA update per group through `capabilityRegistry.updateReputation`.
   *
   * Input → Decision → Output:
   *   Input:    RoutingDecision rows with `createdAt > lastRollupAt`.
   *   Decision: per-group success = !(retried || edited), aggregated.
   *   Output:   Capability.successRate / retryRate updates + a fresh
   *             lastRollupAt persisted to disk.
   *
   * Returns the counts so the scheduler can log them. Idempotent in the
   * sense that a crashed run will simply re-read the same window next time
   * (the timestamp only advances after a successful pass).
   */
  async rollup(): Promise<{ processed: number; updated: number }> {
    const startedAt = new Date();
    const state = await this.readState();
    const since = state.lastRollupAt ?? new Date(0);

    // -- Input step ----------------------------------------------------------
    // `prisma.routingDecision.findMany` is typed via the generated client.
    // We cast the raw rows to RoutingDecisionRow so this file compiles even
    // if @prisma/client has not been regenerated yet (matches the pattern
    // used elsewhere in the kernel).
    const rows = (await this.prisma.routingDecision.findMany({
      where: { createdAt: { gt: since } },
      take: 50_000,
    })) as unknown as RoutingDecisionRow[];

    if (rows.length === 0) {
      this.logger.log(
        `rollup: no new rows since ${since.toISOString()}; nothing to do.`,
      );
      // Still bump the timestamp so we don't re-scan the same empty window
      // every 6 hours forever.
      await this.writeState({ lastRollupAt: startedAt, processed: 0, updated: 0 });
      return { processed: 0, updated: 0 };
    }

    // -- Decision step -------------------------------------------------------
    // Group rows by capability key and tally successes / failures per group.
    const groups = new Map<string, { successes: number; failures: number }>();
    for (const row of rows) {
      const key = `${row.chosenProvider}\u0000${row.chosenModel}`;
      const success = LearningEngineService.isSuccess(row);
      const group = groups.get(key) ?? { successes: 0, failures: 0 };
      if (success) group.successes += 1;
      else group.failures += 1;
      groups.set(key, group);
    }

    // -- Output step ---------------------------------------------------------
    let updated = 0;
    for (const [key, tally] of groups) {
      const [provider, model] = key.split('\u0000');
      const cap = await this.capabilityRegistry.findByProviderModel(
        provider,
        model,
      );
      if (!cap) {
        // Capability row missing (unseeded model, typo, etc.) — skip silently.
        // We don't warn because this is expected during early bring-up.
        continue;
      }

      // Collapse the group into a single reputation update using the group's
      // observed success ratio as the target value. This gives the EMA one
      // meaningful nudge per rollup rather than N tiny ones, which keeps
      // alpha=0.05 responsive enough to detect drift without oscillating.
      const total = tally.successes + tally.failures;
      const successRatio = total === 0 ? 1 : tally.successes / total;

      // Push the group's outcome as a single boolean for the EMA. We use the
      // majority signal so a 50/50 split doesn't push successRate toward 0.5
      // artificially — a tied group is treated as success (the routing
      // engine is allowed to keep picking this model).
      const groupSuccess = successRatio >= 0.5;
      await this.capabilityRegistry.updateReputation(cap.id, groupSuccess);
      updated += 1;
    }

    await this.writeState({
      lastRollupAt: startedAt,
      processed: rows.length,
      updated,
    });

    this.logger.log(
      `rollup: processed=${rows.length} updated=${updated} groups=${groups.size} since=${since.toISOString()}`,
    );

    return { processed: rows.length, updated };
  }

  /** Surface last-run metadata for the admin status endpoint. */
  async getStatus(): Promise<{
    lastRollupAt: string | null;
    lastProcessedCount: number;
    lastUpdatedCount: number;
  }> {
    const s = await this.readState();
    return {
      lastRollupAt: s.lastRollupAt ? s.lastRollupAt.toISOString() : null,
      lastProcessedCount: s.processed ?? 0,
      lastUpdatedCount: s.updated ?? 0,
    };
  }

  private async readState(): Promise<LearningEngineState> {
    try {
      const row = await this.prisma.learningEngineState.findUnique({
        where: { id: LearningEngineService.STATE_ID },
      });
      if (!row) return {};
      return {
        lastRollupAt: row.lastRollupAt ?? undefined,
        processed: row.lastProcessedCount,
        updated: row.lastUpdatedCount,
      };
    } catch (err) {
      this.logger.warn(
        `readState failed (using defaults): ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
      return {};
    }
  }

  private async writeState(next: Required<LearningEngineState>): Promise<void> {
    try {
      await this.prisma.learningEngineState.upsert({
        where: { id: LearningEngineService.STATE_ID },
        create: {
          id: LearningEngineService.STATE_ID,
          lastRollupAt: next.lastRollupAt,
          lastProcessedCount: next.processed,
          lastUpdatedCount: next.updated,
        },
        update: {
          lastRollupAt: next.lastRollupAt,
          lastProcessedCount: next.processed,
          lastUpdatedCount: next.updated,
        },
      });
    } catch (err) {
      // The rollup itself still succeeded — we just can't persist the new
      // watermark. Next run will reprocess the same window, which is safe.
      this.logger.warn(
        `writeState failed (will reprocess next cycle): ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }
  }
}

type LearningEngineState = {
  lastRollupAt?: Date;
  processed?: number;
  updated?: number;
};
