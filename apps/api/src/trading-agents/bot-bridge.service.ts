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
    if (this.dbUrlCache && now - this.dbUrlCache.at < 15_000) {
      if (this.dbUrlCache.url) return this.dbUrlCache.url;
    } else {
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
    }
    return this.getBotUrl();
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

  /** Execution paths bypass cache for instant showcase parity. */
  async fetchStateForExecution(force = true): Promise<BotApiState | null> {
    return this.fetchState(force, 'relay');
  }

  async fetchState(force = false, mode: 'full' | 'relay' = 'full'): Promise<BotApiState | null> {
    const base = await this.resolveBotUrl();
    if (!base) return null;

    const now = Date.now();
    if (!force && this.cached && now - this.lastFetchAt < this.cacheMs) {
      return this.cached;
    }

    const paths =
      mode === 'relay'
        ? ['/api/relay-state', '/api/state']
        : ['/api/state', '/api/relay-state'];

    for (const path of paths) {
      const timeoutMs = path.includes('relay-state') ? 8_000 : 12_000;
      try {
        const res = await fetch(`${base}${path}`, {
          signal: AbortSignal.timeout(timeoutMs),
          headers: { Accept: 'application/json' },
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
        const res = await fetch(`${base}${path}`, { signal: AbortSignal.timeout(8_000) });
        if (!res.ok) continue;
        return (await res.json()) as Record<string, unknown>;
      } catch {
        /* try next path */
      }
    }
    return null;
  }
}
