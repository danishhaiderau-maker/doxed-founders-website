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

import { BitfinexPublicTickerCache } from './bitfinex-public-ticker-ws';
import { setStopGuardQuoteCache, setStopGuardListener, type StopGuardEvent } from './bitfinex-api.client';

describe('LIVE-FIX-1506 WS quote cache + guard events', () => {
  it('parses subscribed + ticker frames, ignores hb, expires stale quotes', () => {
    const c = new BitfinexPublicTickerCache(['tBTCF0:USTF0'], () => ({ on() {}, send() {}, close() {} }));
    c.handle(JSON.stringify({ event: 'subscribed', channel: 'ticker', chanId: 7, symbol: 'tBTCF0:USTF0' }), 0);
    c.handle(JSON.stringify([7, 'hb']), 0);
    c.handle(JSON.stringify([7, [82700, 1, 82701, 1, 0, 0, 82700, 0, 0, 0]]), 1000);
    assert.deepEqual(c.get('tBTCF0:USTF0', 3000, 2000), { bid: 82700, ask: 82701, atMs: 1000 });
    assert.equal(c.get('tBTCF0:USTF0', 3000, 5000), null);
  });
  it('uses the WS quote first and emits REFUSED / FAIL_OPEN events', async () => {
    const events: StopGuardEvent[] = [];
    setStopGuardListener((e) => events.push(e));
    setStopGuardQuoteCache(() => ({ bid: 82700, ask: 82701 }));
    setStopSideTickerFetchForTests(async () => { throw new Error('REST must not be needed'); });
    await assert.rejects(assertProtectiveStopSide('v2/auth/w/order/submit', stop('-0.0003', '82879')), /STOP_SIDE_REFUSED/);
    assert.equal(events.at(-1)?.kind, 'REFUSED');
    assert.equal(events.at(-1)?.quote_source, 'WS');
    setStopGuardQuoteCache(() => null);
    await assertProtectiveStopSide('v2/auth/w/order/submit', stop('-0.0003', '82879'));
    assert.equal(events.at(-1)?.kind, 'FAIL_OPEN');
    setStopGuardListener(null); setStopGuardQuoteCache(null);
  });
});
