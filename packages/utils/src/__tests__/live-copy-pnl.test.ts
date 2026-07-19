import assert from 'node:assert/strict';
import test from 'node:test';
import { computeLiveCopyEquityPnl } from '../live-copy-pnl';

test('does not double-count unrealized PnL already included by Bitfinex metrics', () => {
  assert.deepEqual(
    computeLiveCopyEquityPnl({
      backendSessionPnlUsd: 0.81,
      bookRealizedPnlUsd: 0,
      unrealizedPnlUsd: 0.81,
    }),
    {
      sessionPnlUsd: 0.81,
      equityPnlUsd: 0.81,
      usedBookFallback: false,
    },
  );
});

test('adds unrealized PnL only when falling back to the realized trade book', () => {
  assert.deepEqual(
    computeLiveCopyEquityPnl({
      backendSessionPnlUsd: 0,
      bookRealizedPnlUsd: 1.77,
      unrealizedPnlUsd: -0.25,
    }),
    {
      sessionPnlUsd: 1.77,
      equityPnlUsd: 1.52,
      usedBookFallback: true,
    },
  );
});
