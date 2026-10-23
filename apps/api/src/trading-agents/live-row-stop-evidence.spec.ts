/**
 * #852 item 4: website live-row countability requires the guardian's stop evidence (same rule as live_guardian):
 * a resident exchange stop (STOP_LOSS_ARMED) confirmed <= 5 s after the fill, no STOP_FAILED, and no guardian flatten.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
import { SignalCycleStatus } from '@prisma/client';
import {
  GUARDIAN_FORCE_FLAT_CLOSE_MARKERS,
  LIVE_FILL_TO_STOP_MAX_S,
  liveRowCountability,
  liveRowCountabilityWithStop,
  liveRowStopCountability,
  liveRowStopEvidenceFromEvents,
} from './live-row-countability';
import { mapParticipantToExportRow } from './live-trade-export.mapper';
import { mapSubscriberExchangeLiveBook } from './subscriber-exchange-live.mapper';

const PY = resolve(__dirname, '../../../../services/btc-conservative-agent');
const t0 = new Date('2026-10-24T06:00:00.000Z');
const at = (s: number) => new Date(t0.getTime() + s * 1000);
const ev = (eventType: string, s: number, payload: Record<string, unknown> = {}) => ({ eventType, payload, createdAt: at(s) });

test('item 4: the 5 s bound and the close markers are the guardian constants', () => {
  const lg = readFileSync(resolve(PY, 'live_guardian.py'), 'utf8');
  const ltg = readFileSync(resolve(PY, 'live_tile_gate.py'), 'utf8');
  assert.equal(Number(/^FILL_TO_STOP_MAX_SEC = ([\d.]+)/m.exec(lg)?.[1]), LIVE_FILL_TO_STOP_MAX_S);
  assert.equal(Number(/^STOP_RESIDENT_MAX_AGE_S = ([\d.]+)/m.exec(ltg)?.[1]), LIVE_FILL_TO_STOP_MAX_S);
  const block = /FORCE_FLAT_CLOSE_MARKERS = \(([\s\S]*?)\)\s/.exec(ltg)?.[1] ?? '';
  const names = [...block.matchAll(/"([A-Z_]+)"/g)].map((m) => m[1]).concat(
    [...block.matchAll(/\b(STOP_FAILURE_FLATTEN|LIQ_CHECK_FLATTEN|LIQ_CLAMP_FLATTEN)\b/g)].map((m) => m[1]));
  assert.ok(names.length >= 6, block);
  for (const n of names) assert.ok(GUARDIAN_FORCE_FLAT_CLOSE_MARKERS.includes(n), n);
  for (const n of ['STOP_FAILURE_FLATTEN', 'LIQ_CHECK_FLATTEN', 'LIQ_CLAMP_FLATTEN', 'AUTHENTICATED_EMERGENCY_CLOSE']) {
    assert.equal(liveRowCountability(`EXIT:${n}`).nonCountable, true, n);
  }
});

test('item 4: stop evidence rule (missing / late / failed / unverifiable => non-countable)', () => {
  const ok = { fillAtMs: 0, stopConfirmedAtMs: 5000, stopFailed: false };
  assert.deepEqual(liveRowStopCountability(ok), { nonCountable: false, nonCountableReason: null });
  assert.equal(liveRowStopCountability({ ...ok, stopConfirmedAtMs: 5001 }).nonCountableReason, 'NO_STOP_EVIDENCE:FILL_TO_STOP_GT_5S');
  assert.equal(liveRowStopCountability({ ...ok, stopConfirmedAtMs: null }).nonCountableReason, 'NO_STOP_EVIDENCE:STOP_NOT_CONFIRMED');
  assert.equal(liveRowStopCountability({ ...ok, stopFailed: true }).nonCountableReason, 'NO_STOP_EVIDENCE:STOP_FAILED');
  assert.equal(liveRowStopCountability({ ...ok, fillAtMs: null }).nonCountableReason, 'NO_STOP_EVIDENCE:FILL_TIME_UNKNOWN');
  assert.equal(liveRowStopCountability(null).nonCountableReason, 'NO_STOP_EVIDENCE:UNLINKED');
  // a forced / guardian close reason wins over good stop evidence
  assert.equal(liveRowCountabilityWithStop(ok, 'STOP_FAILURE_FLATTEN').nonCountableReason, 'FORCED_CLOSE:STOP_FAILURE_FLATTEN');
});

test('item 4: evidence comes from the same events as the Fly reporter (fill_at_ts / stop_confirmed_at_ts / STOP_FAILED)', () => {
  const e = liveRowStopEvidenceFromEvents([
    ev('ORDER_PLACED', 0),
    ev('FILLED', 3, { exchange_fill_last_at: at(1).toISOString(), stop_loss_placed: true }),
    ev('STOP_LOSS_ARMED', 5),
  ]);
  assert.deepEqual(e, { fillAtMs: at(1).getTime(), stopConfirmedAtMs: at(5).getTime(), stopFailed: false });
  assert.equal(liveRowStopEvidenceFromEvents([ev('FILLED', 2, { stop_loss_placed: false })]).stopFailed, true);
  assert.equal(liveRowStopEvidenceFromEvents([ev('FILLED', 2)]).fillAtMs, at(2).getTime());
});

function closedRow(tradeId: string, events: ReturnType<typeof ev>[], reason: string | null = 'TP') {
  return {
    status: SignalCycleStatus.CLOSED, fillPrice: 100_000, exitPrice: 100_100, pnlUsd: 3, pnlMarginPct: 10,
    limitPrice: 100_000, qty: 0.03, terminalReason: null, exchangeProven: true, createdAt: t0, updatedAt: at(60),
    stopEvidence: liveRowStopEvidenceFromEvents(events),
    cycle: { tradeId, status: SignalCycleStatus.CLOSED, intentEnvelope: { direction: 'LONG' }, showcaseExitReason: reason, createdAt: t0 },
  };
}

test('item 4: the website live book counts a row only with stop evidence (requireStopEvidence)', () => {
  const participants = [
    closedRow('good', [ev('FILLED', 1), ev('STOP_LOSS_ARMED', 4)]),
    closedRow('late', [ev('FILLED', 1), ev('STOP_LOSS_ARMED', 9)]),
    closedRow('none', [ev('FILLED', 1)]),
    closedRow('failed', [ev('FILLED', 1, { stop_loss_placed: false }), ev('STOP_LOSS_ARMED', 2)]),
    closedRow('sff', [ev('FILLED', 1), ev('STOP_LOSS_ARMED', 2)], 'STOP_FAILURE_FLATTEN'),
  ];
  const book = mapSubscriberExchangeLiveBook({
    orders: [], position: null, participants, requireStopEvidence: true,
    ledgerCloses: [{ ledgerId: 9, closedAt: at(9999), pnlUsd: 1 } as never],
  });
  const by = Object.fromEntries(book.trades.map((t) => [t.tradeId, t]));
  assert.equal(by.good?.nonCountable, false);
  assert.equal(by.late?.nonCountableReason, 'NO_STOP_EVIDENCE:FILL_TO_STOP_GT_5S');
  assert.equal(by.none?.nonCountableReason, 'NO_STOP_EVIDENCE:STOP_NOT_CONFIRMED');
  assert.equal(by.failed?.nonCountableReason, 'NO_STOP_EVIDENCE:STOP_FAILED');
  assert.equal(by.sff?.nonCountableReason, 'FORCED_CLOSE:STOP_FAILURE_FLATTEN');
  assert.equal(by['bfx-9']?.nonCountableReason, 'NO_STOP_EVIDENCE:UNLINKED');
  // without the flag (non-exchange books) the reason-only B4 rule is unchanged
  const paper = mapSubscriberExchangeLiveBook({ orders: [], position: null, participants });
  assert.equal(Object.fromEntries(paper.trades.map((t) => [t.tradeId, t])).none?.nonCountable, false);
});

test('item 4: the live trade export applies the same rule to filled rows', () => {
  const base = { id: 'p1', status: SignalCycleStatus.CLOSED, venue: 'bitfinex', fillPrice: 100_000, exitPrice: 100_100,
    pnlUsd: 3, pnlMarginPct: 10, feeUsd: null, createdAt: t0, updatedAt: at(60) };
  const cycle = { id: 'c1', tradeId: 't1', intentEnvelope: {}, showcaseExitReason: 'TP', createdAt: t0, closedAt: at(60) };
  const good = mapParticipantToExportRow({ participant: { ...base, events: [ev('FILLED', 1), ev('STOP_LOSS_ARMED', 3), ev('EXIT', 60)] }, cycle });
  assert.equal(good.nonCountable, false);
  const none = mapParticipantToExportRow({ participant: { ...base, events: [ev('FILLED', 1), ev('EXIT', 60)] }, cycle });
  assert.equal(none.nonCountableReason, 'NO_STOP_EVIDENCE:STOP_NOT_CONFIRMED');
});

test('item 4: wiring - the relay live book and export pass the stop evidence', () => {
  const svc = readFileSync(resolve(__dirname, 'trading-agents.service.ts'), 'utf8');
  assert.ok(svc.includes('stopEvidence: liveRowStopEvidenceFromEvents(r.events)'));
  assert.ok(svc.includes("requireStopEvidence: instance.exchangeProvider === 'bitfinex'"));
});
