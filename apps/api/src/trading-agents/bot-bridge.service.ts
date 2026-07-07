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
  /**
   * F7 (2026-07-07 incident) — Execution-path cache TTL. The execution relay
   * polls every SUBSCRIBER_EXECUTION_POLL_MS (default 2000ms); a 5s cache
   * meant exits lagged the showcase by up to 5s on every cycle. Drop to 2s
   * so the bridge stays fresh against the relay's own poll cadence. Still
   * env-overridable for high-throughput tunnels (set BOT_BRIDGE_EXEC_CACHE_MS
   * back to 5000 if the tunnel can't take the load).
   */
  private execCacheMs = Number(process.env.BOT_BRIDGE_EXEC_CACHE_MS ?? 2000);
  private dbUrlCache: { url: string | null; at: number } | null = null;
  /** Execution-path cache — canonical showcase bot ONLY (never populated from the Fly race). */
  private execCached: BotApiState | null = null;
  private execFetchAt = 0;

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

  /** Canonical showcase URL — home bot :7002 via Cloudflare tunnel ONLY. */
  async resolveShowcaseUrl(): Promise<string> {
    return (await this.resolveBotUrl()) ?? this.DEFAULT_CF_URL;
  }

  /** Ordered candidate bot URLs for legacy health probes.
   *  Canonical showcase is Cloudflare first; Fly is a separate legacy instance and must
   *  never win showcase state races (split-brain). */
  async resolveBotUrls(): Promise<string[]> {
    const cf = await this.resolveShowcaseUrl();
    const fly = this.getFlyUrl();
    const seen = new Set<string>();
    const out: string[] = [];
    for (const u of [cf, fly]) {
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

  /** Agent Hub showcase desk — canonical home bot (:7002 via Cloudflare) ONLY.
   *  Never the Fly.io stale instance. Falls back to the Railway-pushed relay snapshot
   *  (up to 10m stale) when the tunnel blips. */
  async fetchPublicShowcaseState(force = true): Promise<BotApiState | null> {
    const now = Date.now();
    if (!force && this.cached && now - this.lastFetchAt < this.cacheMs) {
      return this.cached;
    }
    const cf = (await this.resolveBotUrl()) ?? this.DEFAULT_CF_URL;
    for (const path of ['/api/relay-state', '/api/state'] as const) {
      const timeoutMs = path === '/api/relay-state' ? 20_000 : 30_000;
      try {
        const res = await fetch(`${cf}${path}`, {
          signal: AbortSignal.timeout(timeoutMs),
          headers: { Accept: 'application/json', 'User-Agent': 'doxxedcrypto-showcase/1.0' },
        });
        if (!res.ok) {
          this.logger.warn(`Public showcase ${cf}${path} HTTP ${res.status}`);
          continue;
        }
        const data = (await res.json()) as BotApiState;
        if (!data || typeof data !== 'object') continue;
        this.cached = data;
        this.lastFetchAt = now;
        return data;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(`Public showcase ${cf}${path} fetch failed: ${msg}`);
      }
    }
    const cached = await this.fetchCachedRelaySnapshot(this.cumulativeStaleMs);
    if (cached) {
      this.cached = cached;
      this.lastFetchAt = now;
      return cached;
    }
    return null;
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

  /** Execution + relay sim — the CANONICAL showcase bot ONLY; never Railway cache, never Fly.
   *  The Fly instance (doxed-btc-bot.fly.dev) is a SEPARATE, stale bot with its own book.
   *  Racing it here (the old fetchState path) let the execution path — entry anchors, chase,
   *  abandon checks, dedupe, capacity, mirror diff — act on a foreign book (split-brain).
   *  Fail-closed: when the canonical showcase bot is unreachable this returns null and the
   *  executor HOLDS; it must never trade on the Fly bot's state. Health/admin/public dashboard
   *  reads keep the Fly race via fetchState / fetchStateForAdmin / fetchHealth. */
  async fetchStateForExecution(force = true): Promise<BotApiState | null> {
    const now = Date.now();
    if (!force && this.execCached && now - this.execFetchAt < this.execCacheMs) {
      return this.execCached;
    }
    const cf = (await this.resolveBotUrl()) ?? this.DEFAULT_CF_URL;
    for (const path of ['/api/relay-state', '/api/state'] as const) {
      const timeoutMs = path === '/api/relay-state' ? 20_000 : 30_000;
      try {
        const res = await fetch(`${cf}${path}`, {
          signal: AbortSignal.timeout(timeoutMs),
          headers: { Accept: 'application/json', 'User-Agent': 'doxxedcrypto-relay/1.0' },
        });
        if (!res.ok) {
          this.logger.warn(`Canonical bot ${cf}${path} HTTP ${res.status}`);
          continue;
        }
        const data = (await res.json()) as BotApiState;
        if (!data || typeof data !== 'object') continue;
        this.execCached = data;
        this.execFetchAt = now;
        return data;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(`Canonical bot ${cf}${path} fetch failed: ${msg}`);
      }
    }
    // Canonical unreachable — execution must hold (no Fly fallback for money decisions).
    this.execCached = null;
    this.execFetchAt = 0;
    return null;
  }

  /** Canonical showcase bot state for session-epoch tracking — DOES NOT race Fly.
   *  Always reads from the single canonical showcase URL (PlatformSettings.showcaseBotPublicUrl,
   *  fallback to the Cloudflare home tunnel) so the epoch key stays stable across polls.
   *  Racing Fly + CF returns different bot_start_time values (they are distinct bot instances)
   *  and flips the epoch on every poll where the race winner changes — which wipes every user's
   *  armed relay sim via resetAllUserCopySessions. Only the canonical showcase bot (the one the
   *  relay mirrors via :7002) is authoritative for session-epoch detection. */
  async fetchShowcaseCanonicalState(force = true): Promise<BotApiState | null> {
    const now = Date.now();
    if (!force && this.cached && now - this.lastFetchAt < this.cacheMs) {
      return this.cached;
    }
    const cf = (await this.resolveBotUrl()) ?? this.DEFAULT_CF_URL;
    try {
      const res = await fetch(`${cf}/api/state`, {
        signal: AbortSignal.timeout(20_000),
        headers: { Accept: 'application/json', 'User-Agent': 'doxxedcrypto-sync/1.0' },
      });
      if (!res.ok) {
        this.logger.warn(`Canonical showcase ${cf}/api/state HTTP ${res.status}`);
        return null;
      }
      const data = (await res.json()) as BotApiState;
      if (!data || typeof data !== 'object') return null;
      this.cached = data;
      this.lastFetchAt = now;
      return data;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.logger.warn(`Canonical showcase ${cf}/api/state fetch failed: ${msg}`);
      return null;
    }
  }

  /** Admin panels — canonical showcase only (never Fly). Cache-first, then live tunnel. */
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

    const data = await this.fetchShowcaseState(['/api/relay-state', '/api/state'], {
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

    const data = await this.fetchShowcaseState(['/api/relay-state', '/api/state'], {
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

  /** Fetch state from the canonical showcase tunnel only — never Fly (split-brain guard). */
  private async fetchShowcaseState(
    paths: string[],
    opts: { relayTimeout: number; stateTimeout: number; userAgent: string },
  ): Promise<BotApiState | null> {
    const base = await this.resolveShowcaseUrl();
    for (const path of paths) {
      const timeoutMs = path.includes('relay-state') ? opts.relayTimeout : opts.stateTimeout;
      try {
        const res = await fetch(`${base}${path}`, {
          signal: AbortSignal.timeout(timeoutMs),
          headers: { Accept: 'application/json', 'User-Agent': opts.userAgent },
        });
        if (!res.ok) {
          this.logger.warn(`Showcase bot ${base}${path} HTTP ${res.status}`);
          continue;
        }
        const data = (await res.json()) as BotApiState;
        if (!data || typeof data !== 'object') continue;
        return data;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(`Showcase bot ${base}${path} fetch failed: ${msg}`);
      }
    }
    return null;
  }

  /** Race all candidate bot URLs in parallel for each path.
   *  Returns the first valid 200 JSON state — online if EITHER endpoint responds.
   *  Uses Promise.any so the first 200 wins without waiting on dead endpoints. */
  private async fetchStateFromCandidates(
    paths: string[],
    opts: { relayTimeout: number; stateTimeout: number; userAgent: string },
  ): Promise<BotApiState | null> {
    const bases = await this.resolveBotUrls();
    if (bases.length === 0) return null;
    for (const path of paths) {
      const timeoutMs = path.includes('relay-state') ? opts.relayTimeout : opts.stateTimeout;
      // Each probe rejects on failure/null so Promise.any only resolves on a real hit.
      const probes = bases.map(
        (base) =>
          new Promise<BotApiState>((resolve, reject) => {
            fetch(`${base}${path}`, {
              signal: AbortSignal.timeout(timeoutMs),
              headers: {
                Accept: 'application/json',
                'User-Agent': opts.userAgent,
              },
            })
              .then(async (res) => {
                if (!res.ok) {
                  this.logger.warn(`Bot ${base}${path} HTTP ${res.status}`);
                  reject(new Error(`HTTP ${res.status}`));
                  return;
                }
                const data = (await res.json()) as BotApiState;
                if (!data || typeof data !== 'object') {
                  reject(new Error('invalid state body'));
                  return;
                }
                if (bases.length > 1) {
                  this.logger.log(`Bot state fetched from ${base}${path}`);
                }
                resolve(data);
              })
              .catch((err: unknown) => {
                const msg = err instanceof Error ? err.message : String(err);
                this.logger.warn(`Bot ${base}${path} fetch failed: ${msg}`);
                reject(err instanceof Error ? err : new Error(msg));
              });
          }),
      );
      try {
        // First valid state wins; remaining probes are abandoned.
        return await Promise.any(probes);
      } catch {
        // All probes for this path failed — try the next path.
      }
    }
    return null;
  }

  /** True when the canonical Cloudflare showcase tunnel responds.
   *  The home Cloudflare tunnel has bimodal latency — pings usually land in
   *  ~0.3s but occasionally spike past 6s (cold cloudflared connection, host
   *  is on the other side of the world). The old 6s budget made the public
   *  agent-status dot flick red even when the bot was healthy, because
   *  getPublicAgentStatus() only falls through to the heavier state probe
   *  when `force=true`. 12s clears the observed tail latency while still
   *  failing fast when the tunnel is genuinely down. */
  async isReachable(force = false): Promise<boolean> {
    const cf = await this.resolveShowcaseUrl();
    try {
      const res = await fetch(`${cf}/api/ping`, {
        signal: AbortSignal.timeout(12_000),
        headers: { Accept: 'application/json', 'User-Agent': 'doxxedcrypto-relay/1.0' },
      });
      if (res.ok) return true;
    } catch {
      // fall through
    }
    if (!force) return false;
    const state = await this.fetchPublicShowcaseState(true);
    return Boolean(state);
  }

  async getLiveDashboard(agentName: string, force = false) {
    const botUrl = await this.resolveShowcaseUrl();
    const bot = await this.fetchPublicShowcaseState(force);
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
    const bases = [await this.resolveShowcaseUrl()];
    if (bases.length === 0 || !bases[0]) {
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
   *  Promise.any returns on the first 200 without waiting on dead endpoints. */
  async fetchHealth() {
    const bases = await this.resolveBotUrls();
    const probes: Promise<Record<string, unknown>>[] = [];
    for (const base of bases) {
      for (const path of ['/api/ping', '/health']) {
        probes.push(
          new Promise<Record<string, unknown>>((resolve, reject) => {
            fetch(`${base}${path}`, {
              signal: AbortSignal.timeout(8_000),
              headers: { Accept: 'application/json', 'User-Agent': 'doxxedcrypto-relay/1.0' },
            })
              .then(async (res) => {
                if (!res.ok) {
                  reject(new Error(`HTTP ${res.status}`));
                  return;
                }
                resolve((await res.json()) as Record<string, unknown>);
              })
              .catch((err: unknown) => {
                reject(err instanceof Error ? err : new Error(String(err)));
              });
          }),
        );
      }
    }
    try {
      return await Promise.any(probes);
    } catch {
      return null;
    }
  }

  /** Read-only proxy to the research analyzer (:9001) via the bot's public tunnel.
   *  The analyzer runs only on the LOCAL showcase bot (Cloudflare tunnel) — Fly does NOT run it.
   *  Try the Cloudflare tunnel for analyzer paths; fall back to Fly for non-analyzer state. */
  private async fetchAnalyzerProxy<T>(path: string, timeoutMs = 20_000): Promise<T | null> {
    // Analyzer proxy is only on the home showcase bot (Cloudflare tunnel :7002) — Fly does NOT run :9001.
    const cf = (await this.resolveBotUrl()) ?? this.DEFAULT_CF_URL;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const res = await fetch(`${cf}${path}`, {
          signal: AbortSignal.timeout(timeoutMs),
          headers: { Accept: 'application/json', 'User-Agent': 'doxxedcrypto-analyzer/1.0' },
        });
        if (!res.ok) {
          this.logger.warn(`Analyzer proxy ${cf}${path} HTTP ${res.status} (attempt ${attempt + 1})`);
          continue;
        }
        return (await res.json()) as T;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(`Analyzer proxy ${cf}${path} failed (attempt ${attempt + 1}): ${msg}`);
      }
    }
    return null;
  }

  async fetchAnalyzerSummary(): Promise<Record<string, unknown> | null> {
    return this.fetchAnalyzerProxy<Record<string, unknown>>('/api/analyzer/summary');
  }

  async fetchAnalyzerGenome(): Promise<Record<string, unknown> | null> {
    return this.fetchAnalyzerProxy<Record<string, unknown>>('/api/analyzer/genome', 12_000);
  }

  /** Cumulative full-session metrics from the canonical showcase bot's full `/api/state`
   *  (fresh cache 60s; last-known-good retained up to 10m on tunnel blips).
   *  Used when the :9001 analyzer proxy is intermittent/unavailable.
   *
   *  Display-only: always uses `fetchShowcaseCanonicalState` (home tunnel `/api/state`).
   *  Never slim `/api/relay-state` (lacks equity/trade_count/session_pnl) and never Fly
   *  (separate stale instance). Never invents $500 / 0 trades / $0 PnL defaults. */
  private cumulativeCache: { at: number; data: CumulativeSessionMetrics } | null = null;
  private readonly cumulativeCacheMs = 60_000;
  /** Serve last successful metrics through brief /api/state tunnel failures. */
  private readonly cumulativeStaleMs = 10 * 60_000;

  private serveStaleCumulativeMetrics(now: number, reason: string): CumulativeSessionMetrics | null {
    if (this.cumulativeCache && now - this.cumulativeCache.at < this.cumulativeStaleMs) {
      this.logger.warn(
        `Serving last-known-good cumulative metrics (${reason}, age ${Math.round((now - this.cumulativeCache.at) / 1000)}s)`,
      );
      return this.cumulativeCache.data;
    }
    return null;
  }

  /** Seed last-known-good display metrics from a successful analyzer summary. */
  seedCumulativeSessionMetrics(metrics: CumulativeSessionMetrics): void {
    this.cumulativeCache = { at: Date.now(), data: metrics };
  }

  async fetchCumulativeSessionMetrics(): Promise<CumulativeSessionMetrics | null> {
    const now = Date.now();
    if (this.cumulativeCache && now - this.cumulativeCache.at < this.cumulativeCacheMs) {
      return this.cumulativeCache.data;
    }
    let bot = await this.fetchShowcaseCanonicalState(true);
    if (!bot) {
      bot = await this.fetchCachedRelaySnapshot(this.cumulativeStaleMs);
    }
    if (!bot) {
      return this.serveStaleCumulativeMetrics(now, 'canonical /api/state unreachable');
    }
    const STARTING = 500;
    const analytics = bot.analytics ?? {};
    const balanceRaw =
      typeof bot.account_balance === 'number' && Number.isFinite(bot.account_balance)
        ? bot.account_balance
        : typeof bot.equity === 'number' && Number.isFinite(bot.equity)
          ? bot.equity
          : null;
    const tradeCountRaw =
      typeof analytics.total_trades === 'number' && Number.isFinite(analytics.total_trades)
        ? analytics.total_trades
        : typeof bot.trade_count === 'number' && Number.isFinite(bot.trade_count)
          ? bot.trade_count
          : typeof bot.trade_count_session === 'number' && Number.isFinite(bot.trade_count_session)
            ? bot.trade_count_session
            : null;
    const sessionPnlRaw =
      typeof bot.session_pnl_usd === 'number' && Number.isFinite(bot.session_pnl_usd)
        ? bot.session_pnl_usd
        : null;

    // Full /api/state must carry at least one real metric field. Slim/partial payloads
    // used to fall through to equity ?? 500 and trade_count ?? 0 with ok:true, which
    // overwrote the landing card with fabricated zeros whenever the analyzer was down.
    if (balanceRaw == null && tradeCountRaw == null && sessionPnlRaw == null) {
      this.logger.warn(
        'Canonical /api/state missing equity/trade_count/session_pnl — refusing fabricated defaults',
      );
      return this.serveStaleCumulativeMetrics(now, 'canonical /api/state missing metrics fields');
    }

    const totalTrades = tradeCountRaw != null ? Number(tradeCountRaw) : 0;
    const winRate = Number(analytics.win_rate ?? 0) || 0;
    const currentBalance =
      balanceRaw != null
        ? Number(balanceRaw)
        : sessionPnlRaw != null
          ? STARTING + sessionPnlRaw
          : null;
    if (currentBalance == null || !Number.isFinite(currentBalance)) {
      return this.serveStaleCumulativeMetrics(now, 'canonical /api/state balance unusable');
    }

    const totalPnlUsd =
      sessionPnlRaw != null
        ? Number(sessionPnlRaw.toFixed(2))
        : Number((currentBalance - STARTING).toFixed(2));
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
