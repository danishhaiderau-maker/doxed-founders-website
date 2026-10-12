import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { stopSideViolation, assertProtectiveStopSide, setStopSideTickerFetchForTests, stopSideRefusals } from './bitfinex-api.client';

const stop = (amount: string, price: string) => ({ type: 'STOP', symbol: 'tBTCF0:USTF0', amount, price, flags: 1024 });

describe('LIVE-FIX-1505 stop-side guard', () => {
  it('pure check', () => {
    assert.equal(stopSideViolation(-0.0003, 82438, 82700, 82701), null);
    assert.match(String(stopSideViolation(-0.0003, 82879, 82700, 82701)), /SELL_STOP_AT_OR_ABOVE_BID/);
    assert.match(String(stopSideViolation(-0.0003, 82700, 82700, 82701)), /SELL_STOP/);
    assert.equal(stopSideViolation(0.0003, 83000, 82700, 82701), null);
    assert.match(String(stopSideViolation(0.0003, 82701, 82700, 82701)), /BUY_STOP_AT_OR_BELOW_ASK/);
  });
  it('refuses the 00:13 wrong-side stop and records it; passes a correct one', async () => {
    setStopSideTickerFetchForTests(async () => ({ bid: 82700, ask: 82701 }));
    await assert.rejects(assertProtectiveStopSide('v2/auth/w/order/submit', stop('-0.00030000', '82879')), /STOP_SIDE_REFUSED/);
    assert.ok(stopSideRefusals.length >= 1);
    await assertProtectiveStopSide('v2/auth/w/order/submit', stop('-0.00030000', '82438'));
  });
  it('ticker failure never blocks; non-stop orders untouched', async () => {
    setStopSideTickerFetchForTests(async () => { throw new Error('down'); });
    await assertProtectiveStopSide('v2/auth/w/order/submit', stop('-0.0003', '99999'));
    await assertProtectiveStopSide('v2/auth/w/order/submit', { type: 'LIMIT', amount: '-0.0003', price: '99999', flags: 0 });
  });
});
