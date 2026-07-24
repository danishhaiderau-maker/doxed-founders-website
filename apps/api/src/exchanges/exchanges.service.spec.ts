import assert from 'node:assert/strict';
import test from 'node:test';
import type { ExchangeCredentials } from './exchange-adapter.interface';
import { readBitfinexExchangeSnapshot } from './exchanges.service';

const creds: ExchangeCredentials = { apiKey: 'test', apiSecret: 'test' };

test('a true empty Bitfinex account remains a verified empty snapshot', async () => {
  const snapshot = await readBitfinexExchangeSnapshot(
    {
      listActiveOrders: async () => [],
      getOpenPositionDetail: async () => null,
    },
    creds,
  );
  assert.deepEqual(snapshot, { orders: [], position: null });
});

test('an active-order read failure is unavailable, not flat', async () => {
  const snapshot = await readBitfinexExchangeSnapshot(
    {
      listActiveOrders: async () => {
        throw new Error('orders unavailable');
      },
      getOpenPositionDetail: async () => null,
    },
    creds,
  );
  assert.equal(snapshot, null);
});

test('a position read failure is unavailable, not flat', async () => {
  const snapshot = await readBitfinexExchangeSnapshot(
    {
      listActiveOrders: async () => [],
      getOpenPositionDetail: async () => {
        throw new Error('position unavailable');
      },
    },
    creds,
  );
  assert.equal(snapshot, null);
});
