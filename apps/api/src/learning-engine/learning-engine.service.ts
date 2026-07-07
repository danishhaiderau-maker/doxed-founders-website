import { Injectable, Logger } from '@nestjs/common';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
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
 * State: last-rollup timestamp is persisted to a single JSON file under
 * `logs/learning-engine-state.json`. One row of state does not justify a
 * Prisma table + migration — a file matches how the trading bot persists
 * its own runtime state and is trivially inspectable by hand.
 */
@Injectable()
export class LearningEngineService {
  private readonly logger = new Logger(LearningEngineService.name);

  static readonly STATE_FILE =
    'logs/learning-engine-state.json' as const;

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
    const state = this.readState();
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
      this.writeState({ lastRollupAt: startedAt, processed: 0, updated: 0 });
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

    this.writeState({
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
  getStatus(): {
    lastRollupAt: string | null;
    lastProcessedCount: number;
    lastUpdatedCount: number;
  } {
    const s = this.readState();
    return {
      lastRollupAt: s.lastRollupAt ? s.lastRollupAt.toISOString() : null,
      lastProcessedCount: s.processed ?? 0,
      lastUpdatedCount: s.updated ?? 0,
    };
  }

  // --- State file -----------------------------------------------------------
  // Plain JSON, sync I/O — the rollup runs once every 6h, file is tiny,
  // and sync read avoids races between the scheduler and any admin probe.

  private readState(): LearningEngineState {
    try {
      if (!existsSync(LearningEngineService.STATE_FILE)) {
        return {};
      }
      const raw = readFileSync(
        LearningEngineService.STATE_FILE,
        'utf8',
      ) as string;
      const parsed = JSON.parse(raw) as Partial<LearningEngineStateJson>;
      return {
        lastRollupAt: parsed.lastRollupAt
          ? new Date(parsed.lastRollupAt)
          : undefined,
        processed: typeof parsed.processed === 'number' ? parsed.processed : undefined,
        updated: typeof parsed.updated === 'number' ? parsed.updated : undefined,
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

  private writeState(next: Required<LearningEngineState>): void {
    try {
      const dir = dirname(LearningEngineService.STATE_FILE);
      mkdirSync(dir, { recursive: true });
      const payload: LearningEngineStateJson = {
        lastRollupAt: next.lastRollupAt.toISOString(),
        processed: next.processed,
        updated: next.updated,
      };
      writeFileSync(
        LearningEngineService.STATE_FILE,
        JSON.stringify(payload, null, 2),
        'utf8',
      );
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

type LearningEngineStateJson = {
  lastRollupAt: string;
  processed: number;
  updated: number;
};
