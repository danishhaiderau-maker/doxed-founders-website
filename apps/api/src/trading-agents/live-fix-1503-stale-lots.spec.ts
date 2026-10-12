import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { staleLotDecision, summarize, STALE_LOT_MIN_AGE_MS } from './stale-lot-cleanup';

const now = 10_000_000;
const lot = (o: Partial<{ updatedAtMs: number; hasExitEvent: boolean }> = {}) => ({ id: 'p', status: 'OPEN', tradeId: 't', updatedAtMs: now - STALE_LOT_MIN_AGE_MS - 1, hasExitEvent: false, ...o });
const flat = { readOk: true, positionAmount: 0, openOrders: 0 };

describe('LIVE-FIX-1503 stale lot cleanup decision', () => {
  it('closes only when the account is proven flat and the lot is old', () => {
    assert.deepEqual(staleLotDecision(lot(), flat, now), { stale: true, reason: 'NO_EXCHANGE_EXPOSURE' });
    assert.deepEqual(staleLotDecision(lot({ hasExitEvent: true }), flat, now), { stale: true, reason: 'EXIT_COMPLETED_NOT_CLOSED' });
  });
  it('never acts on a failed read, an open position/order, or a recent lot', () => {
    assert.equal(staleLotDecision(lot(), { ...flat, readOk: false }, now).stale, false);
    assert.equal(staleLotDecision(lot(), { ...flat, positionAmount: 0.0003 }, now).stale, false);
    assert.equal(staleLotDecision(lot(), { ...flat, openOrders: 1 }, now).stale, false);
    assert.equal(staleLotDecision(lot({ updatedAtMs: now - 1000 }), flat, now).stale, false);
  });
  it('summarize gives n/min/p50/p95', () => {
    assert.deepEqual(summarize([1, 2, 3, 4, 100]), { n: 5, min: 1, p50: 3, p95: 100 });
  });
});
