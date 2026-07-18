import assert from 'node:assert/strict';
import test from 'node:test';
import { ShowcaseRelayEventsService } from './showcase-relay-events.service';

function createService(activeInstance: string | null) {
  const config = { get: () => undefined };
  const botBridge = {
    getCachedDashboardOwnerIdentity: () =>
      activeInstance
        ? { instanceId: activeInstance, pid: 42, port: 7002, seenAt: Date.now() }
        : null,
    invalidateCache: () => undefined,
  };
  const cycles = { wakeFromShowcase: async () => false };
  const execution = { wakeNow: async () => undefined };
  const prisma = {
    tradingAgent: { findUnique: async () => null },
  };
  return new ShowcaseRelayEventsService(
    config as never,
    botBridge as never,
    cycles as never,
    execution as never,
    prisma as never,
  );
}

test('rejects a signed relay-shaped event without an active owner identity', async () => {
  const service = createService(null);
  await assert.rejects(
    service.ingest('conservative-btc', {
      event: 'APPROVE_PENDING',
      trade_id: 'cont-1',
      dashboard_owner: true,
      bot_instance_id: 'dashboard-old',
      dashboard_port: 7002,
    }),
    /Active dashboard owner is not currently confirmed/,
  );
});

test('rejects a stale dashboard instance', async () => {
  const service = createService('dashboard-active');
  await assert.rejects(
    service.ingest('conservative-btc', {
      event: 'ORDER_PLACED',
      trade_id: 'cont-2',
      dashboard_owner: true,
      bot_instance_id: 'dashboard-stale',
      dashboard_port: 7002,
    }),
    /dashboard instance is stale/,
  );
});

test('accepts the currently cached dashboard owner', async () => {
  const service = createService('dashboard-active');
  const result = await service.ingest('conservative-btc', {
    event: 'LIMIT_UPDATED',
    trade_id: 'cont-3',
    dashboard_owner: true,
    bot_instance_id: 'dashboard-active',
    dashboard_port: 7002,
  });
  assert.equal(result.ok, true);
});
