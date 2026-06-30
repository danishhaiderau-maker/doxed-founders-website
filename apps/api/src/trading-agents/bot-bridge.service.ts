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

/** Cumulative full-session metrics derived from the bot's /api/state. */
export type CumulativeSessionMetrics = {
  starting_balance: number;
  current_balance: number;
  total_pnl_usd: number;
  total_pnl_pct: number;
  daily_pnl_usd: number;
  trade_count: number;
  win_rate: number;
  session_start: string | null;
  session_hours: number | undefined;
  bot_version: string | null;
  bot_start_time: number | null;
  last_fresh_reset_ts: number | null;
  generated_at: string;
};

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

  /** Default canonical showcase bot URL (Cloudflare tunnel — has been flaky with HTTP 530). */
  private readonly DEFAULT_CF_URL = 'https://bot.doxxedcrypto.digital';
  /** Stable Fly.io trading-only bot — used as the PRIMARY endpoint for health + state pulls. */
  private readonly DEFAULT_FLY_URL = 'https://doxed-btc-bot.fly.dev';

  getBotUrl(): string | null {
    const url = (
      this.config.get<string>('TRADING_AGENT_BOT_URL') ??
      this.config.get<string>('CONSERVATIVE_BTC_BOT_URL') ??
      ''
    ).trim();
    return url ? url.replace(/\/$/, '') : null;
  }

  /** Stable Fly.io endpoint — env override wins, else the well-known fly.dev URL. */
  getFlyUrl(): string {
    const env = (this.config.get<string>('BOT_FLY_URL') ?? '').trim();
    return (env ? env : this.DEFAULT_FLY_URL).replace(/\/$/, '');
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
        `Ignoring local env bot URL (${envUrl}) — wire showcase to ${this.DEFAULT_CF_URL}`,
      );
      return null;
    }
    return envUrl;
  }

  /** Ordered candidate bot URLs for resilient health/state pulls.
   *  Fly.io is PRIMARY (stable HTTP 200), Cloudflare tunnel is FALLBACK (covers analyzer proxy
   *  and the local showcase bot when Fly is unreachable). Online if EITHER responds. */
  async resolveBotUrls(): Promise<string[]> {
    const cf = (await this.resolveBotUrl()) ?? this.DEFAULT_CF_URL;
    const fly = this.getFlyUrl();
    // De-dup while preserving order (Fly first).
    const seen = new Set<string>();
    const out: string[] = [];
    for (const u of [fly, cf]) {
      if (!u) continue;
      if (seen.has(u)) continue;
      seen.add(u);
      out.push(u);
    }
    return out;
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

    const data = await this.fetchStateFromCandidates(['/api/relay-state', '/api/state'], {
      relayTimeout: 8_000,
      stateTimeout: 8_000,
      userAgent: 'doxxedcrypto-admin/1.0',
    });
    if (data) {
      this.cached = data;
      this.lastFetchAt = now;
    }
    return data;
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

    const data = await this.fetchStateFromCandidates(['/api/relay-state', '/api/state'], {
      relayTimeout: 20_000,
      stateTimeout: 30_000,
      userAgent: 'doxxedcrypto-relay/1.0',
    });
    if (data) {
      this.cached = data;
      this.lastFetchAt = now;
      return data;
    }

    this.invalidateCache();
    return null;
  }

  /** Race all candidate bot URLs in parallel for each path.
   *  Returns the first valid 200 JSON state — online if EITHER endpoint responds.
   *  Parallel racing avoids slow Fly-down latency (first responder wins). */
  private async fetchStateFromCandidates(
    paths: string[],
    opts: { relayTimeout: number; stateTimeout: number; userAgent: string },
  ): Promise<BotApiState | null> {
    const bases = await this.resolveBotUrls();
    if (bases.length === 0) return null;
    for (const path of paths) {
      const timeoutMs = path.includes('relay-state') ? opts.relayTimeout : opts.stateTimeout;
      const attempts = bases.map(async (base): Promise<BotApiState | null> => {
        try {
          const res = await fetch(`${base}${path}`, {
            signal: AbortSignal.timeout(timeoutMs),
            headers: {
              Accept: 'application/json',
              'User-Agent': opts.userAgent,
            },
          });
          if (!res.ok) {
            this.logger.warn(`Bot ${base}${path} HTTP ${res.status}`);
            return null;
          }
          const data = (await res.json()) as BotApiState;
          if (!data || typeof data !== 'object') return null;
          if (bases.length > 1) {
            this.logger.log(`Bot state fetched from ${base}${path}`);
          }
          return data;
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          this.logger.warn(`Bot ${base}${path} fetch failed: ${msg}`);
          return null;
        }
      });
      // First valid state wins; ignore rejections/empty results from the other endpoint.
      const settled = await Promise.allSettled(attempts);
      for (const r of settled) {
        if (r.status === 'fulfilled' && r.value) return r.value;
      }
    }
    return null;
  }

  /** True when EITHER the Fly bot or the Cloudflare showcase tunnel responds. */
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
    const bases = await this.resolveBotUrls();
    if (bases.length === 0) {
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
    const p = path.startsWith('/') ? path : `/${path}`;
    for (const base of bases) {
      try {
        const res = await fetch(`${base}${p}`, {
          method: 'POST',
          headers,
          body: JSON.stringify(body),
          signal: AbortSignal.timeout(8000),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok || res.status < 500) {
          return { ok: res.ok, status: res.status, data };
        }
        this.logger.warn(`Bot POST ${base}${p} HTTP ${res.status} — trying next endpoint`);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(`Bot POST ${base}${p} failed: ${msg} — trying next endpoint`);
      }
    }
    return { ok: false, error: 'All bot endpoints unreachable' };
  }

  /** Health probe — race Fly + Cloudflare in parallel; online if either responds 200.
   *  Parallel racing avoids waiting on a dead endpoint (first 200 wins). */
  async fetchHealth() {
    const bases = await this.resolveBotUrls();
    const probes: Promise<Record<string, unknown> | null>[] = [];
    for (const base of bases) {
      for (const path of ['/api/ping', '/health']) {
        probes.push(
          (async () => {
            try {
              const res = await fetch(`${base}${path}`, {
                signal: AbortSignal.timeout(8_000),
                headers: { Accept: 'application/json', 'User-Agent': 'doxxedcrypto-relay/1.0' },
              });
              if (!res.ok) return null;
              return (await res.json()) as Record<string, unknown>;
            } catch {
              return null;
            }
          })(),
        );
      }
    }
    const settled = await Promise.allSettled(probes);
    for (const r of settled) {
      if (r.status === 'fulfilled' && r.value) return r.value;
    }
    return null;
  }

  /** Read-only proxy to the research analyzer (:9001) via the bot's public tunnel.
   *  The analyzer runs only on the LOCAL showcase bot (Cloudflare tunnel) — Fly does NOT run it.
   *  Try the Cloudflare tunnel for analyzer paths; fall back to Fly for non-analyzer state. */
  private async fetchAnalyzerProxy<T>(path: string, timeoutMs = 8_000): Promise<T | null> {
    // Analyzer proxy is only on the Cloudflare showcase bot (Fly is trading-only).
    const cf = (await this.resolveBotUrl()) ?? this.DEFAULT_CF_URL;
    try {
      const res = await fetch(`${cf}${path}`, {
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

  /** Cumulative full-session metrics derived from the bot's /api/state (cached 60s).
   *  Used as the reliable fallback when the :9001 analyzer proxy (/api/analyzer/summary)
   *  is not exposed by the bot. Reads analytics.total_trades / win_rate (cumulative since the
   *  last fresh-collection wipeout) and the live account balance + daily P&L. */
  private cumulativeCache: { at: number; data: CumulativeSessionMetrics | null } | null = null;
  private readonly cumulativeCacheMs = 60_000;

  async fetchCumulativeSessionMetrics(): Promise<CumulativeSessionMetrics | null> {
    const now = Date.now();
    if (this.cumulativeCache && now - this.cumulativeCache.at < this.cumulativeCacheMs) {
      return this.cumulativeCache.data;
    }
    const bot = await this.fetchState(true, 'relay');
    if (!bot) {
      this.cumulativeCache = { at: now, data: null };
      return null;
    }
    const STARTING = 500;
    const analytics = bot.analytics ?? {};
    const totalTrades =
      Number(analytics.total_trades ?? bot.trade_count_session ?? 0) || 0;
    const winRate = Number(analytics.win_rate ?? 0) || 0;
    const currentBalance = Number(
      bot.account_balance ?? bot.equity ?? STARTING,
    );
    const totalPnlUsd = Number((currentBalance - STARTING).toFixed(2));
    const totalPnlPct = Number(((totalPnlUsd / STARTING) * 100).toFixed(2));
    const dailyPnlUsd =
      typeof bot.daily_pnl_usd === 'number' && Number.isFinite(bot.daily_pnl_usd)
        ? bot.daily_pnl_usd
        : totalPnlUsd;
    // Session anchor = last fresh-collection wipeout, else bot start.
    const freshResetTs = Number(bot.last_fresh_reset_ts ?? 0);
    const anchorTs =
      freshResetTs && freshResetTs > 0 ? freshResetTs : Number(bot.bot_start_time ?? 0);
    const sessionStartIso = anchorTs > 0 ? new Date(anchorTs * 1000).toISOString() : null;
    const sessionHours = anchorTs > 0 ? Math.max(0, (now / 1000 - anchorTs) / 3600) : undefined;
    const metrics: CumulativeSessionMetrics = {
      starting_balance: STARTING,
      current_balance: Number(currentBalance.toFixed(2)),
      total_pnl_usd: totalPnlUsd,
      total_pnl_pct: totalPnlPct,
      daily_pnl_usd: Number(dailyPnlUsd.toFixed(2)),
      trade_count: totalTrades,
      win_rate: Number(winRate.toFixed(1)),
      session_start: sessionStartIso,
      session_hours: sessionHours != null ? Number(sessionHours.toFixed(2)) : undefined,
      bot_version: bot.bot_version ?? null,
      bot_start_time: bot.bot_start_time ?? null,
      last_fresh_reset_ts: bot.last_fresh_reset_ts ?? null,
      generated_at: new Date().toISOString(),
    };
    this.cumulativeCache = { at: now, data: metrics };
    return metrics;
  }
}
