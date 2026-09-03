import assert from 'node:assert/strict';
import test from 'node:test';
import type { ExchangeCredentials } from './exchange-adapter.interface';
import {
  bitfinexCredentialFingerprint,
  credentialFingerprintMatches,
  ExchangesService,
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

function resolverFor(
  row: Record<string, unknown> | null,
  decrypt: (payload: string) => string,
): ExchangesService {
  return new ExchangesService(
    {
      integrationCredential: {
        findUnique: async () => row,
      },
    } as never,
    { decrypt } as never,
  );
}

async function withCredentialEnvironment(
  values: { subscriber?: string; expected?: string },
  run: () => Promise<void>,
) {
  const previousSubscriber = process.env.SUBSCRIBER_EXECUTION_ENABLED;
  const previousExpected = process.env.BITFINEX_EXPECTED_CREDENTIAL_FINGERPRINT;
  try {
    if (values.subscriber == null) delete process.env.SUBSCRIBER_EXECUTION_ENABLED;
    else process.env.SUBSCRIBER_EXECUTION_ENABLED = values.subscriber;
    if (values.expected == null) delete process.env.BITFINEX_EXPECTED_CREDENTIAL_FINGERPRINT;
    else process.env.BITFINEX_EXPECTED_CREDENTIAL_FINGERPRINT = values.expected;
    await run();
  } finally {
    if (previousSubscriber == null) delete process.env.SUBSCRIBER_EXECUTION_ENABLED;
    else process.env.SUBSCRIBER_EXECUTION_ENABLED = previousSubscriber;
    if (previousExpected == null) delete process.env.BITFINEX_EXPECTED_CREDENTIAL_FINGERPRINT;
    else process.env.BITFINEX_EXPECTED_CREDENTIAL_FINGERPRINT = previousExpected;
  }
}

test('credential resolution distinguishes missing row and token without secret material', async () => {
  const missingRow = await resolverFor(null, () => 'unused').resolveUserCredentials('user', 'bitfinex');
  assert.deepEqual(missingRow, { ok: false, code: 'ROW_MISSING', credentials: null });

  const missingToken = await resolverFor({ token: '   ' }, () => 'unused')
    .resolveUserCredentials('user', 'bitfinex');
  assert.deepEqual(missingToken, { ok: false, code: 'TOKEN_MISSING', credentials: null });
});

test('credential resolution distinguishes decrypt, JSON, and field failures', async () => {
  const decryptFailure = await resolverFor({ token: 'opaque' }, () => {
    throw new Error('ciphertext and secret must not escape');
  }).resolveUserCredentials('user', 'bitfinex');
  assert.deepEqual(decryptFailure, { ok: false, code: 'DECRYPT_FAILED', credentials: null });

  const jsonFailure = await resolverFor({ token: 'opaque' }, () => 'not-json-secret')
    .resolveUserCredentials('user', 'bitfinex');
  assert.deepEqual(jsonFailure, { ok: false, code: 'JSON_INVALID', credentials: null });

  const fieldsFailure = await resolverFor({ token: 'opaque' }, () => JSON.stringify({ apiKey: 'private-key' }))
    .resolveUserCredentials('user', 'bitfinex');
  assert.deepEqual(fieldsFailure, { ok: false, code: 'FIELDS_MISSING', credentials: null });
  const whitespaceFields = await resolverFor({ token: 'opaque' }, () => JSON.stringify({
    apiKey: '   ', apiSecret: '\t',
  })).resolveUserCredentials('user', 'bitfinex');
  assert.deepEqual(whitespaceFields, { ok: false, code: 'FIELDS_MISSING', credentials: null });
  assert.doesNotMatch(JSON.stringify(fieldsFailure), /private-key|not-json-secret|ciphertext/);
});

test('credential resolution distinguishes stored and configured fingerprint mismatches', async () => {
  const decrypted = JSON.stringify({ apiKey: 'private-key', apiSecret: 'private-secret' });
  const wrongFingerprint = bitfinexCredentialFingerprint({ apiKey: 'other-key', apiSecret: 'other-secret' });

  await withCredentialEnvironment({}, async () => {
    const storedMismatch = await resolverFor({
      token: 'opaque',
      metadata: { accountCredentialFingerprint: wrongFingerprint },
    }, () => decrypted).resolveUserCredentials('user', 'bitfinex');
    assert.deepEqual(storedMismatch, {
      ok: false,
      code: 'STORED_FINGERPRINT_MISMATCH',
      credentials: null,
    });
  });

  await withCredentialEnvironment({ expected: wrongFingerprint }, async () => {
    const configuredMismatch = await resolverFor({ token: 'opaque', metadata: {} }, () => decrypted)
      .resolveUserCredentials('user', 'bitfinex');
    assert.deepEqual(configuredMismatch, {
      ok: false,
      code: 'CONFIGURED_FINGERPRINT_MISMATCH',
      credentials: null,
    });
  });
});

test('credential resolution identifies required missing fingerprint and returns credentials only on OK', async () => {
  const decrypted = JSON.stringify({ apiKey: 'private-key', apiSecret: 'private-secret' });
  await withCredentialEnvironment({ subscriber: 'true' }, async () => {
    const missingFingerprint = await resolverFor({ token: 'opaque', metadata: {} }, () => decrypted)
      .resolveUserCredentials('user', 'bitfinex');
    assert.deepEqual(missingFingerprint, {
      ok: false,
      code: 'FINGERPRINT_REQUIRED_MISSING',
      credentials: null,
    });
  });

  await withCredentialEnvironment({}, async () => {
    const service = resolverFor({ token: 'opaque', metadata: {} }, () => decrypted);
    const resolved = await service.resolveUserCredentials('user', 'bitfinex');
    assert.equal(resolved.ok, true);
    assert.equal(resolved.code, 'OK');
    assert.deepEqual(resolved.credentials, { apiKey: 'private-key', apiSecret: 'private-secret' });
    assert.deepEqual(await service.getUserCredentials('user', 'bitfinex'), resolved.credentials);
  });
});
