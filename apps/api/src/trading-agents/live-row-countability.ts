/**
 * HM B4: website/relay live rows carry `non_countable` for forced closes.
 *
 * A force-flattened or maintenance close is not a strategy outcome. The bot already excludes these
 * from paper stats (services/btc-conservative-agent/paper_pnl_canon.py FORCED_EXIT_REASONS); live
 * rows rendered by the API must carry the same flag so no live/paper stat counts them.
 */

/** Mirrors paper_pnl_canon.FORCED_EXIT_REASONS (spec asserts this is a superset) plus relay-only forced closes. */
export const LIVE_FORCED_EXIT_REASONS: ReadonlySet<string> = new Set([
  'ADMIN_MANUAL_CLOSE',
  'ADMIN_FORCE_FLAT',
  'CIRCUIT_BREAKER_ADMIN_MANUAL',
  'DEPLOY_FORCE_FLAT',
  // Relay-side: the user stopped the relay and it flattened the copy.
  'USER_RELAY_STOP',
]);

/**
 * #852 item 4: the guardian's own non-countable close markers (live_tile_gate.FORCE_FLAT_CLOSE_MARKERS, matched as
 * substrings exactly like force_flat_non_countable). Spec asserts this is a superset of the Python tuple.
 */
export const GUARDIAN_FORCE_FLAT_CLOSE_MARKERS: readonly string[] = [
  'AUTHENTICATED_EMERGENCY_CLOSE',
  'FORCE_FLAT',
  'GUARDIAN_FORCE_FLAT',
  'STOP_FAILURE_FLATTEN',
  'LIQ_CHECK_FLATTEN',
  'LIQ_CLAMP_FLATTEN',
];

/** live_guardian.FILL_TO_STOP_MAX_SEC (HM G6) = live_tile_gate.STOP_RESIDENT_MAX_AGE_S (E6): spec pins both. */
export const LIVE_FILL_TO_STOP_MAX_S = 5;

/** Any reason naming a force-flat or a maintenance close is forced (e.g. ADMIN_PAUSE:DEPLOY_MAINTENANCE). */
const FORCED_PATTERN = /FORCE_FLAT|MAINTENANCE/;

const DISPLAY_PREFIXES = ['SOURCE_CONFIRMED_', 'LATE_ENTRY_BETTER_ONLY_CONTINUATION: ', 'LATE_ENTRY_BETTER_ONLY_CONTINUATION'];

export type LiveRowCountability = { nonCountable: boolean; nonCountableReason: string | null };

/** Strip display prefixes the mapper adds, so `SOURCE_CONFIRMED_DEPLOY_FORCE_FLAT` classifies as forced. */
function canonicalReason(raw: string | null | undefined): string {
  let s = String(raw ?? '').trim().toUpperCase();
  for (const p of DISPLAY_PREFIXES) {
    if (s.startsWith(p)) s = s.slice(p.length).replace(/^:\s*/, '');
  }
  return s;
}

/** First forced reason among the candidates (row exit reason, showcase exit reason, terminal reason). */
export function liveRowCountability(...reasons: Array<string | null | undefined>): LiveRowCountability {
  for (const r of reasons) {
    const s = canonicalReason(r);
    if (!s) continue;
    if (LIVE_FORCED_EXIT_REASONS.has(s) || FORCED_PATTERN.test(s) || GUARDIAN_FORCE_FLAT_CLOSE_MARKERS.some((m) => s.includes(m))) {
      return { nonCountable: true, nonCountableReason: `FORCED_CLOSE:${s}` };
    }
  }
  return { nonCountable: false, nonCountableReason: null };
}

/**
 * #852 item 4: a live row also needs the guardian's stop evidence to count (same rule as live_guardian.evaluate 0b/1):
 * an exchange-authenticated resident reduce-only stop (STOP_LOSS_ARMED, the relay's STOP_CONFIRMED report to Fly)
 * confirmed within LIVE_FILL_TO_STOP_MAX_S of the fill, and no STOP_FAILED (stop_loss_placed === false at fill).
 * Missing, late, failed or unverifiable stop evidence => non-countable (fail closed). Continuous residency while
 * holding (<= 5 s evidence) is enforced live by the guardian, whose flatten then closes the row with a
 * GUARDIAN_FORCE_FLAT_CLOSE_MARKERS reason (non-countable above).
 */
export type LiveRowStopEvidence = { fillAtMs: number | null; stopConfirmedAtMs: number | null; stopFailed: boolean };

const NC = (reason: string): LiveRowCountability => ({ nonCountable: true, nonCountableReason: `NO_STOP_EVIDENCE:${reason}` });
const COUNTABLE: LiveRowCountability = { nonCountable: false, nonCountableReason: null };

export function liveRowStopCountability(ev: LiveRowStopEvidence | null | undefined): LiveRowCountability {
  if (!ev) return NC('UNLINKED');
  if (ev.stopFailed) return NC('STOP_FAILED');
  if (ev.fillAtMs == null || !Number.isFinite(ev.fillAtMs)) return NC('FILL_TIME_UNKNOWN');
  if (ev.stopConfirmedAtMs == null || !Number.isFinite(ev.stopConfirmedAtMs)) return NC('STOP_NOT_CONFIRMED');
  if (ev.stopConfirmedAtMs - ev.fillAtMs > LIVE_FILL_TO_STOP_MAX_S * 1000) return NC('FILL_TO_STOP_GT_5S');
  return COUNTABLE;
}

type EvidenceEvent = { eventType: string; payload: unknown; createdAt: Date };

function payloadMs(payload: unknown, key: string): number | null {
  const raw = payload && typeof payload === 'object' ? (payload as Record<string, unknown>)[key] : undefined;
  const ms = typeof raw === 'number' ? raw : typeof raw === 'string' ? Date.parse(raw) : Number.NaN;
  return Number.isFinite(ms) ? ms : null;
}

/** Same sources as live-copy-fly-reporter (fill_at_ts / stop_confirmed_at_ts / STOP_FAILED) so Fly and the website agree. */
export function liveRowStopEvidenceFromEvents(events: readonly EvidenceEvent[]): LiveRowStopEvidence {
  const filled = events.find((e) => e.eventType === 'FILLED');
  const armed = events.find((e) => e.eventType === 'STOP_LOSS_ARMED');
  const fillAtMs = filled
    ? payloadMs(filled.payload, 'exchange_fill_last_at') ?? payloadMs(filled.payload, 'fill_detected_at') ?? filled.createdAt.getTime()
    : null;
  const stopFailed = events.some((e) => e.eventType === 'FILLED' && e.payload && typeof e.payload === 'object'
    && (e.payload as Record<string, unknown>).stop_loss_placed === false);
  return { fillAtMs, stopConfirmedAtMs: armed ? armed.createdAt.getTime() : null, stopFailed };
}

/**
 * HM R4 B1 + RK 08:03 (R4 one helper, R17): THE exit-price provenance of a closed live row, shared by the Fly reporter
 * (D3 loss caps on Fly read close.price/close.price_source) and the countability rule below. Only a REAL Bitfinex fill
 * counts: `exchange_fill` = the authenticated close-order trades (resolveNormalCloseExchangeEvidence:
 * exit_exchange_fill_price) and `stop_fill` = the resident stop's own order trades at the exact lot qty
 * (pnl_source bitfinex_order_trades + EXCHANGE_STOP + exchange_exit_fill_ids). Everything else (ticker/mark, entry-price
 * fallback, failed or budget-refused fill read, partial, non-USD fee => no evidence) has price null: D3 UNREADABLE
 * (AMBER entry block on Fly) and the row non-countable. Never a $0 loss.
 */
export type LiveExitPriceSource = 'exchange_fill' | 'stop_fill' | 'mark' | 'entry_fallback' | 'none';
export const LIVE_REAL_EXIT_FILL_SOURCES: ReadonlySet<LiveExitPriceSource> = new Set(['exchange_fill', 'stop_fill']);
export type LiveExitFillEvidence = {
  price: number | null;
  source: LiveExitPriceSource;
  reportedPrice: number | null;
  /** nit 6: set by liveExitFillEvidenceFromEvents (the real entry fill); absent = not checked */
  entry?: LiveEntryFillEvidence;
};

const finitePos = (v: unknown): number | null => {
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : Number.NaN;
  return Number.isFinite(n) && n > 0 ? n : null;
};

export function liveExitFillEvidence(exitPayload: unknown, entryFillPrice?: unknown): LiveExitFillEvidence {
  const pl = exitPayload && typeof exitPayload === 'object' ? (exitPayload as Record<string, unknown>) : {};
  const reported = finitePos(pl.exit_price);
  const fill = finitePos(pl.exit_exchange_fill_price);
  if (fill != null) return { price: fill, source: 'exchange_fill', reportedPrice: reported ?? fill };
  const stopIds = Array.isArray(pl.exchange_exit_fill_ids) ? pl.exchange_exit_fill_ids : [];
  const stopQtyOk = stopIds.length > 0 && finitePos(pl.exchange_exit_filled_qty) != null
    && String(pl.pnl_source ?? '') === 'bitfinex_order_trades';
  if (reported != null && stopQtyOk && (String(pl.exit_reason ?? '').toUpperCase() === 'EXCHANGE_STOP'
      // HM R5 C1 / RK 09:10: the shared order-fill helper (liveOrderExitFill) stamps the resident stop's own trades
      || (pl.exit_fill_status === EXIT_FILL_REAL && pl.exit_fill_kind === 'stop'))) {
    return { price: reported, source: 'stop_fill', reportedPrice: reported };
  }
  if (reported == null) {
    // RK 09:10: an unreadable exit is stored with exit_price null; keep the source it was stamped with (audit only)
    const stamped = String(pl.exit_price_source ?? '');
    const source: LiveExitPriceSource = stamped === 'mark' || stamped === 'entry_fallback' ? stamped : 'none';
    return { price: null, source, reportedPrice: null };
  }
  const entry = finitePos(entryFillPrice ?? pl.entry_exchange_fill_price ?? pl.fill_price);
  return { price: null, source: entry != null && Math.abs(entry - reported) < 1e-9 ? 'entry_fallback' : 'mark', reportedPrice: reported };
}

/**
 * HM R5 nit 6: the ENTRY price of a live row is real only when the FILLED event carries the entry order's own Bitfinex
 * trades (fill_price_source 'exchange_trades'). A limit / stop-reference / position-delta / merged-base fallback is never
 * used by D3 or countability (price null => ENTRY_FILL_UNREADABLE, non-countable; Fly D3 treats it as unreadable).
 */
export type LiveEntryFillEvidence = { price: number | null; source: string };
export function liveEntryFillEvidence(filledPayload: unknown): LiveEntryFillEvidence {
  const pl = filledPayload && typeof filledPayload === 'object' ? (filledPayload as Record<string, unknown>) : {};
  const source = String(pl.fill_price_source ?? pl.fill_source ?? 'none') || 'none';
  const price = finitePos(pl.fill_price);
  return { price: source === 'exchange_trades' ? price : null, source };
}

/** EXIT plus its later clearing re-stamp (RK 09:10 (3)): the latest of the two is THE exit record of a participant. */
export const EXIT_FILL_RECONCILED_EVENT = 'EXIT_FILL_RECONCILED';
export function latestLiveExitEvent<T extends { eventType: string }>(events: readonly T[]): T | undefined {
  return [...events].reverse().find((e) => e.eventType === 'EXIT' || e.eventType === EXIT_FILL_RECONCILED_EVENT);
}

/** The latest EXIT (or its EXIT_FILL_RECONCILED re-stamp) of a participant, with the real FILLED entry (nit 6). */
export function liveExitFillEvidenceFromEvents(events: readonly { eventType: string; payload: unknown }[]): LiveExitFillEvidence {
  const exit = latestLiveExitEvent(events);
  const filled = [...events].reverse().find((e) => e.eventType === 'FILLED');
  const entry = liveEntryFillEvidence(filled?.payload);
  const fp = filled && filled.payload && typeof filled.payload === 'object' ? (filled.payload as Record<string, unknown>) : {};
  return { ...liveExitFillEvidence(exit?.payload, fp.exchange_fill_price ?? fp.fill_price), entry };
}

/** R17: a closed row whose exit price is not a real Bitfinex fill is non-countable; nit 6: nor one without a real entry fill. */
export function liveRowExitFillCountability(ev: LiveExitFillEvidence | null | undefined): LiveRowCountability {
  if (!(ev && LIVE_REAL_EXIT_FILL_SOURCES.has(ev.source) && ev.price != null)) {
    return { nonCountable: true, nonCountableReason: `EXIT_FILL_UNREADABLE:${ev?.source ?? 'none'}` };
  }
  if (ev.entry && ev.entry.price == null) return { nonCountable: true, nonCountableReason: `ENTRY_FILL_UNREADABLE:${ev.entry.source}` };
  return COUNTABLE;
}

/**
 * HM R5 C1 + RK 09:10 (binding): THE one exit-fill helper of every live exit path. Reads are the exit order(s)' OWN
 * Bitfinex trades (resolveExchangeTradesFillEvidence per order id, under the shared read budget), for the EXACT lot qty:
 *   - one order whose trades equal the qty (sats) => that order's VWAP;
 *   - else every readable order together equal to the qty (partial / multiple fills) => the combined VWAP;
 *   - else UNREADABLE: exit_price null, pnl null, exit_fill_status EXIT_FILL_UNREADABLE (+ the pending order ids and qty
 *     the clearing read retries). Never a mark, ticker or entry price, not even fail-safe.
 * P&L = (exit VWAP - REAL entry fill) x qty x side; no fee or funding term (fees are kept as audit fields only). Without a
 * real entry fill (nit 6) pnl is null.
 */
export const EXIT_FILL_REAL = 'REAL_FILL';
export const EXIT_FILL_UNREADABLE = 'EXIT_FILL_UNREADABLE';
export type LiveOrderFillRead = {
  price: number;
  qty: number;
  fillIds: Array<number | string>;
  fees?: unknown[];
  firstExecutedAtMs: number | null;
  lastExecutedAtMs: number | null;
};
export type LiveOrderExitFillInput = {
  kind: 'stop' | 'close';
  expectedQty: number | null | undefined;
  /** one entry per exit order id; read null = the read failed / was budget-refused / returned no trades */
  reads: Array<{ orderId: number; read: LiveOrderFillRead | null }>;
  direction?: 'LONG' | 'SHORT' | null;
  entryFillPrice?: number | null;
  leverage?: number | null;
};
export type LiveOrderExitFill = {
  ok: boolean;
  price: number | null;
  qty: number | null;
  firstMs: number | null;
  lastMs: number | null;
  pnlUsd: number | null;
  fields: Record<string, unknown>;
};
const SATS = 1e8;
const toSats = (btc: number) => Math.round(Math.abs(btc) * SATS);
const isoOrNull = (ms: number | null | undefined) => (ms == null || !Number.isFinite(ms) ? null : new Date(ms).toISOString());

export function liveOrderExitFill(i: LiveOrderExitFillInput): LiveOrderExitFill {
  const expected = i.expectedQty != null && Number.isFinite(i.expectedQty) ? toSats(i.expectedQty) : 0;
  const ids = [...new Set(i.reads.map((r) => Number(r.orderId)).filter((id) => Number.isFinite(id) && id > 0))];
  const readable = i.reads.filter((r): r is { orderId: number; read: LiveOrderFillRead } =>
    r.read != null && finitePos(r.read.price) != null && toSats(r.read.qty) > 0);
  const unreadable = (reason: string): LiveOrderExitFill => ({
    ok: false, price: null, qty: null, firstMs: null, lastMs: null, pnlUsd: null,
    fields: {
      exit_price: null,
      pnl_usd: null,
      pnl_margin_pct: null,
      gross_pnl_usd: null,
      exit_fill_status: EXIT_FILL_UNREADABLE,
      exit_fill_kind: i.kind,
      exit_fill_pending_order_ids: ids,
      exit_fill_expected_qty: i.expectedQty ?? null,
      exit_fill_unreadable_reason: reason,
      exit_fill_direction: i.direction ?? null,
      ...(i.kind === 'close' ? { exit_exchange_fill_price: null } : {}),
    },
  });
  if (ids.length === 0) return unreadable('NO_ORDER_ID');
  if (!(expected > 0)) return unreadable('NO_EXPECTED_QTY');
  if (readable.length === 0) return unreadable('NO_FILL_READ');   // every read failed, was budget-refused or returned no trades
  let used = readable.filter((r) => toSats(r.read.qty) === expected).slice(0, 1);
  if (used.length === 0) {
    const total = readable.reduce((s, r) => s + toSats(r.read.qty), 0);
    if (total !== expected) {
      return unreadable(`QTY_MISMATCH:${(total / SATS).toFixed(8)}!=${(expected / SATS).toFixed(8)}`);
    }
    used = readable;
  }
  const qtySats = used.reduce((s, r) => s + toSats(r.read.qty), 0);
  const qty = qtySats / SATS;
  const vwap = used.reduce((s, r) => s + toSats(r.read.qty) * r.read.price, 0) / qtySats;
  const firsts = used.map((r) => r.read.firstExecutedAtMs).filter((v): v is number => v != null && Number.isFinite(v));
  const lasts = used.map((r) => r.read.lastExecutedAtMs).filter((v): v is number => v != null && Number.isFinite(v));
  const firstMs = firsts.length ? Math.min(...firsts) : null;
  const lastMs = lasts.length ? Math.max(...lasts) : null;
  const entry = finitePos(i.entryFillPrice);
  const sign = i.direction === 'LONG' ? 1 : i.direction === 'SHORT' ? -1 : 0;
  const gross = entry != null && sign !== 0 ? (vwap - entry) * qty * sign : null;
  const lev = finitePos(i.leverage);
  const pnlUsd = gross == null ? null : Math.round(gross * 100) / 100;
  const pnlMarginPct = gross == null || entry == null || lev == null
    ? null : Math.round((gross / (entry * qty)) * 100 * lev * 100) / 100;
  const usedIds = used.map((r) => Number(r.orderId));
  return {
    ok: true, price: vwap, qty, firstMs, lastMs, pnlUsd,
    fields: {
      exit_price: vwap,
      exit_fill_vwap: vwap,
      exit_fill_status: EXIT_FILL_REAL,
      exit_fill_kind: i.kind,
      exit_fill_order_ids: usedIds,
      exit_order_id: usedIds[0],
      exit_fill_at: isoOrNull(lastMs),
      exchange_exit_fill_first_at: isoOrNull(firstMs),
      exchange_exit_fill_last_at: isoOrNull(lastMs),
      exchange_exit_fill_ids: [...new Set(used.flatMap((r) => r.read.fillIds.map(String)))],
      exchange_exit_fill_count: used.reduce((s, r) => s + r.read.fillIds.length, 0),
      exchange_exit_filled_qty: qty,
      exchange_exit_costs: used.flatMap((r) => r.read.fees ?? []),   // audit only: never in P&L or gates
      ...(i.kind === 'close' ? { exit_exchange_fill_price: vwap } : {}),
      pnl_source: 'bitfinex_order_trades',
      pnl_usd: pnlUsd,
      pnl_margin_pct: pnlMarginPct,
      gross_pnl_usd: pnlUsd,
      entry_fill_price_used: entry,
    },
  };
}

/**
 * RK 09:10 (1): the last line before ANY live EXIT payload is persisted. A payload whose exit price is not a real
 * Bitfinex fill (shared liveExitFillEvidence) is stored with exit_price null and every P&L field null,
 * exit_fill_status EXIT_FILL_UNREADABLE; a real one is stored at the fill (exit_fill_status REAL_FILL).
 */
export function liveExitPayloadRealFillOnly(body: Record<string, unknown>): Record<string, unknown> {
  const ev = liveExitFillEvidence(body);
  if (ev.price != null && LIVE_REAL_EXIT_FILL_SOURCES.has(ev.source)) {
    return {
      ...body,
      exit_price: ev.price,
      exit_price_source: ev.source,
      exit_fill_status: EXIT_FILL_REAL,
      exit_fill_at: body.exit_fill_at ?? body.exchange_exit_fill_last_at ?? body.exchange_close_fill_last_at ?? null,
    };
  }
  return {
    ...body,
    exit_price: null,
    exit_price_source: ev.source,
    exit_fill_status: EXIT_FILL_UNREADABLE,
    exit_fill_unreadable_reason: body.exit_fill_unreadable_reason ?? `NO_REAL_FILL:${ev.source}`,
    pnl_usd: null,
    pnl_margin_pct: null,
    gross_pnl_usd: null,
    net_pnl_usd: null,
    actual_bitfinex_realized_pnl_usd: null,
  };
}

/** HM 02:55 blocker 4 (R17): a live row with no leg stamps to check (e.g. a Bitfinex cash-ledger-only close) is
 * never countable: FORCED_CLOSE when forced, else LIVE_LEGS_UNCHECKED. */
export const LIVE_LEGS_UNCHECKED = 'LIVE_LEGS_UNCHECKED';
export function liveRowUncheckedCountability(...reasons: Array<string | null | undefined>): LiveRowCountability {
  const forced = liveRowCountability(...reasons);
  return forced.nonCountable ? forced : { nonCountable: true, nonCountableReason: LIVE_LEGS_UNCHECKED };
}

/**
 * HM 01:43 item 3 (R17): per-leg check of a LIVE row. Each side (entry; exit when closed by a Fly-sent order)
 * needs the four stamps signal -> Fly send -> relay (Railway) receive -> exchange ack/fill; each stamp ends one
 * leg and starts the next, so no interval is counted twice. A missing stamp or a leg that ends before it starts
 * (beyond the clock slack) makes the row non-countable. Mirrors services/btc-conservative-agent/live_ack_pool.py
 * LIVE_LEGS / live_legs (test_live_vs_paper.py asserts every name and key below appears here).
 */
export const LIVE_LEGS = {
  entry: [
    ['signal', ['fly_signal_at_ts']],
    ['fly_send', ['fly_intent_emitted_at_ts']],
    ['relay_receive', ['railway_received_at_ts']],
    ['ack_fill', ['exchange_ack_at_ts', 'fill_at_ts']],
  ],
  exit: [
    ['signal', ['exit_signal_at_ts']],
    ['fly_send', ['fly_send_started_at_ts']],
    ['relay_receive', ['railway_received_at_ts']],
    ['ack_fill', ['exit_ack_at_ts', 'exit_confirmed_at_ts']],
  ],
  // RK (1) 24 Oct: exchange-resident stop exit: no Fly send leg; trigger = the real trade print that crossed the
  // stop, fill = Bitfinex's own fill record. Both required (missing -> non-countable).
  exit_resident: [
    ['signal', ['exit_trigger_print_at_ts']],
    ['ack_fill', ['exit_fill_record_at_ts']],
  ],
} as const;

export type LiveExitSide = 'exit' | 'exit_resident';

export const LIVE_LEG_CLOCK_SLACK_S = 0.05;

/** Epoch-second stamps of one side, keyed like the live chain / Fly outbox record. */
export type LiveLegStamps = Record<string, number | null | undefined>;

export type LiveLegCheck = LiveRowCountability & { legReasons: string[] };

const posNum = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : null);

/** `exit` null = no exit legs required (row still open). `exitSide` picks the bot-sent or exchange-resident legs. */
export function liveRowLegCheck(
  entry: LiveLegStamps,
  exit: LiveLegStamps | null,
  exitSide: LiveExitSide = 'exit',
): LiveLegCheck {
  const reasons: string[] = [];
  const sides: Array<['entry' | LiveExitSide, LiveLegStamps]> = [['entry', entry ?? {}]];
  if (exit) sides.push([exitSide, exit]);
  for (const [side, src] of sides) {
    const spec: ReadonlyArray<readonly [string, ReadonlyArray<string>]> = LIVE_LEGS[side];
    const stamps = spec.map(([name, keys]) => {
      const v = keys.map((k) => posNum(src[k])).find((x) => x != null) ?? null;
      if (v == null) reasons.push(`LIVE_LEG_MISSING:${side}.${name}`);
      return [name, v] as const;
    });
    for (let i = 0; i + 1 < stamps.length; i++) {
      const [n0, v0] = stamps[i];
      const [n1, v1] = stamps[i + 1];
      if (v0 != null && v1 != null && v1 - v0 < -LIVE_LEG_CLOCK_SLACK_S) reasons.push(`LIVE_LEG_ORDER:${side}.${n0}->${n1}`);
    }
  }
  return { nonCountable: reasons.length > 0, nonCountableReason: reasons[0] ?? null, legReasons: reasons };
}

/**
 * THE live-row countability rule (one rule for every live row: #842 HM B4 + HM 01:43 item 3 + #852 item 4, merged in #852 B1):
 *   1. a forced close reason => FORCED_CLOSE (B4);
 *   2. the guardian's stop evidence for a FILLED row (#852 item 4: exchange-resident stop confirmed <= 5 s after the
 *      fill, no STOP_FAILED). `stopEvidence === undefined` means the row never filled (no stop to require); `null`
 *      means filled but unlinked => NO_STOP_EVIDENCE:UNLINKED (fail closed);
 *   3. the per-leg check (HM 01:43 item 3);
 *   4. a closed row's exit price must be a real Bitfinex fill (liveExitFillEvidence; HM R4 B1, RK 08:03, R17).
 * First failure wins; rows with no leg stamps at all use liveRowUncheckedCountability (always non-countable).
 */
export function liveRowCountabilityWithLegs(
  reasons: Array<string | null | undefined>,
  entry: LiveLegStamps,
  exit: LiveLegStamps | null,
  exitSide: LiveExitSide = 'exit',
  stopEvidence?: LiveRowStopEvidence | null,
  exitFill?: LiveExitFillEvidence | null,
): LiveRowCountability {
  const forced = liveRowCountability(...reasons);
  if (forced.nonCountable) return forced;
  if (stopEvidence !== undefined) {
    const stop = liveRowStopCountability(stopEvidence);
    if (stop.nonCountable) return stop;
  }
  const legs = liveRowLegCheck(entry, exit, exitSide);
  if (legs.nonCountable) return { nonCountable: true, nonCountableReason: legs.nonCountableReason };
  // HM R4 B1 / RK 08:03 (R17): a closed row counts only with a REAL Bitfinex exit fill (shared liveExitFillEvidence).
  if (exit && exitFill !== undefined) return liveRowExitFillCountability(exitFill);
  return COUNTABLE;
}
