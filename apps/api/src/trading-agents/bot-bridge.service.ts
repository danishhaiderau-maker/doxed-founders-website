import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import {
  BotActivityEntry,
  BotApiState,
  mapBotStateToActivity,
  mapBotStateToAgentStats,
  mapBotStateToDashboard,
} from './bot-state.mapper';
import { PrismaService } from '../prisma/prisma.service';
import { ShowcaseSnapshotService } from './showcase-snapshot.service';

@Injectable()
export class BotBridgeService {
  private readonly logger = new Logger(BotBridgeService.name);
  private lastFetchAt = 0;
  private cached: BotApiState | null = null;
  private cacheMs = Number(process.env.BOT_BRIDGE_CACHE_MS ?? 5000);
  private dbUrlCache: { url: string | null; at: number } | null = null;

  constructor(
    private readonly config: ConfigService,
    private readonly prisma: PrismaService,
    private readonly showcaseSnapshot: ShowcaseSnapshotService,
  ) {}

  getBotUrl(): string | null {
    const url = (
      this.config.get<string>('TRADING_AGENT_BOT_URL') ??
      this.config.get<string>('CONSERVATIVE_BTC_BOT_URL') ??
      ''
    ).trim();
    return url ? url.replace(/\/$/, '') : null;
  }

  /** Home tunnel URL is wired to Neon first; env vars need a Railway restart to catch up. */
  async resolveBotUrl(): Promise<string | null> {
    const now = Date.now();
    if (this.dbUrlCache && now - this.dbUrlCache.at < 15_000 && this.dbUrlCache.url) {
      return this.dbUrlCache.url;
    }
    try {
      const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
      const db = row?.showcaseBotPublicUrl?.trim();
      const normalized = db ? db.replace(/\/$/, '') : null;
      this.dbUrlCache = { url: normalized, at: now };
      if (normalized) return normalized;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.logger.warn(`Bot URL DB lookup failed: ${msg}`);
    }
    const envUrl = this.getBotUrl();
    if (envUrl && /127\.0\.0\.1|:7800\b|localhost/i.test(envUrl)) {
      this.logger.warn(
        `Ignoring local env bot URL (${envUrl}) — wire showcase to https://bot.doxxedcrypto.digital`,
      );
      return null;
    }
    return envUrl;
  }

  async isEnabledAsync(): Promise<boolean> {
    return Boolean(await this.resolveBotUrl());
  }

  isEnabled(): boolean {
    if (this.getBotUrl()) return true;
    // Sync callers (relay tick) — use last Neon-resolved URL from resolveBotUrl().
    if (this.dbUrlCache?.url) return true;
    return false;
  }

  invalidateCache() {
    this.cached = null;
    this.lastFetchAt = 0;
  }

  /** Cache-first relay snapshot pushed from home bot every ~2s (admin display only). */
  private async fetchCachedRelaySnapshot(maxAgeMs = 15_000): Promise<BotApiState | null> {
    try {
      const cached = await this.showcaseSnapshot.getCachedSnapshot();
      if (!cached.snapshot) return null;
      const ageMs = cached.at ? Date.now() - cached.at.getTime() : Number.MAX_SAFE_INTEGER;
      if (ageMs > maxAgeMs) {
        this.logger.warn(`Cached showcase snapshot stale (${Math.round(ageMs / 1000)}s) — falling back to live bot`);
        return null;
      }
      const state = cached.snapshot as BotApiState;
      state.snapshot_seq = cached.snapshot_seq;
      state.snapshot_source = 'railway_cache';
      return state;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.logger.warn(`Cached snapshot read failed: ${msg}`);
      return null;
    }
  }

  /** Execution + relay sim — always live bot; never Railway cache. */
  async fetchStateForExecution(force = true): Promise<BotApiState | null> {
    return this.fetchState(force, 'live');
  }

  /** Admin panels — fast relay snapshot; do not block on full /api/state (large trades_map). */
  async fetchStateForAdmin(force = true): Promise<BotApiState | null> {
    const now = Date.now();
    if (!force && this.cached && now - this.lastFetchAt < this.cacheMs) {
      return this.cached;
    }

    const cached = await this.fetchCachedRelaySnapshot();
    if (cached) {
      this.cached = cached;
      this.lastFetchAt = now;
      return cached;
    }

    const base = await this.resolveBotUrl();
    if (!base) return null;

    for (const path of ['/api/relay-state', '/api/state']) {
      try {
        const res = await fetch(`${base}${path}`, {
          signal: AbortSignal.timeout(8_000),
          headers: {
            Accept: 'application/json',
            'User-Agent': 'doxxedcrypto-admin/1.0',
          },
        });
        if (!res.ok) {
          this.logger.warn(`Bot ${path} HTTP ${res.status}`);
          continue;
        }
        const data = (await res.json()) as BotApiState;
        if (!data || typeof data !== 'object') continue;
        this.cached = data;
        this.lastFetchAt = now;
        return data;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(`Bot ${path} admin fetch failed: ${msg}`);
      }
    }

    return null;
  }

  async fetchState(force = false, mode: 'full' | 'relay' | 'live' = 'full'): Promise<BotApiState | null> {
    const now = Date.now();
    if (!force && this.cached && now - this.lastFetchAt < this.cacheMs) {
      return this.cached;
    }

    if (mode === 'relay') {
      const cached = await this.fetchCachedRelaySnapshot();
      if (cached) {
        this.cached = cached;
        this.lastFetchAt = now;
        return cached;
      }
    }

    const base = await this.resolveBotUrl();
    if (!base) return null;

    const paths =
      mode === 'full' || mode === 'live'
        ? ['/api/relay-state', '/api/state']
        : ['/api/relay-state', '/api/state'];

    for (const path of paths) {
      const timeoutMs = path.includes('relay-state') ? 20_000 : 30_000;
      try {
        const res = await fetch(`${base}${path}`, {
          signal: AbortSignal.timeout(timeoutMs),
          headers: {
            Accept: 'application/json',
            'User-Agent': 'doxxedcrypto-relay/1.0',
          },
        });
        if (!res.ok) {
          this.logger.warn(`Bot ${path} HTTP ${res.status}`);
          continue;
        }
        const data = (await res.json()) as BotApiState;
        if (!data || typeof data !== 'object') continue;
        this.cached = data;
        this.lastFetchAt = now;
        return data;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(`Bot ${path} fetch failed: ${msg}`);
      }
    }

    this.invalidateCache();
    return null;
  }

  /** True when the showcase bot HTTP endpoints respond (not killed on Railway). */
  async isReachable(force = false): Promise<boolean> {
    const health = await this.fetchHealth();
    if (health) return true;
    const state = await this.fetchState(force, 'relay');
    return Boolean(state);
  }

  async getLiveDashboard(agentName: string, force = false) {
    const botUrl = await this.resolveBotUrl();
    const bot = await this.fetchState(force, 'relay');
    if (!bot) {
      // Health-only is not enough for showcase tables — avoid "connected" with empty liveBook.
      return null;
    }
    return {
      dashboard: mapBotStateToDashboard(bot),
      stats: mapBotStateToAgentStats(bot),
      activity: mapBotStateToActivity(bot, agentName),
      rawState: bot,
      botConnected: true,
      botUrl,
      strategyMode: bot.strategy_mode ?? 'RESEARCH',
      executionPaused: bot.execution_paused ?? false,
      executionReason: bot.execution_reason ?? null,
    };
  }

  async getLiveActivity(agentName: string): Promise<BotActivityEntry[] | null> {
    const bot = await this.fetchState();
    if (!bot) return null;
    return mapBotStateToActivity(bot, agentName);
  }

  async proxyBotPost(path: string, body: Record<string, unknown> = {}) {
    const base = await this.resolveBotUrl();
    if (!base) {
      return { ok: false, error: 'Bot bridge not configured' };
    }
    const secret = this.config.get<string>('BOT_CONTROL_SECRET')?.trim();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    };
    if (secret) {
      headers['X-Bot-Control-Secret'] = secret;
    }
    try {
      const res = await fetch(`${base}${path.startsWith('/') ? path : `/${path}`}`, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(8000),
      });
      const data = await res.json().catch(() => ({}));
      return { ok: res.ok, status: res.status, data };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return { ok: false, error: msg };
    }
  }

  async fetchHealth() {
    const base = await this.resolveBotUrl();
    if (!base) return null;
    for (const path of ['/api/ping', '/health']) {
      try {
        const res = await fetch(`${base}${path}`, {
          signal: AbortSignal.timeout(8_000),
          headers: { Accept: 'application/json', 'User-Agent': 'doxxedcrypto-relay/1.0' },
        });
        if (!res.ok) continue;
        return (await res.json()) as Record<string, unknown>;
      } catch {
        /* try next path */
      }
    }
    return null;
  }

  /** Read-only proxy to the research analyzer (:9001) via the bot's public tunnel.
   *  The analyzer itself is not publicly reachable; the bot forwards /api/analyzer/* to localhost:9001. */
  private async fetchAnalyzerProxy<T>(path: string, timeoutMs = 8_000): Promise<T | null> {
    const base = await this.resolveBotUrl();
    if (!base) return null;
    try {
      const res = await fetch(`${base}${path}`, {
        signal: AbortSignal.timeout(timeoutMs),
        headers: { Accept: 'application/json', 'User-Agent': 'doxxedcrypto-analyzer/1.0' },
      });
      if (!res.ok) return null;
      return (await res.json()) as T;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.logger.warn(`Analyzer proxy ${path} failed: ${msg}`);
      return null;
    }
  }

  async fetchAnalyzerSummary(): Promise<Record<string, unknown> | null> {
    return this.fetchAnalyzerProxy<Record<string, unknown>>('/api/analyzer/summary');
  }

  async fetchAnalyzerGenome(): Promise<Record<string, unknown> | null> {
    return this.fetchAnalyzerProxy<Record<string, unknown>>('/api/analyzer/genome', 12_000);
  }
}
