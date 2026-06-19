import { Injectable, Logger } from '@nestjs/common';
import { appendFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import type { TradeCycleAuditEntry, TradeCycleAuditStage } from '@dcf/utils';

@Injectable()
export class TradeCycleAuditService {
  private readonly logger = new Logger(TradeCycleAuditService.name);
  private readonly logPath = join(process.cwd(), 'logs', 'trade_cycle_audit.jsonl');

  record(entry: Omit<TradeCycleAuditEntry, 'ts'> & { ts?: string }) {
    const row: TradeCycleAuditEntry = {
      ...entry,
      ts: entry.ts ?? new Date().toISOString(),
    };
    try {
      mkdirSync(join(process.cwd(), 'logs'), { recursive: true });
      appendFileSync(this.logPath, `${JSON.stringify(row)}\n`, 'utf8');
    } catch (err) {
      this.logger.warn(
        `trade_cycle_audit append failed: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  stage(
    stage: TradeCycleAuditStage,
    ctx: {
      userId: string;
      agentId?: string;
      cycleId?: string;
      participantId?: string;
      tradeId?: string;
      venue?: string;
      detail?: string;
      meta?: Record<string, unknown>;
    },
  ) {
    this.record({ stage, ...ctx });
  }
}
