import { Injectable, Logger } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { CapabilityRegistryService } from '../capability-registry/capability-registry.service';
import { PrismaService } from '../prisma/prisma.service';
import type { RoutingDecisionRow } from './learning-engine.types';

/**
 * Consumes Flight Recorder outcomes and updates global routing reputation.
 * Its watermark and lease live in Postgres: a local file is unsafe on Railway
 * and a watermark without a lease lets multiple API replicas learn the same
 * window twice.
 */
@Injectable()
export class LearningEngineService {
  private readonly logger = new Logger(LearningEngineService.name);
  private static readonly STATE_ID = 'global';
  /** A rollup is bounded to 50k rows, so two hours gives a healthy worker ample time. */
  private static readonly LEASE_MS = 2 * 60 * 60 * 1000;

  static isSuccess(row: { retried: boolean | null; edited: boolean | null }): boolean {
    return !row.retried && !row.edited;
  }

  constructor(
    private readonly prisma: PrismaService,
    private readonly capabilityRegistry: CapabilityRegistryService,
  ) {}

  async rollup(): Promise<{ processed: number; updated: number }> {
    const leaseOwner = randomUUID();
    if (!(await this.acquireLease(leaseOwner))) {
      this.logger.debug('rollup skipped: another API replica owns the durable lease');
      return { processed: 0, updated: 0 };
    }

    let committed = false;
    try {
      const startedAt = new Date();
      const state = await this.readState();
      const since = state.lastRollupAt ?? new Date(0);
      const rows = (await this.prisma.routingDecision.findMany({
        where: { createdAt: { gt: since } },
        take: 50_000,
      })) as unknown as RoutingDecisionRow[];

      if (rows.length === 0) {
        await this.commitState(leaseOwner, {
          lastRollupAt: startedAt,
          processed: 0,
          updated: 0,
        });
        committed = true;
        this.logger.log(`rollup: no new rows since ${since.toISOString()}; nothing to do.`);
        return { processed: 0, updated: 0 };
      }

      const groups = new Map<string, { successes: number; failures: number }>();
      for (const row of rows) {
        const key = `${row.chosenProvider}\u0000${row.chosenModel}`;
        const group = groups.get(key) ?? { successes: 0, failures: 0 };
        if (LearningEngineService.isSuccess(row)) group.successes += 1;
        else group.failures += 1;
        groups.set(key, group);
      }

      let updated = 0;
      for (const [key, tally] of groups) {
        const [provider, model] = key.split('\u0000');
        const capability = await this.capabilityRegistry.findByProviderModel(provider, model);
        if (!capability) continue;
        const total = tally.successes + tally.failures;
        await this.capabilityRegistry.updateReputation(
          capability.id,
          tally.successes / total >= 0.5,
        );
        updated += 1;
      }

      await this.commitState(leaseOwner, {
        lastRollupAt: startedAt,
        processed: rows.length,
        updated,
      });
      committed = true;
      this.logger.log(
        `rollup: processed=${rows.length} updated=${updated} groups=${groups.size} since=${since.toISOString()}`,
      );
      return { processed: rows.length, updated };
    } finally {
      if (!committed) await this.releaseLease(leaseOwner);
    }
  }

  async getStatus(): Promise<{
    lastRollupAt: string | null;
    lastProcessedCount: number;
    lastUpdatedCount: number;
  }> {
    const state = await this.readState();
    return {
      lastRollupAt: state.lastRollupAt ? state.lastRollupAt.toISOString() : null,
      lastProcessedCount: state.processed ?? 0,
      lastUpdatedCount: state.updated ?? 0,
    };
  }

  private async acquireLease(owner: string): Promise<boolean> {
    const now = new Date();
    const leaseExpiresAt = new Date(now.getTime() + LearningEngineService.LEASE_MS);
    const claimed = await this.prisma.learningEngineState.updateMany({
      where: {
        id: LearningEngineService.STATE_ID,
        OR: [{ leaseExpiresAt: null }, { leaseExpiresAt: { lt: now } }],
      },
      data: { leaseOwner: owner, leaseExpiresAt },
    });
    if (claimed.count === 1) return true;

    try {
      await this.prisma.learningEngineState.create({
        data: {
          id: LearningEngineService.STATE_ID,
          leaseOwner: owner,
          leaseExpiresAt,
        },
      });
      return true;
    } catch (err) {
      // Another replica may have created the singleton after our conditional
      // update missed it. It owns this rollup window.
      if ((err as { code?: string }).code === 'P2002') return false;
      throw err;
    }
  }

  private async readState(): Promise<LearningEngineState> {
    const row = await this.prisma.learningEngineState.findUnique({
      where: { id: LearningEngineService.STATE_ID },
    });
    if (!row) throw new Error('Learning Engine state disappeared while its lease was held');
    return {
      lastRollupAt: row.lastRollupAt ?? undefined,
      processed: row.lastProcessedCount,
      updated: row.lastUpdatedCount,
    };
  }

  private async commitState(owner: string, next: Required<LearningEngineState>): Promise<void> {
    const result = await this.prisma.learningEngineState.updateMany({
      where: { id: LearningEngineService.STATE_ID, leaseOwner: owner },
      data: {
        lastRollupAt: next.lastRollupAt,
        lastProcessedCount: next.processed,
        lastUpdatedCount: next.updated,
        leaseOwner: null,
        leaseExpiresAt: null,
      },
    });
    if (result.count !== 1) throw new Error('Learning Engine lost its durable rollup lease');
  }

  private async releaseLease(owner: string): Promise<void> {
    try {
      await this.prisma.learningEngineState.updateMany({
        where: { id: LearningEngineService.STATE_ID, leaseOwner: owner },
        data: { leaseOwner: null, leaseExpiresAt: null },
      });
    } catch (err) {
      this.logger.warn(`rollup lease release failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
}

type LearningEngineState = {
  lastRollupAt?: Date;
  processed?: number;
  updated?: number;
};
