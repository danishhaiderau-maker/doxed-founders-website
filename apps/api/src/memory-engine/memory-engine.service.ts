import { Injectable, Logger } from '@nestjs/common';
import type { MemoryEntry, MemoryQuery, MemoryStore } from './memory-engine.types';

/**
 * Memory Engine — skeleton.
 *
 * Phase 1 only ships the interface. Real backends land in Phases 2-4 (see
 * docs/KERNEL.md §10):
 *   - conversation store: short-term chat memory
 *   - project store:      per-project knowledge (Phase 2)
 *   - founder store:      per-founder durable memory (Phase 2)
 *   - workspace store:    workspace-shared knowledge (Phase 3)
 *
 * Every method is stubbed to a no-op that logs at debug level so kernel
 * callers (Routing Engine context builder, Founder Intent Engine later)
 * can wire against the interface today.
 */
@Injectable()
export class MemoryEngineService {
  private readonly logger = new Logger(MemoryEngineService.name);

  async get(
    store: MemoryStore,
    scope: string,
    key: string,
  ): Promise<MemoryEntry | null> {
    this.logger.debug(`MemoryEngine.get stubbed store=${store} scope=${scope} key=${key}`);
    return null;
  }

  async set(
    store: MemoryStore,
    scope: string,
    key: string,
    value: unknown,
  ): Promise<void> {
    this.logger.debug(`MemoryEngine.set stubbed store=${store} scope=${scope} key=${key}`);
  }

  async query(query: MemoryQuery): Promise<MemoryEntry[]> {
    this.logger.debug(`MemoryEngine.query stubbed query=${JSON.stringify(query)}`);
    return [];
  }

  async forget(
    store: MemoryStore,
    scope: string,
    key: string,
  ): Promise<void> {
    this.logger.debug(`MemoryEngine.forget stubbed store=${store} scope=${scope} key=${key}`);
  }
}
