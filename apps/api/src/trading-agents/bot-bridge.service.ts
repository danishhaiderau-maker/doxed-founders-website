import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import {
  BotActivityEntry,
  BotApiState,
  mapBotStateToActivity,
  mapBotStateToAgentStats,
  mapBotStateToDashboard,
} from './bot-state.mapper';
import { ShowcaseSnapshotService } from './showcase-snapshot.service';
import { CANONICAL_SHOWCASE_BOT_URL } from './canonical-showcase-runtime';
import {
  FLY_CANONICAL_LOCK_ENFORCED,
  isFlyDeclaredDashboardUrl,
} from './fly-canonical-lock';

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
  /**
   * Wall-clock of the most recent SUCCESSFUL live-bot state fetch. NEVER
   * reset by invalidateCache() — relay-event webhooks call invalidateCache
   * on every push (which is a sign of life, not failure), so coupling
   * lastLiveFetchAt to cache invalidation would wipe our liveness signal
   * on every bot event. Updated only by successful fetches.
   */
  private lastLiveFetchAt = 0;
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
  /**
   * The canonical bot lives in Australia while Railway currently runs in SFO.
   * An 800ms deadline was below the observed cross-region tail latency, so every
   * poll aborted and immediately tried the much heavier `/api/state` fallback.
   * Keep one bounded relay-state request inside the user's 2-3 second target;
   * on failure the money path remains fail-closed and waits for the next signed
   * webhook/poll instead of creating a request storm.
   */
  private readonly executionRelayTimeoutMs = Math.max(
    1_500,
    Math.min(3_000, Number(process.env.BOT_BRIDGE_EXEC_TIMEOUT_MS ?? 2_500)),
  );
  private readonly pushedExecutionMaxAgeMs = Math.max(
    4_000,
    Math.min(
      15_000,
      Number(process.env.BOT_BRIDGE_PUSHED_EXEC_MAX_AGE_MS ?? 8_000),
    ),
  );
  /** Execution-path cache — one canonical showcase bot only; no source race. */
  private execCached: BotApiState | null = null;
  private execFetchAt = 0;
  /**
   * A pushed snapshot is transport data, not proof of who owns the strategy.
   * The API must first contact the source-controlled Fly URL directly and see
   * the same instance/revision. This prevents an old publisher that still has
   * the shared secret from establishing its own owner identity.
   */
  private directFlyOwnerProof: {
    instanceId: string;
    sourceRevision: string;
    pid: number | null;
    port: number;
    seenAt: number;
  } | null = null;
  private readonly directFlyOwnerProofMaxAgeMs = 60_000;
  /**
   * All canonical `/api/relay-state` readers share one short-lived live fetch.
   * The Agent Hub, relay subscriber, session sync and webhook wake path can all
   * request a forced refresh in the same tick. Without coalescing, those calls
   * fan out into dozens of identical tunnel requests and trigger the showcase
   * bot's HTTP 429 guard, leaving live copy permanently stuck in safe mode.
   */
  private readonly showcaseFetchInFlight = new Map<
    'shared' | 'execution',
    Promise<BotApiState | null>
  >();
  private showcaseFetchCached: BotApiState | null = null;
  private showcaseFetchAt = 0;
  private showcaseFetchBackoffUntil = 0;
  private showcaseRateLimitLogAt = 0;
  private readonly showcaseFetchMinIntervalMs = Math.max(
    500,
    Number(process.env.BOT_BRIDGE_FETCH_COALESCE_MS ?? 1500),
  );

  constructor(
    private readonly config: ConfigService,
    private readonly showcaseSnapshot: ShowcaseSnapshotService,
  ) {}

  /** Fly is the authoritative 24/7 owner. */
  private readonly DEFAULT_CANONICAL_URL = CANONICAL_SHOWCASE_BOT_URL;

  getBotUrl(): string {
    return this.DEFAULT_CANONICAL_URL;
  }

  /**
   * Resolve the single source-controlled owner. Database rows and environment
   * variables are deliberately not routing inputs: both previously allowed an
   * old Railway or laptop URL to silently take control of the money path.
   */
  async resolveBotUrl(): Promise<string> {
    return this.DEFAULT_CANONICAL_URL;
  }

  /** Canonical showcase URL — exactly one configured owner, Fly by default. */
  async resolveShowcaseUrl(): Promise<string> {
    return this.resolveBotUrl();
  }

  async isEnabledAsync(): Promise<boolean> {
    return (await this.resolveBotUrl()) === this.DEFAULT_CANONICAL_URL;
  }

  isEnabled(): boolean {
    return true;
  }

  invalidateCache() {
    this.cached = null;
    this.lastFetchAt = 0;
  }

  /** Wall-clock of the most recent SUCCESSFUL live-bot state fetch. NEVER
   *  reset by invalidateCache() — relay-event webhooks call invalidateCache
   *  on every bot push (which is itself a sign of life), so coupling this
   *  timestamp to cache invalidation would wipe our liveness signal on
   *  every bot event. Used by getPublicAgentStatus() as a last-resort
   *  liveness signal when the tunnel is momentarily unreachable from
   *  Railway — the dashboard polls the same BotBridge instance every few
   *  seconds, so a fresh timestamp here proves the bot is alive even when
   *  this specific status probe loses the tunnel race. */
  getLastLiveFetchAt(): number {
    return this.lastLiveFetchAt;
  }

  /**
   * Relay webhooks may mutate trading state only when their instance identity
   * matches a recently fetched canonical dashboard owner.
   *
   * FIX 2 — stale records are reported as null (already evicted on read by
   * the TTL check). The in-memory field is intentionally not cleared on a
   * miss so a transient clock skew or GC pause does not wipe a still-valid
   * proof mid-window; the age check is authoritative. Callers must treat a
   * null return as "no active canonical owner" and fail closed (relay
   * webhooks surface this as 401 "Active dashboard owner is not currently
   * confirmed"). Use {@link evictStaleDirectFlyOwnerProof} to explicitly
   * clear a record that is known to be stale (e.g. after a Fly rolling
   * deploy is observed via a changed source_git_rev on a fresh fetch).
   */
  getCachedDashboardOwnerIdentity(): {
    instanceId: string;
    pid: number | null;
    port: number | null;
    seenAt: number;
  } | null {
    const proof = this.directFlyOwnerProof;
    if (
      !proof
      || Date.now() - proof.seenAt < 0
      || Date.now() - proof.seenAt > this.directFlyOwnerProofMaxAgeMs
    ) return null;
    return {
      instanceId: proof.instanceId,
      pid: proof.pid,
      port: proof.port,
      seenAt: proof.seenAt,
    };
  }

  /**
   * Explicitly clear the cached canonical owner proof. Called when a fresh
   * direct fetch returns a different instanceId/revision (rolling deploy)
   * or when an upper layer observes that the recorded proof no longer
   * matches the canonical Fly process. Idempotent.
   */
  evictStaleDirectFlyOwnerProof(): void {
    this.directFlyOwnerProof = null;
  }

  /**
   * Read the last canonical execution snapshot without starting a tunnel
   * request. Signed owner webhooks use this only for non-authoritative caps
   * and observability while their exact HMAC-authenticated limit takes the
   * entry fast path. A stale/foreign/Fly snapshot is never returned.
   */
  getCachedExecutionState(maxAgeMs = 10_000): BotApiState | null {
    const ageMs = Date.now() - this.execFetchAt;
    const instanceId = this.execCached?.bot_instance_id?.trim();
    if (
      !this.execCached
      || !instanceId
      || !this.matchesDirectFlyOwnerProof(this.execCached)
      || ageMs < 0
      || ageMs > maxAgeMs
    ) {
      return null;
    }
    return this.execCached;
  }

  /** Agent Hub showcase desk — canonical Fly bot only.
   *  Uses the fresh signed platform snapshot first, then the same Fly owner. */
  async fetchPublicShowcaseState(force = true): Promise<BotApiState | null> {
    const now = Date.now();
    if (!force && this.cached && now - this.lastFetchAt < this.cacheMs) {
      return this.cached;
    }
    const pushed = await this.fetchCachedRelaySnapshot(15_000);
    if (pushed && this.isCanonicalPushedSnapshot(pushed, 15_000)) {
      this.cached = pushed;
      this.lastFetchAt = now;
      this.lastLiveFetchAt = now;
      return pushed;
    }
    const data = await this.fetchShowcaseState(['/api/relay-state', '/api/state'], {
      relayTimeout: 20_000,
      stateTimeout: 30_000,
      userAgent: 'doxxedcrypto-showcase/1.0',
    });
    if (data) {
      const fetchedAt = Date.now();
      this.cached = data;
      this.lastFetchAt = fetchedAt;
      this.lastLiveFetchAt = fetchedAt;
      return data;
    }
    return null;
  }

  /** Cache-first relay snapshot pushed by the canonical Fly bot. */
  private async fetchCachedRelaySnapshot(maxAgeMs = 15_000): Promise<BotApiState | null> {
    try {
      const cached = await this.showcaseSnapshot.getCachedSnapshot();
      if (!cached.snapshot) return null;
      const ageMs = cached.at ? Date.now() - cached.at.getTime() : Number.MAX_SAFE_INTEGER;
      if (ageMs > maxAgeMs) {
        this.logger.warn(`Cached showcase snapshot stale (${Math.round(ageMs / 1000)}s) — falling back to live bot`);
        return null;
      }
      const state = { ...(cached.snapshot as BotApiState) };
      state.snapshot_seq = cached.snapshot_seq;
      state.snapshot_source = 'railway_cache';
      return state;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.logger.warn(`Cached snapshot read failed: ${msg}`);
      return null;
    }
  }

  /** Execution + relay sim — the single configured canonical showcase only.
   *  Fly now owns that role. There is deliberately no Fly/home race: entry anchors,
   *  chase, abandon checks, dedupe and mirror diff must all come from one identity.
   *  Fail-closed: when the configured owner is unreachable this returns null and the
   *  executor HOLDS. */
  private isCanonicalPushedSnapshot(state: BotApiState, maxAgeMs: number): boolean {
    const instanceId = state.bot_instance_id?.trim();
    const sourceRevision = state.source_git_rev?.trim();
    const sourceAt = state.server_ts ? Date.parse(state.server_ts) : Number.NaN;
    const sourceAgeMs = Date.now() - sourceAt;
    return Boolean(
      state.snapshot_source === 'railway_cache'
      && state.dashboard_owner === true
      && state.dashboard_port === 7002
      && instanceId
      && sourceRevision
      && !state.api_state_error
      && Number.isFinite(sourceAt)
      && sourceAgeMs >= -10_000
      && sourceAgeMs <= maxAgeMs
      && this.matchesDirectFlyOwnerProof(state),
    );
  }

  private recordDirectFlyOwnerProof(state: BotApiState): boolean {
    const instanceId = state.bot_instance_id?.trim();
    const sourceRevision = state.source_git_rev?.trim();
    const sourceAt = state.server_ts ? Date.parse(state.server_ts) : Number.NaN;
    const sourceAgeMs = Date.now() - sourceAt;
    if (
      state.dashboard_owner !== true
      || state.dashboard_port !== 7002
      || !instanceId
      || !sourceRevision
      || state.api_state_error
      || !Number.isFinite(sourceAt)
      || sourceAgeMs < -10_000
      || sourceAgeMs > 120_000
    ) {
      return false;
    }
    // FIX 2 — Fly-origin proof. When config/fly-canonical.lock.json is
    // enforced, the responding process must declare its public dashboard
    // URL as the canonical Fly URL. A desktop process (loopback :7002 or
    // LAN) reports a non-Fly URL in /api/state, so this guard rejects a
    // stale or rogue desktop publisher even if it shares the relay
    // BOT_CONTROL_SECRET. The instance-id format `dashboard-7002-pid-*`
    // alone is not sufficient because both Fly and the legacy desktop
    // owner use the same format string in bot.py.
    //
    // The check is enforced when `dashboard_url` is present in the
    // payload (always the case for /api/state). /api/relay-state does
    // not currently surface this field, so when it's absent we fall
    // back to the existing owner checks above; the X-Desktop-Mirror
    // header guard in fetchShowcaseStateOnce still rejects proxied
    // responses regardless of which endpoint served them.
    if (
      FLY_CANONICAL_LOCK_ENFORCED
      && typeof state.dashboard_url === 'string'
      && state.dashboard_url.trim() !== ''
      && !isFlyDeclaredDashboardUrl(state.dashboard_url)
    ) {
      this.logger.warn(
        `Rejecting direct owner proof: dashboard_url='${state.dashboard_url}' is not canonical Fly`,
      );
      return false;
    }
    this.directFlyOwnerProof = {
      instanceId,
      sourceRevision,
      pid: typeof state.dashboard_pid === 'number' ? state.dashboard_pid : null,
      port: 7002,
      seenAt: Date.now(),
    };
    return true;
  }

  private matchesDirectFlyOwnerProof(state: BotApiState): boolean {
    const proof = this.directFlyOwnerProof;
    if (!proof) return false;
    const proofAgeMs = Date.now() - proof.seenAt;
    return Boolean(
      proofAgeMs >= 0
      && proofAgeMs <= this.directFlyOwnerProofMaxAgeMs
      && state.dashboard_owner === true
      && state.dashboard_port === proof.port
      && state.bot_instance_id?.trim() === proof.instanceId
      && state.source_git_rev?.trim() === proof.sourceRevision,
    );
  }

  async fetchStateForExecution(force = true): Promise<BotApiState | null> {
    const now = Date.now();
    // `force` bypasses the display cache, not this execution cache. Otherwise
    // every caller in the same relay tick creates an identical tunnel request.
    if (this.execCached && now - this.execFetchAt < this.execCacheMs) {
      return this.execCached;
    }
    const pushed = await this.fetchCachedRelaySnapshot(this.pushedExecutionMaxAgeMs);
    if (pushed && this.isCanonicalPushedSnapshot(pushed, this.pushedExecutionMaxAgeMs)) {
      this.execCached = pushed;
      this.execFetchAt = Date.now();
      return pushed;
    }
    const data = await this.fetchShowcaseState(
      ['/api/relay-execution-state', '/api/relay-state'],
      {
        // The signed webhook is the primary money-path transport. Canonical
        // polling is its fail-closed backstop. Prefer the bounded execution
        // snapshot; retain /api/relay-state only as a rolling-deploy fallback.
        relayTimeout: this.executionRelayTimeoutMs,
        stateTimeout: this.executionRelayTimeoutMs,
        userAgent: 'doxxedcrypto-relay/1.0',
        lane: 'execution',
      },
    );
    if (data && this.matchesDirectFlyOwnerProof(data)) {
      this.execCached = data;
      this.execFetchAt = Date.now();
      return data;
    }
    if (data) {
      this.logger.warn('Canonical bot did not prove dashboard ownership; execution held');
    }
    // Canonical unreachable — execution must hold; never race another bot instance.
    this.execCached = null;
    this.execFetchAt = 0;
    return null;
  }

  /** Canonical Fly state for session-epoch tracking — never races owners. */
  async fetchShowcaseCanonicalState(
    force = true,
    allowPushedSnapshot = true,
  ): Promise<BotApiState | null> {
    const now = Date.now();
    if (!force && this.cached && now - this.lastFetchAt < this.cacheMs) {
      return this.cached;
    }
    const pushed = allowPushedSnapshot
      ? await this.fetchCachedRelaySnapshot(15_000)
      : null;
    if (pushed && this.isCanonicalPushedSnapshot(pushed, 15_000)) {
      this.cached = pushed;
      this.lastFetchAt = now;
      this.lastLiveFetchAt = now;
      return pushed;
    }
    const cf = await this.resolveBotUrl();
    try {
      const adminToken = this.config.get<string>('BOT_ADMIN_TOKEN')?.trim();
      const headers: Record<string, string> = {
        Accept: 'application/json',
        'User-Agent': 'doxxedcrypto-sync/1.0',
      };
      if (adminToken) headers['X-Bot-Admin-Token'] = adminToken;
      const res = await fetch(`${cf}/api/state`, {
        signal: AbortSignal.timeout(20_000),
        headers,
      });
      if (!res.ok) {
        this.logger.warn(`Canonical showcase ${cf}/api/state HTTP ${res.status}`);
        return null;
      }
      const data = (await res.json()) as BotApiState;
      if (!data || typeof data !== 'object') return null;
      if (!this.recordDirectFlyOwnerProof(data)) {
        this.logger.warn('Canonical Fly /api/state did not prove exact owner identity');
        return null;
      }
      this.cached = data;
      this.lastFetchAt = now;
        this.lastLiveFetchAt = now;
      return data;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.logger.warn(`Canonical showcase ${cf}/api/state fetch failed: ${msg}`);
      return null;
    }
  }

  /** Admin panels — canonical Fly owner only. Cache-first, then live Fly state. */
  async fetchStateForAdmin(force = true): Promise<BotApiState | null> {
    const now = Date.now();
    if (!force && this.cached && now - this.lastFetchAt < this.cacheMs) {
      return this.cached;
    }

    const cached = await this.fetchCachedRelaySnapshot();
    if (cached && this.isCanonicalPushedSnapshot(cached, 15_000)) {
      this.cached = cached;
      this.lastFetchAt = now;
        this.lastLiveFetchAt = now;
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
        this.lastLiveFetchAt = now;
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
      if (cached && this.isCanonicalPushedSnapshot(cached, 15_000)) {
        this.cached = cached;
        this.lastFetchAt = now;
        this.lastLiveFetchAt = now;
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
        this.lastLiveFetchAt = now;
      return data;
    }

    this.invalidateCache();
    return null;
  }

  /** Fetch state from the canonical Fly owner only (split-brain guard). */
  private async fetchShowcaseState(
    paths: string[],
    opts: {
      relayTimeout: number;
      stateTimeout: number;
      userAgent: string;
      lane?: 'shared' | 'execution';
    },
  ): Promise<BotApiState | null> {
    const now = Date.now();
    if (
      this.showcaseFetchCached
      && now - this.showcaseFetchAt < this.showcaseFetchMinIntervalMs
    ) {
      return this.showcaseFetchCached;
    }
    const lane = opts.lane ?? 'shared';
    const inFlight = this.showcaseFetchInFlight.get(lane);
    if (inFlight) return inFlight;
    if (now < this.showcaseFetchBackoffUntil) return null;

    const task = this.fetchShowcaseStateOnce(paths, opts);
    this.showcaseFetchInFlight.set(lane, task);
    try {
      const data = await task;
      if (data) {
        this.showcaseFetchCached = data;
        this.showcaseFetchAt = Date.now();
        this.showcaseFetchBackoffUntil = 0;
      }
      return data;
    } finally {
      if (this.showcaseFetchInFlight.get(lane) === task) {
        this.showcaseFetchInFlight.delete(lane);
      }
    }
  }

  private async fetchShowcaseStateOnce(
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
        // FIX 2 — reject desktop-mirrored responses when verifying direct
        // Fly ownership. The desktop :7002 proxy (scripts/fly-dashboard-proxy.py)
        // adds `X-Desktop-Mirror: fly` to every response. When the lock is
        // enforced, the canonical-owner proof must come from Fly itself,
        // not a local loopback proxy that happens to share the relay
        // secret. Without this guard, a misconfigured direct-Fly fetch
        // URL pointing at a desktop proxy would still satisfy the proof.
        if (
          FLY_CANONICAL_LOCK_ENFORCED
          && (res.headers.get('x-desktop-mirror') || '').toLowerCase() === 'fly'
        ) {
          this.logger.warn(
            `Canonical Fly ${base}${path} was served by the desktop mirror — rejecting owner proof`,
          );
          continue;
        }
        if (res.status === 429) {
          const retryAfterSec = Number(res.headers.get('retry-after') ?? 0);
          const backoffMs = Math.min(
            30_000,
            Math.max(
              2_000,
              Number.isFinite(retryAfterSec) && retryAfterSec > 0
                ? retryAfterSec * 1000
                : 5_000,
            ),
          );
          this.showcaseFetchBackoffUntil = Date.now() + backoffMs;
          if (Date.now() - this.showcaseRateLimitLogAt >= 5_000) {
            this.showcaseRateLimitLogAt = Date.now();
            this.logger.warn(
              `Showcase bot ${base}${path} HTTP 429; coalesced bridge backing off ${backoffMs}ms`,
            );
          }
          // `/api/state` shares the same limiter. Falling through would double
          // the request storm instead of giving the bot time to recover.
          return null;
        }
        if (!res.ok) {
          this.logger.warn(`Showcase bot ${base}${path} HTTP ${res.status}`);
          continue;
        }
        const data = (await res.json()) as BotApiState;
        if (!data || typeof data !== 'object') continue;
        if (!this.recordDirectFlyOwnerProof(data)) {
          this.logger.warn(
            `Canonical Fly ${base}${path} did not prove exact owner identity`,
          );
          continue;
        }
        return data;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(`Showcase bot ${base}${path} fetch failed: ${msg}`);
      }
    }
    return null;
  }

  /** True when either canonical Fly health endpoint responds. */
  async isReachable(force = false): Promise<boolean> {
    const pushed = await this.fetchCachedRelaySnapshot(15_000);
    if (pushed && this.isCanonicalPushedSnapshot(pushed, 15_000)) return true;
    if (await this.isFlyHealthReachable()) return true;
    if (!force) return false;
    const state = await this.fetchPublicShowcaseState(true);
    return Boolean(state);
  }

  /**
   * Lightweight canonical-Fly reachability probe that never falls through to
   * a heavy /api/state fetch. Used to disambiguate "Fly truly offline" from
   * "Fly online but state stale" without coupling dashboard liveness to the
   * slow state endpoint. Mirrors probePublicBotHealth but kept private so the
   * bridge stays the single owner of canonical-URL resolution.
   */
  async isFlyHealthReachable(): Promise<boolean> {
    const pushed = await this.fetchCachedRelaySnapshot(15_000);
    if (pushed && this.isCanonicalPushedSnapshot(pushed, 15_000)) return true;
    const base = await this.resolveShowcaseUrl();
    const probes: Promise<boolean>[] = [];
    for (const path of ['/api/ping', '/health'] as const) {
      probes.push(
        (async () => {
          const res = await fetch(`${base}${path}`, {
            signal: AbortSignal.timeout(5_000),
            headers: { Accept: 'application/json', 'User-Agent': 'doxxedcrypto-relay/1.0' },
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return true as const;
        })(),
      );
    }
    try {
      await Promise.any(probes);
      return true;
    } catch {
      return false;
    }
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
    const adminToken = this.config.get<string>('BOT_ADMIN_TOKEN')?.trim();
    if (!adminToken) {
      return {
        ok: false,
        error: 'BOT_ADMIN_TOKEN is not configured for canonical Fly controls',
      };
    }
    const controlSecret = this.config.get<string>('BOT_CONTROL_SECRET')?.trim();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'X-Bot-Admin-Token': adminToken,
    };
    if (controlSecret) {
      // Retained only for bot builds that also audit the platform identity.
      // It never substitutes for the dedicated admin credential above.
      headers['X-Bot-Control-Secret'] = controlSecret;
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

  /** Health probe — race Fly `/api/ping` and `/health`; first 200 wins. */
  async fetchHealth() {
    const state = await this.fetchPublicShowcaseState(true);
    if (state) {
      return {
        ok: true,
        bot_instance_id: state.bot_instance_id ?? null,
        dashboard_owner: state.dashboard_owner ?? false,
        dashboard_port: state.dashboard_port ?? null,
        source_git_rev: state.source_git_rev ?? null,
        server_ts: state.server_ts ?? null,
        source: state.snapshot_source ?? 'fly-direct',
      };
    }
    const base = await this.resolveShowcaseUrl();
    const probes: Promise<Record<string, unknown>>[] = [];
    for (const path of ['/api/ping', '/health']) {
      probes.push(
        new Promise<Record<string, unknown>>((resolve, reject) => {
          fetch(`${base}${path}`, {
            signal: AbortSignal.timeout(5_000),
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
    try {
      return await Promise.any(probes);
    } catch {
      return null;
    }
  }

  /** Read-only analyzer snapshot exposed by the canonical Fly dashboard API. */
  private async fetchAnalyzerProxy<T>(path: string, timeoutMs = 20_000): Promise<T | null> {
    const cf = await this.resolveBotUrl();
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
   *  Display-only: uses the canonical Fly `/api/state`; never invents
   *  $500 / 0 trades / $0 PnL defaults from a slim relay payload. */
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
    // Cumulative analytics require the authenticated full Fly /api/state.
    // The fresh Railway relay snapshot intentionally contains only the
    // execution/showcase subset and cannot prove full-session totals.
    let bot = await this.fetchShowcaseCanonicalState(true, false);
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
    const explicitWinRate = Number(analytics.win_rate);
    const aggregateWins = Number(analytics.wins);
    const aggregateLosses = Number(analytics.losses);
    const aggregateDecisions = aggregateWins + aggregateLosses;
    const winRate = Number.isFinite(explicitWinRate)
      ? explicitWinRate
      : Number.isFinite(aggregateWins)
        && Number.isFinite(aggregateLosses)
        && aggregateWins >= 0
        && aggregateLosses >= 0
        && aggregateDecisions > 0
        ? (aggregateWins / aggregateDecisions) * 100
        : totalTrades === 0
          ? 0
          : null;
    if (winRate == null) {
      return this.serveStaleCumulativeMetrics(
        now,
        'canonical /api/state omitted cumulative win-rate evidence',
      );
    }
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
