import assert from 'node:assert/strict';
import test from 'node:test';
import { TradingAgentInstanceStatus } from '@prisma/client';
import { TradingAgentInstancesService } from './trading-agent-instances.service';

const pausedDash = {
  relayExecutionMode: 'PAUSED', relayArmedAt: null, realTradingConfirmedAt: null,
  immutablePriorAudit: { keep: true },
};

function harness(opts?: {
  status?: TradingAgentInstanceStatus;
  finalStatus?: TradingAgentInstanceStatus;
  snapshots?: unknown[];
}) {
  const calls: string[] = [];
  const status = opts?.status ?? TradingAgentInstanceStatus.PAUSED;
  const instance = {
    id: 'instance-1', userId: 'user-1', agentId: 'agent-1',
    exchangeProvider: 'bitfinex', credentialId: 'old-credential', status,
    dashboardState: pausedDash,
  };
  const snapshots = opts?.snapshots ?? [
    { orders: [], position: null }, { orders: [], position: null },
  ];
  let snapshotIndex = 0;
  let updateData: Record<string, unknown> | null = null;
  const prisma = {
    tradingAgent: { findUnique: async () => ({ id: 'agent-1', slug: 'conservative-btc' }) },
    tradingAgentInstance: {
      findUnique: async () => ({ ...instance }),
      update: async ({ data }: { data: Record<string, unknown> }) => {
        calls.push('instance.update'); updateData = data; return { ...instance, ...data };
      },
    },
    signalCycleParticipant: {
      findMany: async () => { calls.push('participants.read'); return []; },
      count: async () => 0,
    },
    integrationCredential: {
      upsert: async () => { calls.push('credential.commit'); return { id: 'credential-1' }; },
    },
  };
  const tx = {
    ...prisma,
    tradingAgentInstance: {
      ...prisma.tradingAgentInstance,
      findUnique: async () => ({
        ...instance, status: opts?.finalStatus ?? instance.status,
      }),
    },
  };
  (prisma as typeof prisma & { $transaction: unknown }).$transaction = async (
    callback: (client: typeof tx) => Promise<unknown>,
  ) => callback(tx);
  const exchanges = {
    prepareUserExchangeConnection: async () => {
      calls.push('credentials.prepare');
      return {
        provider: 'bitfinex', providerKey: 'exchange:bitfinex',
        encryptedToken: 'encrypted', metadata: {}, message: 'ok',
      };
    },
    readBitfinexCandidateSnapshot: async () => {
      calls.push('exchange.snapshot'); return snapshots[snapshotIndex++] ?? null;
    },
    resolveUserCredentials: async () => ({
      ok: true, code: 'OK', credentials: { apiKey: 'stored', apiSecret: 'stored-secret' },
    }),
    ensureUserBitfinexDerivativesMargin: async () => {
      calls.push('FORBIDDEN_MARGIN_TRANSFER'); throw new Error('must not be called');
    },
  };
  const points = {
    spend: async () => calls.push('FORBIDDEN_CHARGE'),
    award: async () => calls.push('FORBIDDEN_AWARD'),
    creditAdminFee: async () => calls.push('FORBIDDEN_ADMIN_FEE'),
  };
  const relaySim = {
    buildReconcileSnapshot: () => ({
      exchangePositionQty: 0, rawExchangePositionQty: 0, signedExchangePositionQty: 0,
      dustPositionQty: 0, ledgerOpenQty: 0, signedLedgerOpenQty: 0, deltaBtc: 0,
      alert: false, openLots: 0, pendingLots: 0, markPrice: null,
      updatedAt: new Date().toISOString(),
    }),
  };
  const service = new TradingAgentInstancesService(
    prisma as never, points as never, {} as never, exchanges as never,
    relaySim as never, {} as never,
  );
  return { service, calls, getUpdate: () => updateData };
}

const input = { exchangeProvider: 'bitfinex', apiKey: 'key', apiSecret: 'secret' };

test('paused credential refresh publishes flat proof without activation, billing, or transfer', async () => {
  const h = harness();
  const result = await h.service.refreshPausedExchangeCredentials('user-1', 'conservative-btc', input);
  assert.equal(result.status, 'PAUSED');
  assert.equal(result.armed, false);
  assert.equal(result.chargedDdollar, 0);
  assert.equal(result.marginTransferRequested, false);
  assert.equal(result.authenticatedAudit.flat, true);
  assert.deepEqual(h.calls, [
    'credentials.prepare', 'exchange.snapshot', 'participants.read',
    'exchange.snapshot', 'participants.read', 'credential.commit', 'instance.update',
  ]);
  const data = h.getUpdate()!;
  assert.equal(data.credentialId, 'credential-1');
  const dash = data.dashboardState as Record<string, unknown>;
  assert.deepEqual(dash.immutablePriorAudit, { keep: true });
  assert.equal(dash.relayExecutionMode, 'PAUSED');
  assert.equal(dash.relayArmedAt, null);
  assert.equal(dash.realTradingConfirmedAt, null);
});

test('credential refresh rejects a non-paused relay before touching credentials', async () => {
  const h = harness({ status: TradingAgentInstanceStatus.ACTIVE });
  await assert.rejects(
    h.service.refreshPausedExchangeCredentials('user-1', 'conservative-btc', input),
    /paused, disarmed relay/,
  );
  assert.deepEqual(h.calls, []);
});

test('incomplete authenticated audit never publishes or reports flat', async () => {
  const h = harness({ snapshots: [{ orders: [], position: null }, null] });
  await assert.rejects(
    h.service.refreshPausedExchangeCredentials('user-1', 'conservative-btc', input),
    /audit was incomplete/,
  );
  assert.equal(h.calls.includes('instance.update'), false);
  assert.equal(h.calls.some((call) => call.startsWith('FORBIDDEN_')), false);
});

test('state flip before commit writes neither credential nor audit', async () => {
  const h = harness({ finalStatus: TradingAgentInstanceStatus.ACTIVE });
  await assert.rejects(
    h.service.refreshPausedExchangeCredentials('user-1', 'conservative-btc', input),
    /state changed/,
  );
  assert.equal(h.calls.includes('credential.commit'), false);
  assert.equal(h.calls.includes('instance.update'), false);
});

test('active orders invalidate flat proof while preserving paused state', async () => {
  const snapshot = { orders: [{ id: 42 }], position: null };
  const h = harness({ snapshots: [snapshot, snapshot] });
  const result = await h.service.refreshPausedExchangeCredentials('user-1', 'conservative-btc', input);
  assert.equal(result.authenticatedAudit.flat, false);
  const data = h.getUpdate()!;
  assert.match(String(data.lastError), /not flat/);
  const dash = data.dashboardState as Record<string, unknown>;
  assert.equal(dash.copyRelayReconcile, null);
  assert.equal((dash.exchangeOrderAudit as Record<string, unknown>).activeOrderCount, 1);
  assert.equal(h.calls.some((call) => call.startsWith('FORBIDDEN_')), false);
});

test('ops flat-audit refresh uses read-only snapshots and never invokes margin funding', async () => {
  const h = harness();
  const result = await h.service.refreshPausedFlatAudit('user-1', 'conservative-btc');
  assert.equal(result.flat, true);
  assert.equal(result.status, 'PAUSED');
  assert.equal(result.armed, false);
  assert.equal(h.calls.filter((call) => call === 'exchange.snapshot').length, 2);
  assert.equal(h.calls.some((call) => call.startsWith('FORBIDDEN_')), false);
  assert.equal(h.calls.includes('credential.commit'), false);
});
