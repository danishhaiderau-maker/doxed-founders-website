import { Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { createHash } from 'node:crypto';
import { PrismaService } from '../prisma/prisma.service';
import { setLiveCopyReportKickListener } from './live-copy-report-kick';
import {
  approvalPermitsRelayCopy,
  readEnvelopeFlyApproval,
  signExecutorCapability,
  signLiveExecutionReport,
} from './live-copy-approval';
import { BITFINEX_BTC_PERP_SYMBOL, BITFINEX_REDUCE_ONLY_FLAG } from '../exchanges/bitfinex-api.client';
import {
  latestBitfinexPositionIdStamp,
  type BitfinexPositionIdStampPayload,
} from './bitfinex-position-id-stamp';

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
/** An unresolved pre-submit fence is an error only after this long. */
export const ENTRY_SUBMISSION_UNKNOWN_GRACE_MS = 60_000;

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
/** Earliest positive finite epoch-ms among candidates (null if none). */
export function earliestReceiveMs(...values: Array<number | null | undefined>): number | null {
  const v = values.filter((x): x is number => x != null && Number.isFinite(x) && x > 0);
  return v.length ? Math.min(...v) : null;
}
const rec = (v: unknown): Record<string, unknown> =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

/** Opaque account label (same shape as the fly-view route): never a user id or key. */
export function liveCopyAccountLabel(instanceId: string): string {
  return `acct-${String(instanceId).slice(0, 8)}`;
}

/** Report `position` block from a BITFINEX_POSITION_ID_STAMP event, or null. */
export function liveCopyPositionBlock(stamp: BitfinexPositionIdStampPayload | null): {
  bitfinex_position_id: number | null;
  position_side: 'LONG' | 'SHORT' | null;
  read_at_ts: number | null;
  status: string;
  source: string;
} | null {
  if (!stamp) return null;
  const pid = typeof stamp.bitfinex_position_id === 'number'
    && Number.isSafeInteger(stamp.bitfinex_position_id)
    && stamp.bitfinex_position_id > 0
    ? stamp.bitfinex_position_id
    : null;
  const readMs = isoMs(stamp.position_id_read_at);
  return {
    bitfinex_position_id: stamp.status === 'STAMPED' ? pid : null,
    position_side: stamp.status === 'STAMPED' ? stamp.position_side : null,
    read_at_ts: readMs != null ? sec(readMs) : null,
    status: String(stamp.status),
    source: String(stamp.read_source ?? 'POST_STOP_POSITIONS_READBACK'),
  };
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
  // The envelope's platform_received_at is overwritten by later lifecycle
  // events (e.g. the EXIT an hour later), which produced a +3600 s
  // intent->receive lag. The ENTRY receive is the earliest receive time.
  const websiteReceivedMs = earliestReceiveMs(
    isoMs(ctx.platform_received_at),
    ...input.events.map((e) => e.platformReceivedAt?.getTime() ?? null),
  );
  const out: LiveExecutionReport[] = [];
  const events = [...input.events].sort((a, b) => a.createdAt.getTime() - b.createdAt.getTime());
  for (const p of input.participants) {
    const mine = events.filter((e) => e.participantId === p.id);
    // The pre-submit durable fence (ENTRY_SUBMISSION_UNKNOWN, phase
    // SUBMITTING) is written before EVERY Bitfinex write. It is only an error
    // when the submission never resolved (no ORDER_PLACED / FILLED / EXPIRED).
    // LIVE-FIX-0741: a fill healed via reconcile (STOP_LOSS_ARMED / EXIT) also
    // resolves it, and a fence younger than the grace window is still
    // in-flight (reporting it as ERROR was permanent: Fly dedups report_id).
    const entryResolved = mine.some((e) => [
      'ORDER_PLACED', 'FILLED', 'EXPIRED', 'STOP_LOSS_ARMED', 'EXIT',
    ].includes(String(e.eventType)));
    // One exchange fill can be recorded by two paths (CANCEL_RACE_FILL and
    // EXCHANGE_FILL_RECONCILE). Report each exchange fill once.
    const seenFills = new Set<string>();
    // Additive: the Bitfinex POSITION_ID read back after the stop (never
    // inferred). #849's _report_pid_side reads position.bitfinex_position_id
    // (+ position_side). Null-pid stamps carry their status for audit.
    const positionBlock = liveCopyPositionBlock(latestBitfinexPositionIdStamp(mine));
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
              // LIVE-FIX-1510: DISLOC entries may be IOC / MARKET (true IOC, never resting).
              type: typeof pl.order_type === 'string' ? pl.order_type : 'LIMIT',
              tif: typeof pl.time_in_force === 'string' ? pl.time_in_force : 'GTC',
              entry_order_mode: typeof pl.entry_order_mode === 'string' ? pl.entry_order_mode : 'LIMIT',
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
          const fillKeys = fillIdentities(pl);
          if (fillKeys.some((k) => seenFills.has(k))) break;
          fillKeys.forEach((k) => seenFills.add(k));
          const fillMs = isoMs(pl.exchange_fill_last_at) ?? isoMs(pl.fill_detected_at) ?? e.createdAt.getTime();
          tl.fill_at_ts = tl.fill_at_ts ?? sec(fillMs);
          const stopAck = isoMs(pl.stop_exchange_ack_at);
          if (stopAck != null) tl.stop_placed_at_ts = tl.stop_placed_at_ts ?? sec(stopAck);
          // LIVE-FIX-1494: entry ACK -> fill detected -> claim -> stop submit marks.
          for (const [k, f] of [['stop_entry_ack_at_ts', 'entry_exchange_ack_at'], ['stop_fill_detected_at_ts', 'fill_detected_at'],
            ['stop_claimed_at_ts', 'stop_claimed_at'], ['stop_submit_started_at_ts', 'stop_submit_started_at'],
            // LIVE-FIX-1499
            ['stop_claim_started_at_ts', 'stop_claim_started_at'], ['stop_phase_submitting_at_ts', 'stop_phase_submitting_at'],
            ['stop_protected_at_ts', 'stop_protected_at'], ['fill_enriched_at_ts', 'fill_enriched_at']] as const) {
            const v = isoMs(pl[f]);
            if (v != null) (tl as Record<string, number | null>)[k] = (tl as Record<string, number | null>)[k] ?? sec(v);
          }
          push(e, 'ORDER_FILLED', {
            // LIVE-FIX-1499: observability only (which detector fired, probe ladder, entry marks).
            entry_obs: {
              fill_detect_source: pl.fill_detect_source ?? pl.fill_detection_context ?? null,
              fill_probe_index: pl.fill_probe_index ?? null,
              fill_probe_marks: Array.isArray(pl.fill_probe_marks) ? pl.fill_probe_marks.slice(0, 10) : [],
              take_submit_fill_status: pl.take_submit_fill_status ?? null,
              marks: pl.entry_marks && typeof pl.entry_marks === 'object' ? pl.entry_marks : null,
              fast_path: pl.entry_fast_path ?? null,
            },
            fill: { price: num(pl.fill_price), qty: num(pl.qty), source: pl.fill_price_source ?? pl.fill_source ?? null },
            ...(positionBlock ? { position: positionBlock } : {}),
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
        case 'EXIT': {
          // LIVE-FIX-1491: exit-side hops for the per-trade timeline route.
          const t = (k: string) => { const ms = isoMs(pl[k]); return ms != null ? sec(ms) : null; };
          tl.exit_preflight_at_ts = t('close_preflight_started_at');
          tl.exit_sent_at_ts = t('close_submit_started_at');
          tl.exit_ack_at_ts = t('close_exchange_ack_at');
          tl.exit_confirmed_at_ts = t('close_confirmed_at');
          tl.exit_recorded_at_ts = sec(e.createdAt.getTime());
          push(e, 'POSITION_CLOSED', {
            // exit_exchange_fill_price is the authenticated Bitfinex close
            // fill; exit_price can be the quote side (11 Oct: 83084 ask vs
            // 83071 real fill), so it is only a fallback.
            close: {
              price: num(pl.exit_exchange_fill_price) ?? num(pl.exit_price) ?? num(pl.fill_price),
              reason: pl.reason ?? pl.exit_reason ?? null,
              paper_exit_price: num(pl.showcase_exit_price),
              exchange_order_id: pl.close_exchange_order_id != null ? String(pl.close_exchange_order_id) : null,
            },
            ...(positionBlock ? { position: positionBlock } : {}),
          });
          break;
        }
        case 'EXPIRED':
          push(e, 'ORDER_CANCELLED', { error: { code: 'ENTRY_EXPIRED', message: String(pl.reason ?? '').slice(0, 200) } });
          break;
        case 'ENTRY_SUBMISSION_UNKNOWN':
          if (entryResolved) break;
          if ((input.nowMs ?? Date.now()) - e.createdAt.getTime() < ENTRY_SUBMISSION_UNKNOWN_GRACE_MS) break;
          push(e, 'ERROR', {
            error: { code: e.eventType, message: String(pl.reason ?? pl.error ?? 'entry submission never resolved').slice(0, 200) },
          });
          break;
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

export const EXECUTOR_DECISION_REPORT_TYPES: Record<string, string> = {
  RECEIVED: 'INTENT_RECEIVED',
  SKIPPED: 'INTENT_SKIPPED',
  SUBMITTED: 'INTENT_SUBMITTED',
};

/**
 * Pure: participant-less EXECUTOR_DECISION events -> one report each
 * (INTENT_RECEIVED / INTENT_SKIPPED{reason} / INTENT_SUBMITTED). These exist
 * even when no participant row was ever created, so Fly sees why an intent
 * did not become an order (11 Oct: 16 intents, 0 orders, no reason visible).
 */
export function buildExecutorDecisionReports(input: {
  cycle: ReporterCycle;
  correlationId: string;
  lane: string;
  events: ReporterEvent[];
  nowMs: number;
}): LiveExecutionReport[] {
  const out: LiveExecutionReport[] = [];
  for (const e of input.events) {
    if (e.eventType !== 'EXECUTOR_DECISION') continue;
    const pl = rec(e.payload);
    const type = EXECUTOR_DECISION_REPORT_TYPES[String(pl.decision ?? '')];
    if (!type) continue;
    out.push({
      schema: LIVE_EXECUTION_REPORT_SCHEMA,
      report_id: `rw-${e.id}-${type}`,
      type,
      correlation_id: input.correlationId,
      trade_id: input.cycle.tradeId,
      cycle_id: input.cycle.id,
      lane: input.lane,
      account: typeof pl.account === 'string' ? pl.account : 'acct-unknown',
      event_at_ts: sec(e.createdAt.getTime()),
      sent_at_ts: input.nowMs / 1000,
      timeline: {},
      decision: {
        decision: String(pl.decision),
        reason: typeof pl.reason === 'string' ? pl.reason.slice(0, 200) : null,
        decided_at_ts: sec(isoMs(pl.decided_at) ?? e.createdAt.getTime()),
      },
    });
  }
  return out;
}

/** Exchange identities of one FILLED record: each exchange fill id, else the entry order. */
export function fillIdentities(pl: Record<string, unknown>): string[] {
  const out: string[] = [];
  const ids = pl.exchange_fill_ids;
  if (Array.isArray(ids)) for (const id of ids) if (id != null && String(id).trim()) out.push(`fid:${String(id).trim()}`);
  const single = pl.exchange_fill_id ?? pl.bitfinex_trade_id ?? pl.fill_id;
  if (single != null && String(single).trim()) out.push(`fid:${String(single).trim()}`);
  if (out.length) return out;
  const oid = pl.bitfinexOrderId ?? pl.bitfinex_order_id ?? pl.clientOrderId;
  return oid == null ? [] : [`oid:${String(oid)}`];
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
    if (Object.values(EXECUTOR_DECISION_REPORT_TYPES).includes(r.type)) continue;
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

/** LIVE-FIX-1502: one row per reporter run (HM 23:42 observability; no behaviour change). */
export interface ReporterRunRecord {
  process: 'executor' | 'website';
  trigger: 'kick' | 'interval' | 'rerun';
  started_at_ms: number;
  collect_ms: number | null;
  duration_ms: number;
  collected: number;
  unacked: number;
  attempted: number;
  skipped_backoff: number;
  sent: number;
  failed: number;
  /** ms from report event_at_ts to Fly ack, per sent report */
  delays_ms: number[];
  max_delay_ms: number | null;
  error: string | null;
}

export const REPORTER_RUNS_MAX = 200;

export function reporterRunLine(r: ReporterRunRecord): string {
  return `[LIVE-COPY-REPORTER-RUN] ${JSON.stringify(r)}`;
}

@Injectable()
export class LiveCopyFlyReporterService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(LiveCopyFlyReporterService.name);
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  readonly acked = new Map<string, number>();
  private readonly lastAttempt = new Map<string, number>();
  readonly stats = { lastRunAt: 0, sent: 0, failed: 0, lastError: null as string | null, pending: 0, consecutiveFailures: 0, lastOkAt: 0, lastFailAt: 0 };
  readonly capability = { lastSentAt: 0, lastOkAt: 0, lastError: null as string | null };
  private lastCapabilityAttempt = 0;
  readonly runs: ReporterRunRecord[] = [];
  private nextTrigger: ReporterRunRecord['trigger'] = 'interval';

  constructor(
    private readonly prisma: PrismaService,
    private readonly config: ConfigService,
  ) {}

  onModuleInit(): void {
    if (this.config.get<string>('LIVE_COPY_REPORTER_ENABLED') === 'false') return;
    if (process.env.NODE_ENV === 'test') return;
    setLiveCopyReportKickListener(() => void this.tick('kick'));
    // The real-money executor worker reports only on kicks (its own events);
    // the website process keeps the 5 s sweep. Fly dedups by report_id.
    if (process.env.RELAY_EXECUTOR_WORKER === 'true') return;
    this.timer = setInterval(() => void this.tick('interval'), 5_000);
    this.timer.unref?.();
  }

  onModuleDestroy(): void {
    setLiveCopyReportKickListener(null);
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
    // LIVE-FIX-1450: batch the per-cycle reads. The old loop did 3 sequential
    // DB round trips per approved cycle (up to 300 cycles), so a fresh fill
    // reached Fly 9-13 s late. Now: 3 queries total, newest cycles first.
    const approved = cycles
      .map((c) => ({ c, verdict: approvalPermitsRelayCopy(readEnvelopeFlyApproval(c.intentEnvelope), c.tradeId, secret) }))
      .filter((x): x is { c: typeof cycles[number]; verdict: Extract<typeof x.verdict, { ok: true }> } => x.verdict.ok);
    if (!approved.length) return reports;
    const cycleIds = approved.map((x) => x.c.id);
    const [allParticipants, allEvents] = await Promise.all([
      this.prisma.signalCycleParticipant.findMany({
        where: { cycleId: { in: cycleIds } },
        select: { id: true, userId: true, stopLossConfirmedAt: true, cycleId: true },
      }),
      this.prisma.signalCycleEvent.findMany({
        where: { cycleId: { in: cycleIds } },
        select: { id: true, participantId: true, eventType: true, payload: true, createdAt: true, platformReceivedAt: true, cycleId: true },
        orderBy: { createdAt: 'asc' },
      }),
    ]);
    const pairs = [...new Set(approved.flatMap((x) => allParticipants.filter((p) => p.cycleId === x.c.id).map((p) => `${x.c.agentId}|${p.userId}`)))];
    const instances = pairs.length
      ? await this.prisma.tradingAgentInstance.findMany({
          where: { OR: pairs.map((k) => ({ agentId: k.split('|')[0], userId: k.split('|')[1] })) },
          select: { id: true, userId: true, agentId: true },
        })
      : [];
    const eventsBy = new Map<string, typeof allEvents>();
    for (const e of allEvents) {
      const list = eventsBy.get(e.cycleId) ?? [];
      if (list.length < 500) list.push(e);
      eventsBy.set(e.cycleId, list);
    }
    for (const { c, verdict } of approved) {
      const events = eventsBy.get(c.id) ?? [];
      const participants = allParticipants.filter((p) => p.cycleId === c.id);
      reports.push(...buildExecutorDecisionReports({
        cycle: c,
        correlationId: String(verdict.approval.correlation_id),
        lane: String(verdict.approval.research_lane),
        events,
        nowMs,
      }));
      if (!participants.length) continue;
      const byUser = new Map(instances.filter((i) => i.agentId === c.agentId).map((i) => [i.userId, liveCopyAccountLabel(i.id)]));
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

  private rerun = false;

  async tick(trigger: ReporterRunRecord['trigger'] = 'interval'): Promise<void> {
    // LIVE-FIX-1054: a kick that lands mid-run is not dropped; one trailing
    // run follows so a fill/stop written during a sweep is reported at once.
    if (this.running) { this.rerun = true; return; }
    this.running = true;
    const run: ReporterRunRecord = {
      process: process.env.RELAY_EXECUTOR_WORKER === 'true' ? 'executor' : 'website',
      trigger, started_at_ms: Date.now(), collect_ms: null, duration_ms: 0, collected: 0, unacked: 0,
      attempted: 0, skipped_backoff: 0, sent: 0, failed: 0, delays_ms: [], max_delay_ms: null, error: null,
    };
    try {
      const secret = this.config.get<string>('SHOWCASE_WEBHOOK_SECRET');
      if (!String(secret ?? '').trim()) return;
      const nowMs = Date.now();
      void this.sendCapability(secret as string, nowMs).catch(() => false); // LIVE-FIX-1450: never delay fill reports
      const collected = await this.collect(nowMs);
      run.collect_ms = Date.now() - nowMs;
      run.collected = collected.length;
      const reports = collected.filter((r) => !this.acked.has(r.report_id));
      run.unacked = reports.length;
      this.stats.pending = reports.length;
      for (const r of reports) {
        const last = this.lastAttempt.get(r.report_id) ?? 0;
        if (nowMs - last < 15_000) { run.skipped_backoff += 1; continue; }
        this.lastAttempt.set(r.report_id, nowMs);
        run.attempted += 1;
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
            run.sent += 1;
            const evAt = Number((r as { event_at_ts?: unknown }).event_at_ts);
            if (Number.isFinite(evAt) && evAt > 0) run.delays_ms.push(Math.round(Date.now() - evAt * 1000));
            this.stats.sent += 1;
            this.stats.consecutiveFailures = 0;
            this.stats.lastOkAt = Date.now();
          } else {
            run.failed += 1;
            this.stats.failed += 1;
            this.stats.consecutiveFailures += 1;
            this.stats.lastFailAt = Date.now();
            this.stats.lastError = `HTTP_${res.status}`;
          }
        } catch (err) {
          run.failed += 1;
          this.stats.failed += 1;
          this.stats.consecutiveFailures += 1;
          this.stats.lastFailAt = Date.now();
          this.stats.lastError = err instanceof Error ? err.name : 'FETCH_FAILED';
        }
      }
      if (this.acked.size > 20_000) this.acked.clear();
    } catch (err) {
      this.stats.lastError = err instanceof Error ? err.message.slice(0, 120) : 'TICK_FAILED';
      this.logger.warn(`[LIVE-COPY-REPORTER] ${this.stats.lastError}`);
      run.error = this.stats.lastError;
    } finally {
      this.stats.lastRunAt = Date.now();
      run.duration_ms = this.stats.lastRunAt - run.started_at_ms;
      run.max_delay_ms = run.delays_ms.length ? Math.max(...run.delays_ms) : null;
      if (run.attempted > 0 && !run.error && run.failed === 0 && run.sent === 0) run.error = 'NO_SIGNATURE';
      this.runs.push(run);
      if (this.runs.length > REPORTER_RUNS_MAX) this.runs.splice(0, this.runs.length - REPORTER_RUNS_MAX);
      // Every run, including idle ones, so restarts and gaps are visible in Railway logs.
      this.logger.log(reporterRunLine(run));
      this.running = false;
      if (this.rerun) {
        this.rerun = false;
        setImmediate(() => void this.tick('rerun'));
      }
    }
  }
}

/**
 * LIVE-FIX-1246: report-back failures must not hide behind GREEN. Any failure
 * in the last 5 min or an unacked backlog => AMBER; >=10 consecutive failures
 * or nothing acked for 2 min while reports are pending => RED.
 */
export function reporterVerdict(
  stats: { consecutiveFailures: number; lastFailAt: number; lastOkAt: number; pending: number },
  nowMs: number,
): 'GREEN' | 'AMBER' | 'RED' {
  const stuck = stats.pending > 0 && nowMs - (stats.lastOkAt || 0) > 120_000 && stats.lastFailAt > stats.lastOkAt;
  if (stats.consecutiveFailures >= 10 || stuck) return 'RED';
  if (stats.consecutiveFailures > 0 || (stats.lastFailAt && nowMs - stats.lastFailAt < 300_000)) return 'AMBER';
  return 'GREEN';
}

export function worstVerdict(a: string, b: string): string {
  const rank: Record<string, number> = { GREEN: 0, AMBER: 1, RED: 2 };
  return (rank[b] ?? 0) > (rank[a] ?? 0) ? b : a;
}
