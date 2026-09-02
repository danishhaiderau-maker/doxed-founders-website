import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import {
  buildOwnerHttpsRequestOptions,
  describeOwnerFetchError,
  hasFullOwnerOrderState,
  isCompleteStoredExchangeOrderAuditFlat,
  isCompleteStoredRawFlatReconcileSnapshot,
  isNeverArmedUncredentialedRelay,
  isStrictExchangeOrderAuditFlat,
  isStrictRawFlatReconcileSnapshot,
  isRelayPausedAndDisarmed,
  isRetryablePrismaConnectionError,
  ownerFetchErrorChain,
  refreshPausedRelayAudit,
} from './check-relay-flat.mjs';

const inertUncredentialedRelay = {
  credentialConfigured: false,
  instanceCredentialId: null,
  providerCredentialPresent: false,
  providerCredentialUpdatedAt: null,
  providerCredentialReadStable: true,
  liveDeskSessionStartedAt: null,
  status: 'PAUSED',
  relayExecutionMode: 'PAUSED',
  relayArmedAt: null,
  realTradingConfirmedAt: null,
  activeParticipants: 0,
  orphanOrderIds: [],
  orphanPositionIds: [],
  reconcile: null,
  exchangeOrderAudit: null,
};

test('durable recovery waives absent audits only for a never-armed uncredentialed relay', () => {
  assert.equal(isNeverArmedUncredentialedRelay(inertUncredentialedRelay, true), true);
  assert.equal(isNeverArmedUncredentialedRelay(inertUncredentialedRelay, false), false);
  assert.equal(isNeverArmedUncredentialedRelay({
    ...inertUncredentialedRelay,
    liveDeskSessionStartedAt: '2026-08-01T00:00:00.000Z',
  }, true), false);
  assert.equal(isNeverArmedUncredentialedRelay({
    ...inertUncredentialedRelay,
    instanceCredentialId: 'credential-link',
  }, true), false);
  assert.equal(isNeverArmedUncredentialedRelay({
    ...inertUncredentialedRelay,
    reconcile: { rawExchangePositionQty: 1 },
  }, true), false);
  assert.equal(isNeverArmedUncredentialedRelay({
    ...inertUncredentialedRelay,
    orphanOrderIds: null,
  }, true), false);
});

test('durable recovery accepts a stable provider row only after an exact newer missing-credential receipt', () => {
  const staleProviderRow = {
    ...inertUncredentialedRelay,
    credentialConfigured: true,
    providerCredentialPresent: true,
    providerCredentialUpdatedAt: '2026-09-03T01:00:00.000Z',
    lastError: 'Exchange credentials missing — re-hire with API keys',
    liveFidelityGuard: {
      status: 'IDLE',
      lastResetReason: 'EXCHANGE_CREDENTIALS_MISSING',
      lastObservedAt: '2026-09-03T01:00:01.000Z',
    },
  };
  assert.equal(isNeverArmedUncredentialedRelay(staleProviderRow, true), true);
  assert.equal(isNeverArmedUncredentialedRelay({
    ...staleProviderRow,
    providerCredentialReadStable: false,
  }, true), false);
  assert.equal(isNeverArmedUncredentialedRelay({
    ...staleProviderRow,
    liveFidelityGuard: { ...staleProviderRow.liveFidelityGuard, lastObservedAt: '2026-09-03T00:59:59.000Z' },
  }, true), false);
  assert.equal(isNeverArmedUncredentialedRelay({
    ...staleProviderRow,
    lastError: 'different error',
  }, true), false);
  assert.equal(isNeverArmedUncredentialedRelay({
    ...staleProviderRow,
    liveFidelityGuard: { ...staleProviderRow.liveFidelityGuard, lastResetReason: 'LIVE_RELAY_INACTIVE' },
  }, true), false);
  assert.equal(isNeverArmedUncredentialedRelay({
    ...staleProviderRow,
    reconcile: {},
  }, true), false);
});

test('native HTTPS fallback preserves auth and pins the canonical proof to IPv4', () => {
  const options = buildOwnerHttpsRequestOptions(
    'https://doxed-btc-bot.fly.dev/api/relay-execution-state?fresh=1',
    'redacted-test-token',
    15_000,
  );
  assert.equal(options.protocol, 'https:');
  assert.equal(options.hostname, 'doxed-btc-bot.fly.dev');
  assert.equal(options.port, 443);
  assert.equal(options.path, '/api/relay-execution-state?fresh=1');
  assert.equal(options.family, 4);
  assert.equal(options.timeout, 15_000);
  assert.equal(options.headers['X-Bot-Admin-Token'], 'redacted-test-token');
});

test('Neon proof retries only transient connection failures', () => {
  assert.equal(
    isRetryablePrismaConnectionError(new Error("Can't reach database server at host:5432")),
    true,
  );
  assert.equal(
    isRetryablePrismaConnectionError(new Error('P1017: Server has closed the connection')),
    true,
  );
  assert.equal(
    isRetryablePrismaConnectionError(new Error('conservative-btc agent missing')),
    false,
  );
});

test('paused relay accepts legacy null mode only when arming timestamps are clear', () => {
  assert.equal(isRelayPausedAndDisarmed({
    status: 'PAUSED', relayExecutionMode: null, relayArmedAt: null, realTradingConfirmedAt: null,
  }), true);
  assert.equal(isRelayPausedAndDisarmed({
    status: 'PAUSED', relayExecutionMode: 'LIVE', relayArmedAt: null, realTradingConfirmedAt: null,
  }), false);
  assert.equal(isRelayPausedAndDisarmed({
    status: 'PAUSED', relayExecutionMode: null, relayArmedAt: '2026-08-09T00:00:00Z', realTradingConfirmedAt: null,
  }), false);
});

const now = Date.parse('2026-07-24T05:45:00.000Z');

function rawFlat(overrides = {}) {
  return {
    rawExchangePositionQty: 0,
    dustPositionQty: 0,
    signedExchangePositionQty: 0,
    ledgerOpenQty: 0,
    signedLedgerOpenQty: 0,
    deltaBtc: 0,
    openLots: 0,
    pendingLots: 0,
    updatedAt: '2026-07-24T05:44:50.000Z',
    ...overrides,
  };
}

test('strict flat proof accepts a fresh complete raw-zero snapshot', () => {
  assert.equal(isStrictRawFlatReconcileSnapshot(rawFlat(), now), true);
});

test('strict flat proof rejects legacy effective-zero snapshots', () => {
  const legacy = rawFlat();
  delete legacy.rawExchangePositionQty;
  delete legacy.dustPositionQty;
  delete legacy.signedExchangePositionQty;
  delete legacy.signedLedgerOpenQty;
  legacy.exchangePositionQty = 0;
  assert.equal(isStrictRawFlatReconcileSnapshot(legacy, now), false);
});

test('strict flat proof rejects one satoshi, dust, and stale observations', () => {
  assert.equal(
    isStrictRawFlatReconcileSnapshot(
      rawFlat({ rawExchangePositionQty: 0.00000001, signedExchangePositionQty: -0.00000001 }),
      now,
    ),
    false,
  );
  assert.equal(
    isStrictRawFlatReconcileSnapshot(rawFlat({ dustPositionQty: 0.00003999 }), now),
    false,
  );
  assert.equal(
    isStrictRawFlatReconcileSnapshot(
      rawFlat({ updatedAt: '2026-07-24T05:43:59.999Z' }),
      now,
    ),
    false,
  );
  assert.equal(
    isStrictRawFlatReconcileSnapshot(rawFlat({ rawExchangePositionQty: null }), now),
    false,
  );
});

test('durable recovery proof accepts complete stored zeros but rejects nonzero or incomplete evidence', () => {
  assert.equal(isCompleteStoredRawFlatReconcileSnapshot(
    rawFlat({ updatedAt: '2025-01-01T00:00:00.000Z' }),
  ), true);
  assert.equal(isCompleteStoredRawFlatReconcileSnapshot(rawFlat({ pendingLots: 1 })), false);
  const incomplete = rawFlat();
  delete incomplete.signedLedgerOpenQty;
  assert.equal(isCompleteStoredRawFlatReconcileSnapshot(incomplete), false);
});

test('authenticated source proof rejects a sanitized state without an order book', () => {
  assert.equal(
    hasFullOwnerOrderState({ dashboard_owner: true, positions: [] }),
    false,
  );
  assert.equal(
    hasFullOwnerOrderState({ dashboard_owner: true, pending_orders: [] }),
    true,
  );
  assert.equal(
    hasFullOwnerOrderState({ dashboard_owner: true, orders: [] }),
    true,
  );
});

test('owner timeout explains Fly routing and health-check failure', () => {
  const error = new Error('The operation was aborted due to timeout');
  error.name = 'TimeoutError';
  const described = describeOwnerFetchError(
    error,
    'https://doxed-btc-bot.fly.dev/api/relay-execution-state',
    15_000,
  );
  assert.match(described.message, /timed out after 15000ms/);
  assert.match(described.message, /doxed-btc-bot\.fly\.dev\/api\/relay-execution-state/);
  assert.match(described.message, /critical service check removed public routing/);
});

test('owner non-timeout failure retains URL and original diagnosis', () => {
  const error = new Error('showcase HTTP 503');
  const described = describeOwnerFetchError(
    error,
    'https://doxed-btc-bot.fly.dev/api/relay-execution-state',
    15_000,
  );
  assert.match(described.message, /request failed/);
  assert.match(described.message, /showcase HTTP 503/);
  assert.match(described.message, /check Fly \/health/);
});

test('owner fetch diagnosis includes nested undici socket cause and attempts', () => {
  const socket = new Error('other side closed');
  socket.name = 'SocketError';
  socket.code = 'UND_ERR_SOCKET';
  const outer = new TypeError('fetch failed', { cause: socket });
  assert.match(ownerFetchErrorChain(outer), /UND_ERR_SOCKET/);
  const described = describeOwnerFetchError(
    outer,
    'https://doxed-btc-bot.fly.dev/api/relay-execution-state',
    15_000,
    3,
  );
  assert.match(described.message, /after 3 attempts/);
  assert.match(described.message, /UND_ERR_SOCKET/);
  assert.match(described.message, /route\/socket reset/);
});

test('strict exchange order proof requires a fresh known zero-order snapshot', () => {
  const flatAudit = {
    known: true,
    activeOrderCount: 0,
    managedActiveOrderCount: 0,
    foreignActiveOrderCount: 0,
    checkedAt: '2026-07-24T05:44:50.000Z',
  };
  assert.equal(isStrictExchangeOrderAuditFlat(flatAudit, now), true);
  assert.equal(
    isStrictExchangeOrderAuditFlat(
      { ...flatAudit, activeOrderCount: 1 },
      now,
    ),
    false,
  );
  assert.equal(
    isStrictExchangeOrderAuditFlat(
      { ...flatAudit, known: false },
      now,
    ),
    false,
  );
  assert.equal(
    isStrictExchangeOrderAuditFlat(
      { ...flatAudit, checkedAt: '2026-07-24T05:43:59.999Z' },
      now,
    ),
    false,
  );
});

test('durable recovery order proof accepts stored known zeros but rejects unknown or nonzero state', () => {
  const stored = {
    known: true,
    activeOrderCount: 0,
    managedActiveOrderCount: 0,
    foreignActiveOrderCount: 0,
    checkedAt: '2025-01-01T00:00:00.000Z',
  };
  assert.equal(isCompleteStoredExchangeOrderAuditFlat(stored), true);
  assert.equal(isCompleteStoredExchangeOrderAuditFlat({ ...stored, known: false }), false);
  assert.equal(isCompleteStoredExchangeOrderAuditFlat({ ...stored, foreignActiveOrderCount: 1 }), false);
});

test('strict proof refresh uses only the authenticated paused wake', async () => {
  const calls = [];
  await refreshPausedRelayAudit(
    'https://api.example.test/',
    'redacted-admin-secret',
    'private-user-id',
    async (url, options) => {
      calls.push({ url, options });
      return { ok: true, status: 202 };
    },
  );
  assert.equal(calls.length, 1);
  assert.equal(
    calls[0].url,
    'https://api.example.test/trading-agents/conservative-btc/ops/refresh-flat-audit',
  );
  assert.equal(calls[0].options.method, 'POST');
  assert.equal(calls[0].options.headers['x-bot-admin-token'], 'redacted-admin-secret');
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    userId: 'private-user-id', confirmation: 'REFRESH_PAUSED_FLAT_AUDIT',
  });
});

test('strict proof refresh fails closed without HTTPS auth or acknowledgement', async () => {
  await assert.rejects(refreshPausedRelayAudit('', 'secret', 'user'), /requires authenticated/);
  await assert.rejects(
    refreshPausedRelayAudit('http://api.internal', 'secret', 'user'),
    /requires an HTTPS platform API URL/,
  );
  await assert.rejects(
    refreshPausedRelayAudit(
      'https://api.example.test', 'secret', 'user', async () => ({ ok: false, status: 401 }),
    ),
    /refresh failed HTTP 401/,
  );
});

test('guarded deploy uses the public API and no unreachable private executor secrets', () => {
  const workflow = fs.readFileSync(
    new URL('../.github/workflows/fly-bot-deploy.yml', import.meta.url),
    'utf8',
  );
  const strictStep = workflow.slice(
    workflow.indexOf('Prove the current Fly owner and every relay account are flat'),
    workflow.indexOf('Prove exact unready Fly revision'),
  );
  assert.match(strictStep, /PLATFORM_API_URL: "https:\/\/doxed-founders-website-production\.up\.railway\.app\/api"/);
  assert.doesNotMatch(strictStep, /RELAY_EXECUTOR_WAKE_URL|BOT_CONTROL_SECRET/);
});
