import assert from 'node:assert/strict';
import test from 'node:test';
import type { ExchangeCredentials } from './exchange-adapter.interface';
import {
  bitfinexCredentialFingerprint,
  credentialFingerprintMatches,
  readBitfinexExchangeSnapshot,
} from './exchanges.service';

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

test('Bitfinex credential identity fingerprint is stable and binds key plus secret', () => {
  const first = bitfinexCredentialFingerprint({ apiKey: 'key-a', apiSecret: 'secret-a' });
  assert.equal(first, bitfinexCredentialFingerprint({ apiKey: 'key-a', apiSecret: 'secret-a' }));
  assert.notEqual(first, bitfinexCredentialFingerprint({ apiKey: 'key-b', apiSecret: 'secret-a' }));
  assert.notEqual(first, bitfinexCredentialFingerprint({ apiKey: 'key-a', apiSecret: 'secret-b' }));
  assert.equal(credentialFingerprintMatches(first, first.toUpperCase()), true);
});

test('Bitfinex credential identity comparison fails closed on malformed or wrong fingerprints', () => {
  const first = bitfinexCredentialFingerprint({ apiKey: 'key-a', apiSecret: 'secret-a' });
  const other = bitfinexCredentialFingerprint({ apiKey: 'key-b', apiSecret: 'secret-b' });
  assert.equal(credentialFingerprintMatches(first, other), false);
  assert.equal(credentialFingerprintMatches(first, ''), false);
  assert.equal(credentialFingerprintMatches('not-a-hash', first), false);
});
