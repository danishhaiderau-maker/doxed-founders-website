import assert from 'node:assert/strict';
import test from 'node:test';
import { boundedExitLegMs, EXIT_TRIGGER_OUT_OF_WINDOW, safeIsoMs } from './exit-leg-time';
import { liveRowLegCheck } from './live-row-countability';
import { liveLegStamps } from './live-trade-export.mapper';
import {
  railwayExitLegStamps,
  readSignedShowcaseClose,
  residentExitLegStamps,
  signedCloseExitStamps,
  SignalSubscriberExecutionService,
} from './signal-subscriber-execution.service';

// HM #842 R3 blocker + RK ruling (24 Oct 2026): exit leg stamps never throw, and any throw after the Bitfinex close
// reconciles the DB to the exchange (EXIT + cleanup) instead of leaving the lot OPEN while the exchange is flat.
const RX = 1_761_300_000_200; // relay receive (ms)
const EV = 1_761_300_000_000; // signed close event (ms)
const BAD: Array<[string, number]> = [
  ['us', RX * 1_000],
  ['ns', RX * 1_000_000],
  ['negative', -RX],
  ['far-future', RX + 365 * 86_400_000],
  ['NaN', Number.NaN],
  ['Infinity', Number.POSITIVE_INFINITY],
  ['beyond Date range', 9e15],
];

test('R3: safeIsoMs / boundedExitLegMs never throw on us, ns, negative, far-future, NaN', () => {
  for (const [name, v] of BAD) {
    assert.doesNotThrow(() => safeIsoMs(v), name);
    assert.doesNotThrow(() => boundedExitLegMs(v, { sourceEventAtMs: EV, platformReceivedAtMs: RX }), name);
    assert.equal(boundedExitLegMs(v, { sourceEventAtMs: EV, platformReceivedAtMs: RX }), null, name);
  }
  assert.equal(safeIsoMs(9e15), null);
  assert.equal(safeIsoMs(RX), new Date(RX).toISOString());
  assert.equal(boundedExitLegMs(EV - 100, { sourceEventAtMs: EV, platformReceivedAtMs: RX }), EV - 100);
  assert.equal(boundedExitLegMs(EV - 5 * 60_000 - 1, { sourceEventAtMs: EV, platformReceivedAtMs: RX }), null);
  assert.equal(boundedExitLegMs(RX + 5_001, { sourceEventAtMs: EV, platformReceivedAtMs: RX }), null);
  assert.equal(boundedExitLegMs(EV - 100, { sourceEventAtMs: EV }), null); // no receive -> no window -> unmeasured
});

test('R3: railwayExitLegStamps never throws; out-of-window trigger is unmeasured (non-countable), never stamped', () => {
  for (const [name, v] of BAD) {
    let out: Record<string, unknown> = {};
    assert.doesNotThrow(() => {
      out = railwayExitLegStamps({ exitTriggerAtMs: v, flySendStartedAtMs: v, sourceEventAtMs: EV, platformReceivedAtMs: RX });
    }, name);
    assert.equal(out.exit_trigger_ts, undefined, name);
    assert.equal(out.fly_send_started_at, undefined, name);
    assert.equal(out.exit_order_class, 'BOT_SENT', name);
    assert.equal(out.platform_received_at, new Date(RX).toISOString(), name);
    if (Number.isFinite(v) && v > 0) assert.equal(out.exit_trigger_unmeasured, EXIT_TRIGGER_OUT_OF_WINDOW, name);
    assert.doesNotThrow(() => residentExitLegStamps(v, v), name);
  }
  assert.doesNotThrow(() => railwayExitLegStamps({ platformReceivedAtMs: Number.NaN }));
});

test('R3: signed close trigger in seconds is bounded too (us/ns-as-seconds -> null)', () => {
  const win = { sourceEventAtMs: EV, platformReceivedAtMs: RX };
  assert.deepEqual(signedCloseExitStamps({ showcase_exit_trigger_ts: (EV - 100) / 1000 }, win).exitTriggerAtMs, EV - 100);
  for (const s of [EV * 1000, EV * 1_000_000, -1, Number.NaN, (EV + 86_400_000) / 1000, '1761300000']) {
    assert.doesNotThrow(() => signedCloseExitStamps({ showcase_exit_trigger_ts: s }, win));
    assert.equal(signedCloseExitStamps({ showcase_exit_trigger_ts: s }, win).exitTriggerAtMs, null, String(s));
  }
  const ctx = {
    action: 'ENTER', trade_id: 'cont-1',
    context: {
      signed_showcase_event: true, showcase_event: 'POSITION_CLOSED', showcase_event_id: 'e1', showcase_event_seq: 3,
      showcase_event_ts: new Date(EV).toISOString(), platform_received_at: new Date(RX).toISOString(),
      showcase_exit_trigger_ts: EV * 1_000_000,
    },
  };
  assert.doesNotThrow(() => readSignedShowcaseClose(ctx, 'cont-1', RX + 100));
});

const claim = {
  ownerToken: 'tok-1', requestId: 'req-1', phase: 'CLAIMED' as const, recovered: false,
  authority: { kind: 'SIGNED_SHOWCASE_CLOSE', canonicalTradeId: 'cont-1', lifecycleGeneration: 1, evidence: { e: 1 } },
};
const target = {
  closeQty: 0.01, targetAmount: 0, confirmedExchangeAmount: 0, remainingManagedOrderIds: [],
  accountWideActiveOrderIds: [], accountWideManagedOrderIds: [], orphanOrderIds: [], foreignOrderIds: [],
  closeSubmitStartedAtMs: RX + 50, closeExchangeAckAtMs: RX + 120, closeConfirmedAtMs: RX + 200,
};

function harness(recordImpl?: (type: string, body: Record<string, unknown>, n: number) => void) {
  const svc = Object.create(SignalSubscriberExecutionService.prototype) as any;
  const events: Array<{ type: string; body: Record<string, unknown> }> = [];
  const pauses: string[] = [];
  const calls = { close: 0 };
  let n = 0;
  Object.assign(svc, {
    exitingLots: new Set<string>(),
    showcaseVanishedMisses: new Map([['p1', 1]]),
    showcasePositionAbsentMisses: new Map([['p1', 1]]),
    showcasePositionAbsentSince: new Map([['p1', 1]]),
    showcaseFlatOpenSince: new Map([['p1', 1]]),
    logger: { log() {}, warn() {}, error() {}, debug() {} },
    swallowedError: () => () => undefined,
    prisma: { signalCycleParticipant: { findUnique: async () => ({ status: 'OPEN', terminalCloseClaimToken: null }) } },
    hasParticipantExited: async () => false,
    acquireTerminalCloseClaim: async () => claim,
    readOpenPositionDetailWithRetry: async () => ({ amount: 0.01, basePrice: 100_000 }),
    expectedRemainingLedgerAmount: async () => 0,
    cancelLinkedPendingLimits: async () => undefined,
    activeTrading: { getMarkPrice: async () => 101_000 },
    terminalCloseClaimStillValid: async () => true,
    closeParticipantPositionToLedgerTarget: async () => { calls.close += 1; return target; },
    resolveNormalCloseExchangeEvidence: async () => ({ entry_exchange_fill_price: 100_000, exit_exchange_fill_price: 101_050 }),
    pauseUserRelayForPositionMismatch: async (_u: string, _a: string, msg: string) => { pauses.push(msg); },
    ensureProtectiveStopForVerifiedExitResidual: async () => undefined,
    cycles: {
      recordHireExecutionEvent: async (_u: string, _a: string, _c: string, type: string, body: Record<string, unknown>) => {
        n += 1;
        recordImpl?.(type, body, n);
        events.push({ type, body });
        return {};
      },
    },
  });
  return { svc, events, pauses, calls };
}

const meta = { qty: 0.01, direction: 'LONG', limitPrice: 100_000 } as any;
const run = (svc: any, opts: Record<string, unknown>) => svc.executeShowcaseMirrorCloseOwned(
  'agent-1', 'user-1', { id: 'cycle-1', intentEnvelope: {} }, { id: 'p1', fillPrice: { toNumber: () => 100_000 } },
  meta, { apiKey: 'k', apiSecret: 's' }, { terminalAuthority: claim.authority, sourceEventAtMs: EV, platformReceivedAtMs: RX, showcaseExitPrice: 101_000, ...opts },
);
const cleared = (svc: any) =>
  ['showcaseVanishedMisses', 'showcasePositionAbsentMisses', 'showcasePositionAbsentSince', 'showcaseFlatOpenSince']
    .every((k) => !svc[k].has('p1')) && !svc.exitingLots.has('p1');

test('R3: us/ns/negative/far-future/NaN trigger after the Bitfinex close never throws; the EXIT is still written', async () => {
  for (const [name, v] of BAD) {
    const h = harness();
    assert.equal(await run(h.svc, { exitTriggerAtMs: v, flySendStartedAtMs: v }), true, name);
    assert.equal(h.calls.close, 1, name);
    const exit = h.events.find((e) => e.type === 'EXIT');
    assert.ok(exit, `${name}: EXIT written`);
    assert.equal(exit.body.exit_trigger_ts, undefined, name); // unmeasured -> LIVE_LEG_MISSING (non-countable)
    assert.equal(exit.body.exit_post_close_reconciled, undefined, name); // normal path, no fallback needed
    assert.equal(exit.body.terminal_close_phase, 'CONFIRMED', name);
    assert.deepEqual(h.pauses, [], name);
    assert.ok(cleared(h.svc), `${name}: cleanup ran`);
  }
});

test('R3: an in-window trigger is still stamped on the normal EXIT', async () => {
  const h = harness();
  assert.equal(await run(h.svc, { exitTriggerAtMs: EV - 100, flySendStartedAtMs: EV + 10 }), true);
  const exit = h.events.find((e) => e.type === 'EXIT')!;
  assert.equal(exit.body.exit_trigger_ts, new Date(EV - 100).toISOString());
  assert.equal(exit.body.fly_send_started_at, new Date(EV + 10).toISOString());
});

test('RK: a forced throw after the Bitfinex close reconciles the DB to flat (EXIT from the exchange fill + cleanup)', async () => {
  const h = harness((type, _b, n) => { if (type === 'EXIT' && n === 1) throw new RangeError('Invalid time value'); });
  assert.equal(await run(h.svc, { exitTriggerAtMs: EV - 100 }), true);
  assert.equal(h.calls.close, 1); // never re-submitted
  const exits = h.events.filter((e) => e.type === 'EXIT');
  assert.equal(exits.length, 1);
  const b = exits[0].body;
  assert.equal(b.exit_post_close_reconciled, true);
  assert.match(String(b.exit_post_close_error), /RangeError: Invalid time value/);
  assert.equal(b.exit_price, 101_050); // the exchange's own fill
  assert.equal(b.pnl_source, 'exchange_fill');
  assert.equal(b.pnl_usd, 10.5);
  assert.equal(b.terminal_close_phase, 'CONFIRMED');
  assert.equal(b.terminal_close_claim_token, 'tok-1');
  assert.equal(b.terminal_close_request_id, 'req-1');
  assert.equal((b.final_reconciliation as any)?.schema, 'relay_final_reconciliation_v1');
  assert.equal(b.exit_trigger_ts, undefined); // non-countable (R17)
  assert.equal(b.exit_legs_unmeasured, 'POST_CLOSE_RECONCILED');
  assert.deepEqual(h.pauses, []);
  assert.ok(cleared(h.svc));
});

test('R3: an evidence read failure after the close is non-fatal; the EXIT is still written', async () => {
  const h = harness();
  let k = 0;
  h.svc.resolveNormalCloseExchangeEvidence = async () => {
    k += 1;
    if (k === 1) throw new Error('evidence boom');
    return { exit_exchange_fill_price: 101_050, entry_exchange_fill_price: 100_000 };
  };
  assert.equal(await run(h.svc, {}), true);
  const exits = h.events.filter((e) => e.type === 'EXIT');
  assert.equal(exits.length, 1);
  assert.equal(exits[0].body.exit_post_close_reconciled, undefined);
  assert.ok(cleared(h.svc));
});

test('RK: if the reconcile write itself fails, the relay is paused (never silent) and the lot is released', async () => {
  const h = harness((type) => { if (type === 'EXIT') throw new Error('db down'); });
  assert.equal(await run(h.svc, {}), false);
  assert.equal(h.pauses.length, 1);
  assert.match(h.pauses[0], /^EXIT_POST_CLOSE_RECONCILE_FAILED cycle=cycle-1/);
  assert.equal(h.svc.exitingLots.has('p1'), false);
  assert.equal(h.svc.showcaseFlatOpenSince.has('p1'), true); // tracking kept for the reconciler
});

test('RK: a throw after the EXIT was written (receipt/log) does not write a second EXIT', async () => {
  const h = harness();
  const orig = h.svc.logger;
  h.svc.logger = { ...orig, log: () => { throw new Error('log boom'); } };
  assert.equal(await run(h.svc, {}), true);
  assert.equal(h.events.filter((e) => e.type === 'EXIT').length, 1);
  assert.ok(cleared(h.svc));
});

test('B1 merge: closeVirtualLot (force-flat / policy closes) routes a post-close throw to the SAME reconcile', async () => {
  const h = harness((type, _b, n) => { if (type === 'EXIT' && n === 1) throw new Error('db write after close failed'); });
  Object.assign(h.svc, { positionRuntime: new Map(), stopManagerCircuitOpen: new Map() });
  const ok = await h.svc.closeVirtualLot('agent-1', 'user-1', 'cycle-1', 'p1', meta, { apiKey: 'k', apiSecret: 's' }, {
    reason: 'AUTHENTICATED_EMERGENCY_CLOSE', mark: 101_000, fillPrice: 100_000, leverage: 1, peakMarginPct: 0,
    unrealMarginPct: 0, stopLossMarginPct: 1, terminalAuthority: claim.authority,
  });
  assert.equal(ok, true);
  assert.equal(h.calls.close, 1); // never re-submitted
  const exits = h.events.filter((e) => e.type === 'EXIT');
  assert.equal(exits.length, 1);
  assert.equal(exits[0].body.exit_post_close_reconciled, true);
  assert.equal(exits[0].body.exit_reason, 'EMERGENCY_CLOSE');
  assert.equal(exits[0].body.exit_price, 101_050);
  assert.equal(exits[0].body.exit_legs_unmeasured, 'POST_CLOSE_RECONCILED');
  assert.equal(exits[0].body.terminal_close_claim_token, 'tok-1');
  assert.deepEqual(h.pauses, []);
  assert.ok(cleared(h.svc));
});

test('R3: executor wake bounds exit stamps to the close window (us/ns/negative/far-future/NaN -> null, wake kept)', async () => {
  const { parseExecutorWakeRequest } = await import('../relay-executor-wake-http');
  const now = Date.now();
  const tid = 'gs1-446291153ae7';
  const wake = (v: unknown) => parseExecutorWakeRequest({
    trigger: 'POSITION_CLOSED', at: new Date(now).toISOString(), tradeId: tid,
    signedClose: {
      tradeId: tid, eventId: `close-${tid}`, eventSeq: 3, exitPrice: 83_900, exitReason: 'TP',
      sourceEventAtMs: now - 400, platformReceivedAtMs: now, exitTriggerAtMs: v, flySendStartedAtMs: v,
    },
  });
  for (const v of [now * 1_000, now * 1_000_000, -now, now + 86_400_000, Number.NaN, 9e15]) {
    const w = wake(v);
    assert.equal(w?.trigger, 'POSITION_CLOSED', String(v));
    assert.equal(w?.signedClose?.exitTriggerAtMs, null, String(v));
    assert.equal(w?.signedClose?.flySendStartedAtMs, null, String(v));
  }
  assert.equal(wake(now - 500)?.signedClose?.exitTriggerAtMs, now - 500);
});

test('R3 nit: exit_signal_at is no longer a fallback for the exit trigger (only exit_trigger_ts counts)', () => {
  const iso = (ms: number) => new Date(ms).toISOString();
  const envelope = { context: { fly_live_approval: { signal_at_ts: 9, created_at_ts: 9.1 }, platform_received_at: iso(9_200) } };
  const placed = { eventType: 'ORDER_PLACED', payload: { entry_exchange_ack_at: iso(9_600) } };
  const base = { exit_order_class: 'BOT_SENT', fly_send_started_at: iso(50_010), platform_received_at: iso(50_080), close_exchange_ack_at: iso(50_400) };
  const legacy = liveLegStamps(envelope, [placed, { eventType: 'EXIT', payload: { ...base, exit_signal_at: iso(49_900) } }], true);
  assert.equal(legacy[1]?.exit_signal_at_ts, null);
  assert.deepEqual(liveRowLegCheck(legacy[0], legacy[1], legacy[2]).legReasons, ['LIVE_LEG_MISSING:exit.signal']);
  const measured = liveLegStamps(envelope, [placed, { eventType: 'EXIT', payload: { ...base, exit_trigger_ts: iso(49_900) } }], true);
  assert.deepEqual(liveRowLegCheck(measured[0], measured[1], measured[2]).legReasons, []);
});
