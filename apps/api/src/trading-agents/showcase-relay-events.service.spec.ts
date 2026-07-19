import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import test from 'node:test';
import { ShowcaseRelayEventsService } from './showcase-relay-events.service';

function createService(
  activeInstance: string | null,
  webhookSecret?: string,
  overrides?: {
    trace?: string[];
    cycles?: Record<string, unknown>;
    execution?: Record<string, unknown>;
    prisma?: Record<string, unknown>;
  },
) {
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
  const cycles = overrides?.cycles ?? {
    wakeFromShowcase: async () => {
      overrides?.trace?.push('cycles');
      return false;
    },
  };
  const execution = overrides?.execution ?? {
    wakeNow: async () => {
      overrides?.trace?.push('execution');
    },
  };
  const prisma = overrides?.prisma ?? {
    tradingAgent: {
      findUnique: async () => {
        overrides?.trace?.push('persist');
        return null;
      },
    },
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

test('persists and enriches before waking subscriber execution', async () => {
  const trace: string[] = [];
  const service = createService('dashboard-active', undefined, { trace });
  await service.ingest('conservative-btc', {
    event: 'LIMIT_UPDATED',
    trade_id: 'cont-ordering',
    dashboard_owner: true,
    bot_instance_id: 'dashboard-active',
    dashboard_port: 7002,
  });
  assert.deepEqual(trace, ['cycles', 'persist', 'execution']);
});

test('showcase ORDER_PLACED remains an audit event and does not pre-claim the subscriber cycle', async () => {
  const updates: unknown[] = [];
  const prisma = {
    tradingAgent: { findUnique: async () => ({ id: 'agent-1' }) },
    signalCycle: {
      findUnique: async (args: { where: Record<string, unknown> }) => {
        if ('agentId_tradeId' in args.where) {
          return {
            id: 'cycle-1',
            intentEnvelope: { schema: 'dcf-signal-intent/v1', action: 'ENTER' },
          };
        }
        return null;
      },
      update: async (args: unknown) => {
        updates.push(args);
        return {};
      },
    },
    signalCycleEvent: {
      findFirst: async () => null,
      create: async () => ({}),
    },
  };
  const service = createService('dashboard-active', undefined, { prisma });
  const result = await service.ingest('conservative-btc', {
    event: 'ORDER_PLACED',
    trade_id: 'cont-no-preclaim',
    direction: 'LONG',
    dashboard_owner: true,
    bot_instance_id: 'dashboard-active',
    dashboard_port: 7002,
  });
  assert.equal(result.persisted, true);
  assert.equal(updates.length, 0);
});

test('signed intent enriches a legacy audit cycle before subscriber execution wakes', async () => {
  const secret = 'test-webhook-secret';
  let storedEnvelope: Record<string, unknown> = {
    schema: 'showcase_relay_audit_v1',
  };
  const trace: string[] = [];
  const prisma = {
    tradingAgent: { findUnique: async () => ({ id: 'agent-1' }) },
    signalCycle: {
      findUnique: async (args: { where: Record<string, unknown> }) => {
        if ('agentId_tradeId' in args.where) {
          return { id: 'cycle-existing', intentEnvelope: storedEnvelope };
        }
        return null;
      },
      update: async (args: { data: { intentEnvelope?: Record<string, unknown> } }) => {
        if (args.data.intentEnvelope) {
          storedEnvelope = args.data.intentEnvelope;
          trace.push('enrich');
        }
        return {};
      },
    },
    signalCycleEvent: {
      findFirst: async () => ({ id: 'event-existing' }),
      create: async () => ({}),
    },
  };
  const execution = {
    wakeNow: async () => {
      trace.push('execution');
      assert.equal(storedEnvelope.action, 'ENTER');
      assert.equal(storedEnvelope.schema, 'dcf-signal-intent/v1');
    },
  };
  const service = createService('dashboard-active', secret, { prisma, execution });
  const body = {
    schema: 'dcf-showcase-intent-v1',
    event: 'APPROVE_PENDING' as const,
    trade_id: 'cont-enrich',
    direction: 'LONG',
    signal_price: 64_000,
    pullback_pct: 0.001,
    margin_usdt: 20,
    bot_version: 'v12-test',
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
  assert.deepEqual(trace, ['enrich', 'execution']);
  assert.equal((storedEnvelope.entry as { offset_pct: number }).offset_pct, -0.1);
  assert.equal((storedEnvelope.risk as { leverage_hint: number }).leverage_hint, 100);
});
