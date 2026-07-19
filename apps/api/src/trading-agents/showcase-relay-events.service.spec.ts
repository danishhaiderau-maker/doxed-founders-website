import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import test from 'node:test';
import { ShowcaseRelayEventsService } from './showcase-relay-events.service';

function createService(activeInstance: string | null, webhookSecret?: string) {
  const config = {
    get: (key: string) => key === 'SHOWCASE_WEBHOOK_SECRET' ? webhookSecret : undefined,
  };
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

test('accepts an unsigned legacy wake from the active owner when HMAC rollout is enabled', async () => {
  const service = createService('dashboard-active', 'test-webhook-secret');
  const result = await service.ingest('conservative-btc', {
    event: 'ORDER_PLACED',
    trade_id: 'cont-legacy',
    dashboard_owner: true,
    bot_instance_id: 'dashboard-active',
    dashboard_port: 7002,
  });
  assert.equal(result.ok, true);
});

test('rejects an unsigned intent-bearing payload when HMAC rollout is enabled', async () => {
  const service = createService('dashboard-active', 'test-webhook-secret');
  const body = {
    schema: 'dcf-showcase-intent-v1',
    event: 'APPROVE_PENDING' as const,
    trade_id: 'cont-intent',
    signal_price: 64_000,
    margin_usdt: 20,
    dashboard_owner: true,
    bot_instance_id: 'dashboard-active',
    dashboard_port: 7002,
  };
  await assert.rejects(
    service.ingest('conservative-btc', body, {
      rawBody: Buffer.from(JSON.stringify(body)),
    }),
    /Missing showcase signature/,
  );
});

test('rejects an unsigned legacy-shaped payload with an unknown future field', async () => {
  const service = createService('dashboard-active', 'test-webhook-secret');
  const body = {
    event: 'LIMIT_UPDATED' as const,
    trade_id: 'cont-unknown',
    dashboard_owner: true,
    bot_instance_id: 'dashboard-active',
    dashboard_port: 7002,
    future_entry_override: 64_000,
  };
  await assert.rejects(
    service.ingest('conservative-btc', body, {
      rawBody: Buffer.from(JSON.stringify(body)),
    }),
    /Missing showcase signature/,
  );
});

test('accepts a correctly signed intent-bearing payload', async () => {
  const secret = 'test-webhook-secret';
  const service = createService('dashboard-active', secret);
  const body = {
    schema: 'dcf-showcase-intent-v1',
    event: 'APPROVE_PENDING' as const,
    trade_id: 'cont-signed',
    signal_price: 64_000,
    margin_usdt: 20,
    dashboard_owner: true,
    bot_instance_id: 'dashboard-active',
    dashboard_port: 7002,
  };
  const rawBody = Buffer.from(JSON.stringify(body));
  const signature = `sha256=${createHmac('sha256', secret).update(rawBody).digest('hex')}`;
  const result = await service.ingest('conservative-btc', body, {
    rawBody,
    signatureHeader: signature,
  });
  assert.equal(result.ok, true);
});
