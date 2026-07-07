import { createHash } from 'node:crypto';
import type { RoutingDecision } from './routing-engine.types';

/**
 * Layer 1 of the routing pipeline (docs/KERNEL.md §6): a tiny LRU cache of
 * routing decisions keyed by a hash of the normalized prompt prefix.
 *
 * v1 spec:
 *   - maxEntries = 512
 *   - ttlMs      = 5 minutes
 *   - key        = "v1:" + sha256(normalized first 4KB of prompt)
 *
 * Eviction is LRU based on insertion / access order. Expiry is lazy — entries
 * are checked on read and reaped when found stale.
 */
export class RoutingEngineCache {
  private readonly maxEntries: number;
  private readonly ttlMs: number;
  private readonly store = new Map<string, { decision: RoutingDecision; expiresAt: number }>();
  private hits = 0;
  private misses = 0;

  constructor(opts?: { maxEntries?: number; ttlMs?: number }) {
    this.maxEntries = opts?.maxEntries ?? 512;
    this.ttlMs = opts?.ttlMs ?? 5 * 60 * 1000;
  }

  /**
   * Normalize whitespace, lowercase, take SHA-256 of the first 4KB, prefix
   * with "v1:". The version prefix lets us bump the cache shape later
   * (e.g. include intent in the key) without invalidating reads abruptly —
   * old keys simply age out via TTL.
   */
  computeKey(prompt: string): string {
    const normalized = prompt.replace(/\s+/g, ' ').trim().toLowerCase();
    const head = normalized.slice(0, 4096);
    const hash = createHash('sha256').update(head, 'utf8').digest('hex');
    return `v1:${hash}`;
  }

  get(key: string): RoutingDecision | null {
    const entry = this.store.get(key);
    if (!entry) {
      this.misses += 1;
      return null;
    }
    if (entry.expiresAt < Date.now()) {
      this.store.delete(key);
      this.misses += 1;
      return null;
    }
    // Move to most-recently-used position.
    this.store.delete(key);
    this.store.set(key, entry);
    this.hits += 1;
    return entry.decision;
  }

  set(key: string, decision: RoutingDecision): void {
    // Delete first so re-inserting the same key reorders it to MRU.
    this.store.delete(key);
    this.store.set(key, { decision, expiresAt: Date.now() + this.ttlMs });

    while (this.store.size > this.maxEntries) {
      // Map iteration is insertion-order; oldest entry is the first key.
      const oldestKey = this.store.keys().next().value;
      if (oldestKey === undefined) break;
      this.store.delete(oldestKey);
    }
  }

  clear(): void {
    this.store.clear();
    this.hits = 0;
    this.misses = 0;
  }

  stats(): { size: number; hits: number; misses: number } {
    return {
      size: this.store.size,
      hits: this.hits,
      misses: this.misses,
    };
  }
}
