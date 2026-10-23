import assert from 'node:assert/strict';
import { mock, test } from 'node:test';
import {
  BITFINEX_BTC_PERP_SYMBOL,
  BITFINEX_REDUCE_ONLY_FLAG,
  BitfinexMultiplePositionsError,
  parseBitfinexPositionCreatedAtMs,
  parseBitfinexPositionId,
  parseOpenPositionPayload,
} from '../exchanges/bitfinex-api.client';
import {
  BITFINEX_POSITION_ID_STAMP_EVENT,
  BITFINEX_POSITION_CREATE_TIME_TOLERANCE_MS,
  BITFINEX_POSITION_ID_STAMP_SCHEMA,
  latestBitfinexPositionIdStamp,
  resolveBitfinexPositionIdStamp,
  type BitfinexPositionIdStampPayload,
} from './bitfinex-position-id-stamp';
import { buildLiveCopyReports, liveCopyPositionBlock } from './live-copy-fly-reporter';
import { liveTradesToCsv, mapParticipantToExportRow } from './live-trade-export.mapper';
import { SignalSubscriberExecutionService } from './signal-subscriber-execution.service';

const PID = 160_123_456;
const READ_START = 1_760_000_003_000;
const READ_AT = 1_760_000_003_120;
// Bitfinex v2 auth/r/positions row: [SYMBOL, STATUS, AMOUNT, BASE_PRICE, FUNDING,
// FUNDING_TYPE, PL, PL_PERC, PRICE_LIQ, LEVERAGE, FLAG, POSITION_ID, MTS_CREATE, MTS_UPDATE, ...]
const FILL_OBS = READ_START - 3_000;
const CREATED = FILL_OBS - 200;
const row = (amount: number, pid: unknown = PID, symbol = BITFINEX_BTC_PERP_SYMBOL, created: unknown = CREATED) =>
  [symbol, 'ACTIVE', amount, 65_000, 0, 0, 0.1, 0.01, 50_000, 100, null, pid, created, CREATED + 50];
const resolve = (outcome: Parameters<typeof resolveBitfinexPositionIdStamp>[0]['outcome'], qty = 0.0004, direction: 'LONG' | 'SHORT' = 'LONG', fillObservedAtMs = FILL_OBS) =>
  resolveBitfinexPositionIdStamp({ outcome, expected: { direction, qty }, readStartedAtMs: READ_START, readAtMs: READ_AT, fillObservedAtMs });

// ---------- parser: POSITION_ID is row[11] exactly as returned ----------

test('positions parser exposes the real POSITION_ID (row[11]) and never invents one', () => {
  assert.equal(parseOpenPositionPayload([row(0.0004)])?.positionId, PID);
  assert.equal(parseOpenPositionPayload([row(0.0004, String(PID))])?.positionId, undefined);
  assert.equal(parseOpenPositionPayload([row(0.0004, null)])?.positionId, undefined);
  assert.equal(parseOpenPositionPayload([row(0.0004).slice(0, 8)])?.positionId, undefined);
  for (const bad of [0, -1, 1.5, Number.NaN, '123', null, undefined, 2 ** 60]) {
    assert.equal(parseBitfinexPositionId(bad), null, String(bad));
  }
  assert.equal(parseBitfinexPositionId(PID), PID);
});

test('positions parser exposes row[12] MTS_CREATE as createdAtMs and never invents one', () => {
  assert.equal(parseOpenPositionPayload([row(0.0004)])?.createdAtMs, CREATED);
  assert.equal(parseOpenPositionPayload([row(0.0004, PID, BITFINEX_BTC_PERP_SYMBOL, null)])?.createdAtMs, undefined);
  assert.equal(parseOpenPositionPayload([row(0.0004).slice(0, 12)])?.createdAtMs, undefined);
  for (const bad of [0, -1, 1.5, Number.NaN, String(CREATED), null, undefined, 2 ** 60]) {
    assert.equal(parseBitfinexPositionCreatedAtMs(bad), null, String(bad));
  }
});

test('positions parser throws a typed multi-position error instead of picking a row', () => {
  assert.throws(
    () => parseOpenPositionPayload([row(0.0004, 1), row(0.0002, 2)]),
    (err: unknown) => err instanceof BitfinexMultiplePositionsError && /duplicate/.test((err as Error).message),
  );
});

// ---------- resolver (pure guard) ----------

test('success: exact single matching position stamps the real pid with its read time', () => {
  const s = resolve({ ok: true, position: parseOpenPositionPayload([row(0.0004)]) });
  assert.equal(s.status, 'STAMPED');
  assert.equal(s.bitfinex_position_id, PID);
  assert.equal(s.position_id_read_at, new Date(READ_AT).toISOString());
  assert.equal(s.position_id_read_started_at, new Date(READ_START).toISOString());
  assert.equal(s.read_source, 'POST_STOP_POSITIONS_READBACK');
  assert.equal(s.position_side, 'LONG');
  assert.equal(s.schema, BITFINEX_POSITION_ID_STAMP_SCHEMA);
  assert.equal(s.position_created_at, new Date(CREATED).toISOString());
  assert.equal(s.fill_observed_at, new Date(FILL_OBS).toISOString());
});

test('HM N1 create-time tie: a position created after the observed fill (+1 s) is CREATE_TIME_MISMATCH', () => {
  const at = (created: unknown) => resolve({ ok: true, position: parseOpenPositionPayload([row(0.0004, PID, BITFINEX_BTC_PERP_SYMBOL, created)]) });
  assert.equal(BITFINEX_POSITION_CREATE_TIME_TOLERANCE_MS, 1_000);
  assert.equal(at(FILL_OBS + 1_000).status, 'STAMPED');                      // inclusive skew tolerance
  assert.equal(at(FILL_OBS - 86_400_000).status, 'STAMPED');                 // long-held position, same fill
  const sliced = parseOpenPositionPayload([row(0.0004).slice(0, 12)]);        // row without [12]
  assert.equal(resolve({ ok: true, position: sliced }).status, 'CREATE_TIME_ABSENT');
  for (const late of [FILL_OBS + 1_001, READ_START, READ_AT + 5_000]) {
    const s = at(late);
    assert.equal(s.status, 'CREATE_TIME_MISMATCH', String(late));
    assert.equal(s.bitfinex_position_id, null);
    assert.equal(s.position_created_at, new Date(late).toISOString());
  }
  for (const missing of [null, '1760000000000', 0, -5, 1.5]) {
    const s = at(missing);
    assert.equal(s.status, 'CREATE_TIME_ABSENT', String(missing));
    assert.equal(s.bitfinex_position_id, null);
    assert.equal(s.position_created_at, null);
  }
  // No fill time => no tie => no pid (fail closed).
  const noFill = resolve({ ok: true, position: parseOpenPositionPayload([row(0.0004)]) }, 0.0004, 'LONG', Number.NaN);
  assert.equal(noFill.status, 'CREATE_TIME_MISMATCH');
  assert.equal(noFill.bitfinex_position_id, null);
  // Earlier guards still win: a pid-less row stays POSITION_ID_ABSENT.
  assert.equal(resolve({ ok: true, position: parseOpenPositionPayload([row(0.0004, null, BITFINEX_BTC_PERP_SYMBOL, READ_AT)]) }).status, 'POSITION_ID_ABSENT');
});

test('read failure: pid null, status READ_FAILED, never throws', () => {
  const s = resolve({ ok: false, error: new Error('nonce lane timeout') });
  assert.equal(s.status, 'READ_FAILED');
  assert.equal(s.bitfinex_position_id, null);
  assert.match(String(s.error), /nonce lane timeout/);
  assert.doesNotThrow(() => resolve({ ok: false, error: { weird: true } }));
  assert.doesNotThrow(() => resolve({ ok: true, position: { amount: 'x' } as never }));
});

test('multi-position: more than one BTCF0:USTF0 row -> pid null, never picks one', () => {
  let error: unknown;
  try { parseOpenPositionPayload([row(0.0004, 1), row(0.0004, 2)]); } catch (e) { error = e; }
  const s = resolve({ ok: false, error });
  assert.equal(s.status, 'MULTI_POSITION');
  assert.equal(s.bitfinex_position_id, null);
});

test('mismatch: symbol, side or amount differs -> pid null', () => {
  const pos = parseOpenPositionPayload([row(0.0004)])!;
  const sym = resolve({ ok: true, position: { ...pos, symbol: 'tETHF0:USTF0' } });
  assert.equal(sym.status, 'SYMBOL_MISMATCH');
  assert.equal(sym.bitfinex_position_id, null);
  const side = resolve({ ok: true, position: pos }, 0.0004, 'SHORT');
  assert.equal(side.status, 'SIDE_MISMATCH');
  assert.equal(side.bitfinex_position_id, null);
  const amt = resolve({ ok: true, position: parseOpenPositionPayload([row(0.0008)]) });
  assert.equal(amt.status, 'AMOUNT_MISMATCH');
  assert.equal(amt.bitfinex_position_id, null);
  const none = resolve({ ok: true, position: null });
  assert.equal(none.status, 'NO_POSITION');
  assert.equal(none.bitfinex_position_id, null);
  const absent = resolve({ ok: true, position: parseOpenPositionPayload([row(0.0004, null)]) });
  assert.equal(absent.status, 'POSITION_ID_ABSENT');
  assert.equal(absent.bitfinex_position_id, null);
});

test('latest stamp wins and foreign payloads are ignored', () => {
  const a = resolve({ ok: false, error: new Error('x') });
  const b = resolve({ ok: true, position: parseOpenPositionPayload([row(0.0004)]) });
  const got = latestBitfinexPositionIdStamp([
    { eventType: BITFINEX_POSITION_ID_STAMP_EVENT, payload: b, createdAt: new Date(2_000) },
    { eventType: BITFINEX_POSITION_ID_STAMP_EVENT, payload: a, createdAt: new Date(1_000) },
    { eventType: BITFINEX_POSITION_ID_STAMP_EVENT, payload: { schema: 'other', bitfinex_position_id: 9 }, createdAt: new Date(3_000) },
    { eventType: 'FILLED', payload: { bitfinex_position_id: 7 }, createdAt: new Date(4_000) },
  ]);
  assert.equal(got?.bitfinex_position_id, PID);
  assert.equal(latestBitfinexPositionIdStamp([]), null);
});

// ---------- executor: stop ordering is unaffected ----------

type Call = { name: string; at: number };
function stopHarness(position: () => Promise<unknown>, recordFails = false) {
  const calls: Call[] = [];
  const recorded: Array<{ type: string; payload: Record<string, unknown> }> = [];
  let seq = 0;
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.logger = { warn: () => undefined, error: () => undefined, log: () => undefined };
  service.activeTrading = {
    submitStopOrder: async () => { calls.push({ name: 'stop-submit', at: seq++ }); return 7_100; },
    findOrder: async () => {
      calls.push({ name: 'stop-auth', at: seq++ });
      return {
        id: 7_100, symbol: BITFINEX_BTC_PERP_SYMBOL, amount: -0.0004, amountOrig: -0.0004,
        price: 64_000, status: 'ACTIVE', orderType: 'STOP', flags: BITFINEX_REDUCE_ONLY_FLAG,
      };
    },
    listActiveOrders: async () => { calls.push({ name: 'orders-read', at: seq++ }); return []; },
    getOpenPositionDetail: () => { calls.push({ name: 'positions-read', at: seq++ }); return position(); },
  };
  service.cycles = {
    recordHireExecutionEvent: async (_u: string, _a: string, _c: string, type: string, payload: Record<string, unknown>) => {
      if (recordFails) throw new Error('db down');
      recorded.push({ type, payload });
    },
  };
  const ctx: Record<string, unknown> = { userId: 'u1', agentId: 'a1', cycleId: 'c1', participantId: 'p1', entryOrderId: 111, fillPath: 'MONITOR_ENTRY_FILL' };
  const input = { positionDirection: 'LONG' as const, qty: 0.0004, stopPrice: 64_000, leverage: 100 };
  return { service, calls, recorded, ctx, input };
}
const flush = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); };

test('stop ordering: the stop is submitted + authenticated and returned before any positions read', async () => {
  mock.timers.enable({ apis: ['setTimeout'] });
  try {
    const h = stopHarness(async () => parseOpenPositionPayload([row(0.0004)]));
    const result = await h.service.submitVerifiedProtectiveStop({}, h.input, h.ctx);
    assert.equal(result.ok, true);
    assert.equal(result.orderId, 7_100);
    // Nothing read on the stop's critical path.
    assert.deepEqual(h.calls.map((c) => c.name), ['stop-submit', 'stop-auth']);
    assert.equal(h.recorded.length, 0);
    mock.timers.tick(2_999);
    await flush();
    assert.equal(h.calls.some((c) => c.name === 'positions-read'), false);
    mock.timers.tick(1);
    await flush();
    assert.deepEqual(h.calls.map((c) => c.name), ['stop-submit', 'stop-auth', 'positions-read', 'orders-read']);
    // Exactly one positions read: the existing sweep read is reused, not duplicated.
    assert.equal(h.calls.filter((c) => c.name === 'positions-read').length, 1);
    assert.equal(h.recorded.length, 1);
    assert.equal(h.recorded[0].type, BITFINEX_POSITION_ID_STAMP_EVENT);
    assert.equal(h.recorded[0].payload.bitfinex_position_id, PID);
    assert.equal(h.recorded[0].payload.status, 'STAMPED');
    assert.equal(h.recorded[0].payload.participant_id, 'p1');
    assert.equal(typeof h.recorded[0].payload.position_id_read_at, 'string');
    // HM N2: stop_order_id is POPULATED with the authenticated stop the sweep keeps.
    assert.equal(h.recorded[0].payload.stop_order_id, 7_100);
    assert.equal(h.recorded[0].payload.bitfinex_order_id, 111);
    // HM N1: fill time defaults to the stop-submit start (a local clock read, no I/O).
    const obs = Date.parse(String(h.recorded[0].payload.fill_observed_at));
    assert.ok(obs <= Date.parse(String(h.recorded[0].payload.position_id_read_started_at)));
  } finally {
    mock.timers.reset();
  }
});

test('HM N1/N2 via the executor: an explicit fill time is honoured; a post-fill position is CREATE_TIME_MISMATCH', async () => {
  mock.timers.enable({ apis: ['setTimeout'] });
  try {
    const late = Date.now() + 60_000;
    const h = stopHarness(async () => parseOpenPositionPayload([row(0.0004, PID, BITFINEX_BTC_PERP_SYMBOL, late)]));
    h.ctx.fillObservedAtMs = late - 5_000;
    const result = await h.service.submitVerifiedProtectiveStop({}, h.input, h.ctx);
    assert.equal(result.ok, true);
    assert.deepEqual(h.calls.map((c) => c.name), ['stop-submit', 'stop-auth']);
    mock.timers.tick(3_000);
    await flush();
    assert.equal(h.calls.filter((c) => c.name === 'positions-read').length, 1, 'zero added Bitfinex reads');
    assert.equal(h.recorded[0].payload.status, 'CREATE_TIME_MISMATCH');
    assert.equal(h.recorded[0].payload.bitfinex_position_id, null);
    assert.equal(h.recorded[0].payload.fill_observed_at, new Date(late - 5_000).toISOString());
    assert.equal(h.recorded[0].payload.stop_order_id, 7_100);
  } finally {
    mock.timers.reset();
  }
});

test('cancel-race path passes its funnel-entry fill time to the stamp', () => {
  const source = String((SignalSubscriberExecutionService.prototype as any).recordCancelRaceFillOwned);
  assert.match(source, /fillPath:\s*`CANCEL_RACE_FILL\/\$\{cancelContext\}`,\s*fillObservedAtMs:\s*fillDetectedAtMs\b/);
  assert.match(source, /const fillDetectedAtMs\s*=\s*Date\.now\(\)/);
});

test('stop ordering: a hanging positions read never delays the stop result', async () => {
  mock.timers.enable({ apis: ['setTimeout'] });
  try {
    const h = stopHarness(() => new Promise(() => undefined));
    const result = await h.service.submitVerifiedProtectiveStop({}, h.input, h.ctx);
    assert.equal(result.ok, true);
    mock.timers.tick(3_000);
    await flush();
    assert.equal(h.recorded.length, 0);
  } finally {
    mock.timers.reset();
  }
});

test('read failure and stamp-write failure never throw into the stop/sweep path', async () => {
  const failing = stopHarness(async () => { throw new Error('positions 500'); });
  failing.ctx.fillObservedAtMs = FILL_OBS;
  await assert.doesNotReject(failing.service.cancelDuplicateProtectiveStops({}, 7_100, failing.input, failing.ctx));
  await flush();
  assert.equal(failing.recorded[0].payload.status, 'READ_FAILED');
  assert.equal(failing.recorded[0].payload.bitfinex_position_id, null);

  const multi = stopHarness(async () => parseOpenPositionPayload([row(0.0004, 1), row(0.0004, 2)]));
  multi.ctx.fillObservedAtMs = FILL_OBS;
  await assert.doesNotReject(multi.service.cancelDuplicateProtectiveStops({}, 7_100, multi.input, multi.ctx));
  await flush();
  assert.equal(multi.recorded[0].payload.status, 'MULTI_POSITION');
  assert.equal(multi.recorded[0].payload.bitfinex_position_id, null);

  const writeFails = stopHarness(async () => parseOpenPositionPayload([row(0.0004)]), true);
  await assert.doesNotReject(writeFails.service.cancelDuplicateProtectiveStops({}, 7_100, writeFails.input, writeFails.ctx));
  await flush();

  const noCtx = stopHarness(async () => parseOpenPositionPayload([row(0.0004)]));
  await noCtx.service.cancelDuplicateProtectiveStops({}, 7_100, noCtx.input);
  await flush();
  assert.equal(noCtx.recorded.length, 0, 'non-fill callers (stop replacement) never stamp');
});

test('fill promotion paths pass a stamp context to the verified stop (no read before it)', () => {
  for (const [name, method] of [
    ['cancel-race', (SignalSubscriberExecutionService.prototype as any).recordCancelRaceFillOwned],
    ['poll', (SignalSubscriberExecutionService.prototype as any).monitorEntry],
    ['reconcile', (SignalSubscriberExecutionService.prototype as any).reconcileUnattributedExchangeFills],
  ] as const) {
    const source = String(method);
    assert.match(source, /submitVerifiedProtectiveStop\([^;]*fillPath:/s, `${name}: stamp context missing`);
  }
  const submit = String((SignalSubscriberExecutionService.prototype as any).submitVerifiedProtectiveStop);
  assert.equal(/getOpenPositionDetail|stampBitfinexPositionId/.test(submit), false, 'no positions read inside the stop submit');
});

// ---------- the field reaches the executor report (#849 contract) ----------

/**
 * TS port of #849 live_copy_monitor_api._report_pid_side (realism-v2/f5-funding-settlement @ 9c22ca9c6):
 * reads ONLY report.position, and only when status === 'STAMPED' and source === 'POST_STOP_POSITIONS_READBACK'.
 */
function reportPidSide(report: Record<string, unknown>): { pid: string | null; side: string | null } {
  const pos = report.position as Record<string, unknown> | undefined;
  if (!pos || typeof pos !== 'object' || Array.isArray(pos)) return { pid: null, side: null };
  if (pos.status !== 'STAMPED' || pos.source !== 'POST_STOP_POSITIONS_READBACK') return { pid: null, side: null };
  const v = pos.bitfinex_position_id;
  const pid = typeof v === 'number' && Number.isInteger(v) && v > 0 ? String(v)
    : typeof v === 'string' && /^\d+$/.test(v) && Number(v) > 0 ? v : null;
  if (pid == null) return { pid: null, side: null };
  const s = String(pos.position_side ?? '').toUpperCase();
  return { pid, side: s === 'LONG' || s === 'SHORT' ? s : null };
}

const T0 = 1_760_000_000_000;
const at = (ms: number) => new Date(T0 + ms);
const ev = (id: string, eventType: string, ms: number, payload: Record<string, unknown>) => ({
  id, participantId: 'p1', eventType, payload, createdAt: at(ms), platformReceivedAt: null,
});
const baseEvents = [
  ev('e2', 'ORDER_PLACED', 850, { limit_price: 60_000, qty: 0.0004, direction: 'LONG', bitfinexOrderId: 111, clientOrderId: 7, margin_usd: 0.25, leverage: 100 }),
  ev('e3', 'FILLED', 5_000, { fill_price: 60_000, qty: 0.0004, exchange_fill_last_at: at(4_000).toISOString(), stop_exchange_ack_at: at(4_600).toISOString(), stop_loss_placed: true, stopOrderId: 222 }),
  ev('e4', 'STOP_LOSS_ARMED', 4_900, { stop_price: 59_790, stopOrderId: 222, qty: 0.0004 }),
];
const stampEvent = (stamp: BitfinexPositionIdStampPayload) => ev('e5', BITFINEX_POSITION_ID_STAMP_EVENT, 8_000, { ...stamp, participant_id: 'p1' });
const exitEvent = ev('e6', 'EXIT', 60_000, { exit_price: 61_000, exit_exchange_fill_price: 61_000, close_exchange_order_id: 333 });
const reports = (events: ReturnType<typeof ev>[]) => buildLiveCopyReports({
  cycle: { id: 'c1', tradeId: 'gs1-a1', intentEnvelope: {} }, correlationId: 'gs1-a1',
  lane: 'L', events, participants: [{ id: 'p1', userId: 'u1' }], accountFor: () => 'acct-x', nowMs: T0 + 120_000,
});

test('bitfinex_position_id reaches the POSITION_CLOSED and ORDER_FILLED reports (#849 _report_pid_side)', () => {
  const stamp = resolve({ ok: true, position: parseOpenPositionPayload([row(0.0004)]) });
  const r = reports([...baseEvents, stampEvent(stamp), exitEvent]);
  for (const type of ['ORDER_FILLED', 'POSITION_CLOSED']) {
    const rep = r.find((x) => x.type === type) as unknown as Record<string, any>;
    assert.ok(rep, type);
    assert.equal(rep.position.bitfinex_position_id, PID);
    assert.equal(rep.position.position_side, 'LONG');
    assert.equal(rep.position.read_at_ts, READ_AT / 1000);
    assert.equal(rep.position.status, 'STAMPED');
    assert.equal(rep.position.source, 'POST_STOP_POSITIONS_READBACK');
    assert.deepEqual(reportPidSide(rep), { pid: String(PID), side: 'LONG' });
    // Survives the JSON the signed report is sent as.
    assert.deepEqual(reportPidSide(JSON.parse(JSON.stringify(rep))), { pid: String(PID), side: 'LONG' });
  }
});

test('null-pid stamps and unstamped trades never leak a pid into the report', () => {
  const failed = resolve({ ok: false, error: new Error('x') });
  const r1 = reports([...baseEvents, stampEvent(failed), exitEvent]);
  const close1 = r1.find((x) => x.type === 'POSITION_CLOSED') as unknown as Record<string, any>;
  assert.equal(close1.position.bitfinex_position_id, null);
  assert.equal(close1.position.status, 'READ_FAILED');
  assert.equal(reportPidSide(close1).pid, null);
  for (const st of ['MULTI_POSITION', 'CREATE_TIME_MISMATCH', 'AMOUNT_MISMATCH']) {
    const blk = liveCopyPositionBlock({ ...resolve({ ok: true, position: parseOpenPositionPayload([row(0.0004)]) }), status: st as never });
    assert.equal(blk?.bitfinex_position_id, null, st);
    assert.equal(reportPidSide({ position: blk } as never).pid, null, st);
  }
  const r2 = reports([...baseEvents, exitEvent]);
  const close2 = r2.find((x) => x.type === 'POSITION_CLOSED') as unknown as Record<string, any>;
  assert.equal('position' in close2, false);
  assert.equal(liveCopyPositionBlock(null), null);
  // A tampered STAMPED payload with a non-integer pid is still nulled.
  assert.equal(liveCopyPositionBlock({ ...resolve({ ok: true, position: parseOpenPositionPayload([row(0.0004)]) }), bitfinex_position_id: 1.5 as never })?.bitfinex_position_id, null);
});

// ---------- live trade export ----------

test('live trade export row + CSV carry bitfinex_position_id and its read time', () => {
  const stamp = resolve({ ok: true, position: parseOpenPositionPayload([row(0.0004)]) });
  const exportRow = mapParticipantToExportRow({
    participant: {
      id: 'p1', status: 'CLOSED' as never, venue: 'bitfinex', fillPrice: 60_000, exitPrice: 61_000,
      pnlUsd: 0.4, pnlMarginPct: 1.6, feeUsd: 0.01, createdAt: at(0), updatedAt: at(60_000),
      events: [
        { eventType: 'FILLED', payload: { fill_price: 60_000 }, createdAt: at(5_000) },
        { eventType: BITFINEX_POSITION_ID_STAMP_EVENT, payload: stamp, createdAt: at(8_000) },
      ],
    },
    cycle: { id: 'c1', tradeId: 'gs1-a1', intentEnvelope: { direction: 'LONG' }, showcaseExitReason: null, createdAt: at(0), closedAt: at(60_000) },
  });
  assert.equal(exportRow.bitfinex_position_id, PID);
  assert.equal(exportRow.bitfinex_position_id_read_at, new Date(READ_AT).toISOString());
  assert.equal(exportRow.bitfinex_position_id_status, 'STAMPED');
  const csv = liveTradesToCsv({
    exportedAt: at(0).toISOString(), agentSlug: 'a', agentName: 'A', exchange: 'bitfinex', sessionStartedAt: null,
    userId: 'u1', tradeCount: 1, closedCount: 1, totalPnlUsd: 0.4, rows: [exportRow],
  });
  const [header, line] = csv.split('\n');
  const cols = header.split(',');
  assert.equal(cols.indexOf('eventsSummary'), cols.length - 4, 'new columns appended after existing ones');
  assert.ok(line.includes(String(PID)));
  const unstamped = mapParticipantToExportRow({
    participant: { ...{ id: 'p2', status: 'CLOSED' as never, venue: 'bitfinex', fillPrice: null, exitPrice: null, pnlUsd: null, pnlMarginPct: null, feeUsd: null, createdAt: at(0), updatedAt: at(0) }, events: [] },
    cycle: { id: 'c2', tradeId: 't2', intentEnvelope: {}, showcaseExitReason: null, createdAt: at(0), closedAt: null },
  });
  assert.equal(unstamped.bitfinex_position_id, null);
  assert.equal(unstamped.bitfinex_position_id_read_at, null);
});
