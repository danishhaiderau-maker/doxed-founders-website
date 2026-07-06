import { createHash } from 'crypto';
import { Injectable, Logger, OnModuleDestroy } from '@nestjs/common';
import type { AiRuntimeRequest, AiRuntimeResponse } from './founder-ai-runtime.types';

type CacheEntry = {
  value: AiRuntimeResponse;
  expiresAt: number;
};

/** Redis-backed cache — Phase 1 when REDIS_URL is set. */
export interface PromptCacheBackend {
  get(key: string): Promise<AiRuntimeResponse | null>;
  set(key: string, value: AiRuntimeResponse, ttlSec: number): Promise<void>;
}

/**
 * In-memory LRU prompt hash cache for Phase 0.
 * Keys: SHA-256(system prefix + section + normalized user prompt + userId).
 * Swap backend via REDIS_URL in Phase 1 without changing callers.
 */
@Injectable()
export class PromptCacheService implements OnModuleDestroy, PromptCacheBackend {
  private readonly logger = new Logger(PromptCacheService.name);
  private readonly store = new Map<string, CacheEntry>();
  private sweepTimer: ReturnType<typeof setInterval> | null = null;

  constructor() {
    this.sweepTimer = setInterval(() => this.evictExpired(), 60_000);
    this.sweepTimer.unref?.();
  }

  onModuleDestroy() {
    if (this.sweepTimer) clearInterval(this.sweepTimer);
  }

  get ttlSec(): number {
    const raw = Number(process.env.AI_PROMPT_CACHE_TTL_SEC ?? 3600);
    return Number.isFinite(raw) && raw > 0 ? raw : 3600;
  }

  get maxEntries(): number {
    const raw = Number(process.env.AI_PROMPT_CACHE_MAX_ENTRIES ?? 500);
    return Number.isFinite(raw) && raw > 0 ? raw : 500;
  }

  buildKey(request: AiRuntimeRequest): string {
    const normalizedUser = request.userPrompt.trim().replace(/\s+/g, ' ').toLowerCase();
    const systemPrefix = request.system.slice(0, 2048);
    const payload = [
      request.section,
      request.userId,
      systemPrefix,
      normalizedUser,
    ].join('\x1e');
    return createHash('sha256').update(payload).digest('hex');
  }

  async get(key: string): Promise<AiRuntimeResponse | null> {
    const entry = this.store.get(key);
    if (!entry) return null;
    if (Date.now() > entry.expiresAt) {
      this.store.delete(key);
      return null;
    }
    // LRU touch
    this.store.delete(key);
    this.store.set(key, entry);
    return { ...entry.value, cacheHit: true, cacheKey: key };
  }

  async set(key: string, value: AiRuntimeResponse, ttlSec = this.ttlSec): Promise<void> {
    if (this.store.size >= this.maxEntries) {
      const oldest = this.store.keys().next().value;
      if (oldest) this.store.delete(oldest);
    }
    this.store.set(key, {
      value: { ...value, cacheHit: false, cacheKey: key },
      expiresAt: Date.now() + ttlSec * 1000,
    });
    this.logger.debug(`prompt cache set key=${key.slice(0, 12)}… ttl=${ttlSec}s`);
  }

  stats() {
    return { entries: this.store.size, maxEntries: this.maxEntries, ttlSec: this.ttlSec };
  }

  private evictExpired() {
    const now = Date.now();
    for (const [key, entry] of this.store) {
      if (now > entry.expiresAt) this.store.delete(key);
    }
  }
}
