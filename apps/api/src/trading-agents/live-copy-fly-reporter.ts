import { Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { createHash } from 'node:crypto';
import { PrismaService } from '../prisma/prisma.service';
import {
  approvalPermitsRelayCopy,
  readEnvelopeFlyApproval,
  signExecutorCapability,
  signLiveExecutionReport,
} from './live-copy-approval';
import { BITFINEX_BTC_PERP_SYMBOL, BITFINEX_REDUCE_ONLY_FLAG } from '../exchanges/bitfinex-api.client';

/**
 * The copier's protective stop as submitStopOrder sends it (type STOP, flags
 * BITFINEX_REDUCE_ONLY_FLAG). The spec test pins this against the client
 * source so the declared capability cannot drift from the real order.
 */
export const EXECUTOR_PROTECTIVE_STOP_SPEC = Object.freeze({ type: 'STOP' as const, flags: BITFINEX_REDUCE_ONLY_FLAG });

/**
 * Option 1 report-back (2026-10-09): every fill, protective stop, close and
 * executor error of a Fly-approved copy trade is POSTed to Fly as a signed
 * `railway_live_execution_report_v1` keyed by Fly's correlation id, with the
 * per-hop timeline. Read-only over durable SignalCycleEvent rows written by
 * the executor (never touches the exchange). Fly dedupes by report_id and
 * journals with fsync, so re-sending after a restart is idempotent.
 */
export const LIVE_EXECUTION_REPORT_SCHEMA = 'railway_live_execution_report_v1';
export const LIVE_COPY_REPORT_PATH = '/api/live-copy/execution-report';
const LOOKBACK_MS = 24 * 3_600_000;
export const EXECUTOR_CAPABILITY_SCHEMA = 'railway_executor_capability_v1';
export const EXECUTOR_CAPABILITY_PATH = '/api/live-copy/executor-capability';
const CAPABILITY_EVERY_MS = 60_000;

/**
 * Pure: the executor's capability report, derived from the exact protective
 * stop spec submitStopOrder sends (STOP + REDUCE_ONLY on tBTCF0:USTF0).
 */
export function buildExecutorCapabilityReport(nowMs: number): Record<string, unknown> {
  const flags = EXECUTOR_PROTECTIVE_STOP_SPEC.flags;
  const reduceOnly = (flags & BITFINEX_REDUCE_ONLY_FLAG) === BITFINEX_REDUCE_ONLY_FLAG;
  return {
    schema: EXECUTOR_CAPABILITY_SCHEMA,
    executor: 'railway-relay-executor',
    executor_version: process.env.RAILWAY_GIT_COMMIT_SHA?.slice(0, 12) ?? null,
    symbol: BITFINEX_BTC_PERP_SYMBOL,
    reduce_only_supported: reduceOnly,
    protective_stop: {
      order_type: EXECUTOR_PROTECTIVE_STOP_SPEC.type,
      flags,
      reduce_only: reduceOnly,
      placed: 'ONCE_AT_ENTRY_FILL',
    },
    sent_at_ts: nowMs / 1000,
  };
}

export type ReporterCycle = { id: string; tradeId: string; intentEnvelope: unknown };
export type ReporterEvent = {
  id: string;
  participantId: string | null;
  eventType: string;
  payload: unknown;
  createdAt: Date;
  platformReceivedAt: Date | null;
};
export type ReporterParticipant = { id: string; userId: string; stopLossConfirmedAt?: Date | null };
export type LiveExecutionReport = Record<string, unknown> & {
  schema: string;
  report_id: string;
  type: string;
  correlation_id: string;
  account: string;
  timeline: Record<string, number | null>;
};

const sec = (ms: number | null | undefined): number | null =>
  ms != null && Number.isFinite(ms) && ms > 0 ? Math.round(ms) / 1000 : null;
const isoMs = (v: unknown): number | null => {
  if (typeof v !== 'string' || !v) return null;
  const t = Date.parse(v);
  return Number.isFinite(t) ? t : null;
};
const num = (v: unknown): number | null => {
  const n = typeof v === 'string' ? Number(v) : (v as number);
  return typeof n === 'number' && Number.isFinite(n) ? n : null;
};
const rec = (v: unknown): Record<string, unknown> =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

/** Opaque account label (same shape as the fly-view route): never a user id or key. */
export function liveCopyAccountLabel(instanceId: string): string {
  return `acct-${String(instanceId).slice(0, 8)}`;
}

/**
 * Pure: durable executor events of ONE approved cycle -> signed-report bodies
 * (unsigned here). Hops: signal -> intent (Fly) -> website receive -> order
 * submit -> exchange ack -> fill -> stop ack -> stop confirmed -> report-back.
 */
export function buildLiveCopyReports(input: {
  cycle: ReporterCycle;
  correlationId: string;
  lane: string;
  events: ReporterEvent[];
  participants: ReporterParticipant[];
  accountFor: (userId: string) => string;
  nowMs: number;
}): LiveExecutionReport[] {
  const ctx = rec(rec(input.cycle.intentEnvelope).context);
  const approval = rec(ctx.fly_live_approval);
  const flySignalTs = num(approval.signal_at_ts);
  const flyIntentTs = num(approval.created_at_ts);
  const websiteReceivedMs = isoMs(ctx.platform_received_at)
    ?? input.events.find((e) => e.participantId == null && e.platformReceivedAt)?.platformReceivedAt?.getTime()
    ?? null;
  const out: LiveExecutionReport[] = [];
  const events = [...input.events].sort((a, b) => a.createdAt.getTime() - b.createdAt.getTime());
  for (const p of input.participants) {
    const mine = events.filter((e) => e.participantId === p.id);
    const tl: Record<string, number | null> = {
      fly_signal_at_ts: flySignalTs,
      fly_intent_emitted_at_ts: flyIntentTs,
      railway_received_at_ts: sec(websiteReceivedMs),
      order_sent_at_ts: null,
      exchange_ack_at_ts: null,
      fill_at_ts: null,
      stop_placed_at_ts: null,
      stop_confirmed_at_ts: null,
    };
    let entryPrice: number | null = null;
    let side: string | null = null;
    const push = (e: ReporterEvent, type: string, extra: Record<string, unknown>) => {
      out.push({
        schema: LIVE_EXECUTION_REPORT_SCHEMA,
        report_id: `rw-${e.id}-${type}`,
        type,
        correlation_id: input.correlationId,
        trade_id: input.cycle.tradeId,
        cycle_id: input.cycle.id,
        lane: input.lane,
        account: input.accountFor(p.userId),
        event_at_ts: sec(e.createdAt.getTime()),
        sent_at_ts: input.nowMs / 1000,
        timeline: { ...tl },
        ...extra,
      });
    };
    for (const e of mine) {
      const pl = rec(e.payload);
      const stages = rec(pl.stages);
      switch (e.eventType) {
        case 'EXECUTION_TIMING':
          if (pl.operation === 'ORDER_PLACED') {
            tl.order_sent_at_ts = tl.order_sent_at_ts ?? sec(num(stages.bitfinexRequestStartedAtMs));
            tl.exchange_ack_at_ts = tl.exchange_ack_at_ts ?? sec(num(stages.exchangeAckAtMs));
          }
          break;
        case 'ORDER_PLACED': {
          const ack = num(pl.entryExchangeAckAtMs);
          if (ack != null) tl.exchange_ack_at_ts = tl.exchange_ack_at_ts ?? sec(ack);
          entryPrice = num(pl.limit_price) ?? num(pl.limitPrice);
          side = typeof pl.direction === 'string' ? pl.direction : side;
          push(e, 'ORDER_PLACED', {
            order: {
              type: 'LIMIT',
              side,
              qty: num(pl.qty),
              price: entryPrice,
              exchange_order_id: pl.bitfinexOrderId ?? pl.bitfinex_order_id ?? null,
              client_order_id: pl.clientOrderId ?? null,
              margin_usd: num(pl.margin_usd),
              leverage: num(pl.leverage),
            },
          });
          break;
        }
        case 'FILLED': {
          const fillMs = isoMs(pl.exchange_fill_last_at) ?? isoMs(pl.fill_detected_at) ?? e.createdAt.getTime();
          tl.fill_at_ts = tl.fill_at_ts ?? sec(fillMs);
          const stopAck = isoMs(pl.stop_exchange_ack_at);
          if (stopAck != null) tl.stop_placed_at_ts = tl.stop_placed_at_ts ?? sec(stopAck);
          push(e, 'ORDER_FILLED', {
            fill: { price: num(pl.fill_price), qty: num(pl.qty), source: pl.fill_price_source ?? pl.fill_source ?? null },
          });
          if (pl.stop_loss_placed === true && pl.stopOrderId != null) {
            push(e, 'STOP_PLACED', {
              stop: { exchange_order_id: String(pl.stopOrderId), reduce_only: true, qty: num(pl.qty) },
            });
          } else if (pl.stop_loss_placed === false) {
            push(e, 'STOP_FAILED', {
              error: { code: 'STOP_NOT_PLACED_AT_FILL', message: 'Fill recorded without an exchange stop' },
            });
          }
          break;
        }
        case 'STOP_LOSS_ARMED': {
          // Recorded only after the executor authenticated the stop on
          // Bitfinex (exact qty/side/price/symbol, ACTIVE, reduce-only flag).
          const stopPrice = num(pl.stop_price);
          const ack = isoMs(pl.stop_exchange_ack_at);
          if (ack != null) tl.stop_placed_at_ts = tl.stop_placed_at_ts ?? sec(ack);
          tl.stop_confirmed_at_ts = tl.stop_confirmed_at_ts ?? sec(e.createdAt.getTime());
          const ref = entryPrice;
          push(e, 'STOP_CONFIRMED', {
            stop: {
              exchange_order_id: pl.stopOrderId != null ? String(pl.stopOrderId) : null,
              price: stopPrice,
              qty: num(pl.qty),
              reduce_only: true,
              verified_on_exchange: pl.stopOrderId != null && stopPrice != null,
              bp_from_entry: ref && stopPrice ? Math.round((Math.abs(stopPrice - ref) / ref) * 1e6) / 100 : null,
            },
          });
          break;
        }
        case 'EXIT':
          push(e, 'POSITION_CLOSED', {
            close: { price: num(pl.exit_price) ?? num(pl.fill_price), reason: pl.reason ?? pl.exit_reason ?? null },
          });
          break;
        case 'EXPIRED':
          push(e, 'ORDER_CANCELLED', { error: { code: 'ENTRY_EXPIRED', message: String(pl.reason ?? '').slice(0, 200) } });
          break;
        case 'ENTRY_SUBMISSION_UNKNOWN':
        case 'RECONCILE_CANCEL_FAILED':
        case 'NEGATIVE_EVIDENCE':
          push(e, 'ERROR', {
            error: { code: e.eventType, message: String(pl.reason ?? pl.error ?? '').slice(0, 200) },
          });
          break;
        default:
          break;
      }
    }
  }
  return out;
}

export const COPY_HOPS: ReadonlyArray<[string, string, string]> = [
  ['signal->intent', 'fly_signal_at_ts', 'fly_intent_emitted_at_ts'],
  ['intent->website_receive', 'fly_intent_emitted_at_ts', 'railway_received_at_ts'],
  ['website_receive->order_submit', 'railway_received_at_ts', 'order_sent_at_ts'],
  ['order_submit->exchange_ack', 'order_sent_at_ts', 'exchange_ack_at_ts'],
  ['exchange_ack->fill', 'exchange_ack_at_ts', 'fill_at_ts'],
  ['fill->stop_ack', 'fill_at_ts', 'stop_placed_at_ts'],
  ['stop_ack->stop_confirmed', 'stop_placed_at_ts', 'stop_confirmed_at_ts'],
  ['stop_confirmed->report_back', 'stop_confirmed_at_ts', 'report_back_at_ts'],
];
export const FILL_TO_STOP_GAP_SEC = 10;
export const ORDER_TO_ACK_GAP_SEC = 10;

function pct(values: number[], q: number): number | null {
  const v = values.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  const k = (v.length - 1) * q;
  const lo = Math.floor(k);
  const hi = Math.min(lo + 1, v.length - 1);
  return Math.round((v[lo] + (v[hi] - v[lo]) * (k - lo)) * 1000) / 1000;
}

/** Pure: per-account hop lags (p50/p95) and gaps from built reports. */
export function summarizeCopyStatus(reports: LiveExecutionReport[], nowMs: number, ackedAtMs?: Map<string, number>) {
  const chains = new Map<string, { account: string; correlation_id: string; lane: unknown; tl: Record<string, number | null>; types: Set<string>; unreported: number }>();
  for (const r of reports) {
    const key = `${r.account}|${r.correlation_id}`;
    const ch = chains.get(key) ?? { account: r.account, correlation_id: r.correlation_id, lane: r.lane, tl: {}, types: new Set<string>(), unreported: 0 };
    for (const [k, v] of Object.entries(r.timeline)) if (v != null && ch.tl[k] == null) ch.tl[k] = v;
    ch.types.add(r.type);
    const ackMs = ackedAtMs?.get(r.report_id);
    if (ackedAtMs && ackMs == null) ch.unreported += 1;
    if (ackMs != null && (r.type === 'STOP_CONFIRMED' || (r.type === 'ORDER_FILLED' && ch.tl.report_back_at_ts == null))) {
      ch.tl.report_back_at_ts = ackMs / 1000;
    }
    chains.set(key, ch);
  }
  const nowSec = nowMs / 1000;
  const accounts: Record<string, { lags: Record<string, number[]>; gaps: Array<Record<string, unknown>>; trades: number }> = {};
  for (const ch of chains.values()) {
    const a = (accounts[ch.account] ??= { lags: {}, gaps: [], trades: 0 });
    a.trades += 1;
    for (const [name, from, to] of COPY_HOPS) {
      const f = ch.tl[from];
      const t = ch.tl[to];
      if (f != null && t != null) (a.lags[name] ??= []).push(Math.round((t - f) * 1000) / 1000);
    }
    const gap = (code: string, detail: Record<string, unknown> = {}) =>
      a.gaps.push({ code, correlation_id: ch.correlation_id, lane: ch.lane, ...detail });
    if (ch.tl.order_sent_at_ts != null && ch.tl.exchange_ack_at_ts == null && nowSec - ch.tl.order_sent_at_ts > ORDER_TO_ACK_GAP_SEC) gap('ORDER_NOT_ACKED');
    if (ch.tl.fill_at_ts != null && !ch.types.has('STOP_CONFIRMED') && !ch.types.has('POSITION_CLOSED')
      && nowSec - ch.tl.fill_at_ts > FILL_TO_STOP_GAP_SEC) gap('FILL_WITHOUT_CONFIRMED_STOP');
    if (ch.types.has('STOP_FAILED')) gap('STOP_FAILED');
    if (ch.types.has('ERROR')) gap('EXECUTOR_ERROR');
    if (ch.unreported > 0) gap('REPORT_NOT_ACKED_BY_FLY', { unreported: ch.unreported });
  }
  const out: Record<string, unknown> = {};
  for (const [acct, a] of Object.entries(accounts)) {
    const lags: Record<string, { n: number; p50: number | null; p95: number | null; max: number | null }> = {};
    for (const [name] of COPY_HOPS) {
      const v = a.lags[name] ?? [];
      lags[name] = { n: v.length, p50: pct(v, 0.5), p95: pct(v, 0.95), max: v.length ? Math.max(...v) : null };
    }
    out[acct] = { trades: a.trades, lags_sec: lags, gaps: a.gaps.slice(0, 50), gap_count: a.gaps.length };
  }
  const allGaps = Object.values(accounts).reduce((n, a) => n + a.gaps.length, 0);
  return {
    schema: 'website_live_copy_status_v1',
    verdict: allGaps === 0 ? 'GREEN' : Object.values(accounts).some((a) => a.gaps.some((g) => g.code === 'FILL_WITHOUT_CONFIRMED_STOP' || g.code === 'STOP_FAILED')) ? 'RED' : 'AMBER',
    accounts: out,
    gap_count: allGaps,
    hops: COPY_HOPS.map(([n]) => n),
    computed_at: new Date(nowMs).toISOString(),
  };
}

@Injectable()
export class LiveCopyFlyReporterService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(LiveCopyFlyReporterService.name);
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  readonly acked = new Map<string, number>();
  private readonly lastAttempt = new Map<string, number>();
  readonly stats = { lastRunAt: 0, sent: 0, failed: 0, lastError: null as string | null, pending: 0 };
  readonly capability = { lastSentAt: 0, lastOkAt: 0, lastError: null as string | null };
  private lastCapabilityAttempt = 0;

  constructor(
    private readonly prisma: PrismaService,
    private readonly config: ConfigService,
  ) {}

  onModuleInit(): void {
    if (this.config.get<string>('LIVE_COPY_REPORTER_ENABLED') === 'false') return;
    if (process.env.NODE_ENV === 'test') return;
    this.timer = setInterval(() => void this.tick(), 5_000);
    this.timer.unref?.();
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
  }

  private flyUrl(): string {
    return (this.config.get<string>('FLY_BOT_URL')?.trim() || 'https://doxed-btc-bot.fly.dev').replace(/\/$/, '');
  }

  /** Reports for approval-backed cycles in the lookback window (pure read). */
  async collect(nowMs = Date.now()): Promise<LiveExecutionReport[]> {
    const secret = this.config.get<string>('SHOWCASE_WEBHOOK_SECRET');
    const cycles = await this.prisma.signalCycle.findMany({
      where: { updatedAt: { gte: new Date(nowMs - LOOKBACK_MS) } },
      select: { id: true, tradeId: true, agentId: true, intentEnvelope: true },
      orderBy: { updatedAt: 'desc' },
      take: 300,
    });
    const reports: LiveExecutionReport[] = [];
    for (const c of cycles) {
      const verdict = approvalPermitsRelayCopy(readEnvelopeFlyApproval(c.intentEnvelope), c.tradeId, secret);
      if (!verdict.ok) continue;
      const [participants, events] = await Promise.all([
        this.prisma.signalCycleParticipant.findMany({
          where: { cycleId: c.id },
          select: { id: true, userId: true, stopLossConfirmedAt: true },
        }),
        this.prisma.signalCycleEvent.findMany({
          where: { cycleId: c.id },
          select: { id: true, participantId: true, eventType: true, payload: true, createdAt: true, platformReceivedAt: true },
          orderBy: { createdAt: 'asc' },
          take: 500,
        }),
      ]);
      if (!participants.length) continue;
      const instances = await this.prisma.tradingAgentInstance.findMany({
        where: { agentId: c.agentId, userId: { in: participants.map((p) => p.userId) } },
        select: { id: true, userId: true },
      });
      const byUser = new Map(instances.map((i) => [i.userId, liveCopyAccountLabel(i.id)]));
      reports.push(...buildLiveCopyReports({
        cycle: c,
        correlationId: String(verdict.approval.correlation_id),
        lane: String(verdict.approval.research_lane),
        events,
        participants,
        accountFor: (u) => byUser.get(u) ?? `acct-${createHash('sha256').update(u).digest('hex').slice(0, 8)}`,
        nowMs,
      }));
    }
    return reports;
  }

  /** Signed capability report to Fly, at most once a minute (best effort). */
  async sendCapability(secret: string, nowMs = Date.now()): Promise<boolean> {
    if (nowMs - this.lastCapabilityAttempt < CAPABILITY_EVERY_MS) return false;
    this.lastCapabilityAttempt = nowMs;
    const body = JSON.stringify(buildExecutorCapabilityReport(nowMs));
    const sig = signExecutorCapability(body, secret);
    if (!sig) return false;
    this.capability.lastSentAt = nowMs;
    try {
      const res = await fetch(`${this.flyUrl()}${EXECUTOR_CAPABILITY_PATH}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'X-Executor-Capability-Signature': sig },
        body,
        signal: AbortSignal.timeout(5_000),
      });
      if (res.ok) {
        this.capability.lastOkAt = Date.now();
        this.capability.lastError = null;
        return true;
      }
      this.capability.lastError = `HTTP_${res.status}`;
    } catch (err) {
      this.capability.lastError = err instanceof Error ? err.name : 'FETCH_FAILED';
    }
    return false;
  }

  async tick(): Promise<void> {
    if (this.running) return;
    this.running = true;
    try {
      const secret = this.config.get<string>('SHOWCASE_WEBHOOK_SECRET');
      if (!String(secret ?? '').trim()) return;
      const nowMs = Date.now();
      await this.sendCapability(secret as string, nowMs);
      const reports = (await this.collect(nowMs)).filter((r) => !this.acked.has(r.report_id));
      this.stats.pending = reports.length;
      for (const r of reports) {
        const last = this.lastAttempt.get(r.report_id) ?? 0;
        if (nowMs - last < 15_000) continue;
        this.lastAttempt.set(r.report_id, nowMs);
        const body = JSON.stringify({ ...r, sent_at_ts: Date.now() / 1000 });
        const sig = signLiveExecutionReport(body, secret);
        if (!sig) return;
        try {
          const res = await fetch(`${this.flyUrl()}${LIVE_COPY_REPORT_PATH}`, {
            method: 'POST',
            headers: { 'content-type': 'application/json', 'X-Live-Report-Signature': sig },
            body,
            signal: AbortSignal.timeout(5_000),
          });
          if (res.ok) {
            this.acked.set(r.report_id, Date.now());
            this.stats.sent += 1;
          } else {
            this.stats.failed += 1;
            this.stats.lastError = `HTTP_${res.status}`;
          }
        } catch (err) {
          this.stats.failed += 1;
          this.stats.lastError = err instanceof Error ? err.name : 'FETCH_FAILED';
        }
      }
      if (this.acked.size > 20_000) this.acked.clear();
    } catch (err) {
      this.stats.lastError = err instanceof Error ? err.message.slice(0, 120) : 'TICK_FAILED';
      this.logger.warn(`[LIVE-COPY-REPORTER] ${this.stats.lastError}`);
    } finally {
      this.stats.lastRunAt = Date.now();
      this.running = false;
    }
  }
}
