import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import test from 'node:test';
import {
  ShowcaseRelayEventsService,
  exactLifecycleRevisionMatches,
  relayIntentEnvelope,
  shouldApplyExactLifecycleUpdate,
} from './showcase-relay-events.service';

test('durable receipt matches the exact canonical event id, sequence, and limit', () => {
  const current = {
    action: 'ENTER',
    trade_id: 'cont-race',
    entry: { exact_limit_price: 63_167 },
    context: {
      showcase_event: 'LIMIT_UPDATED',
      showcase_event_id: 'revision-a',
      showcase_event_seq: 4,
    },
  };
  assert.equal(
    exactLifecycleRevisionMatches(current, {
      event: 'LIMIT_UPDATED',
      trade_id: 'cont-race',
      event_id: 'revision-a',
      event_seq: 4,
      limit_price: 63_167,
    }),
    true,
  );
  assert.equal(
    exactLifecycleRevisionMatches(current, {
      event: 'LIMIT_UPDATED',
      trade_id: 'cont-race',
      event_id: 'revision-b',
      event_seq: 4,
      limit_price: 63_166,
    }),
    false,
  );
});

test('terminal fallback marker survives only on its signed exact-limit revision', () => {
  const base = {
    schema: 'dcf-showcase-intent-v1',
    event: 'LIMIT_UPDATED' as const,
    trade_id: 'cont-settle',
    ts: '2026-08-02T13:05:00.000Z',
    event_seq: 4,
    platform_received_at: '2026-08-02T13:05:00.500Z',
    direction: 'SHORT',
    executable: true,
    entry_limit_policy: 'micro_sr_structural_limit_v1',
    limit_price: 63_167,
  };
  const terminal = relayIntentEnvelope('cycle-1', 'cont-settle', {
    ...base,
    marketable_fallback: true,
    relay_settle_not_before_ts: '2026-08-02T13:05:15.000Z',
  }) as { context?: Record<string, unknown> };
  assert.equal(terminal.context?.marketable_fallback, true);
  assert.equal(
    terminal.context?.relay_settle_not_before_ts,
    '2026-08-02T13:05:15.000Z',
  );

  const ordinary = relayIntentEnvelope('cycle-1', 'cont-settle', {
    ...base,
    event_seq: 5,
    ts: '2026-08-02T13:05:20.000Z',
  }) as { context?: Record<string, unknown> };
  assert.equal(ordinary.context?.marketable_fallback, false);
  assert.equal(ordinary.context?.relay_settle_not_before_ts, null);
});

test('exact-limit lifecycle revisions are monotonic and cannot regress after close', () => {
  const current = {
    action: 'ENTER',
    context: {
      showcase_event: 'LIMIT_UPDATED',
      showcase_event_at: '2026-07-30T01:00:04.000Z',
      showcase_event_id: 'cont-1:LIMIT_UPDATED:4',
      showcase_event_seq: 4,
    },
  };
  assert.equal(
    shouldApplyExactLifecycleUpdate(current, {
      event: 'LIMIT_UPDATED',
      trade_id: 'cont-00000001',
      ts: '2026-07-30T01:00:03.000Z',
      event_seq: 3,
    }),
    false,
  );
  assert.equal(
    shouldApplyExactLifecycleUpdate(current, {
      event: 'LIMIT_UPDATED',
      trade_id: 'cont-1',
      ts: '2026-07-30T01:00:05.000Z',
      event_seq: 5,
    }),
    true,
  );
  assert.equal(
    shouldApplyExactLifecycleUpdate(
      {
        action: 'ENTER',
        context: { showcase_event: 'POSITION_CLOSED' },
      },
      {
        event: 'LIMIT_UPDATED',
        trade_id: 'cont-1',
        ts: '2026-07-30T01:00:06.000Z',
        event_seq: 6,
      },
    ),
    false,
  );
});

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
  const prismaBase = overrides?.prisma ?? {
    tradingAgent: {
      findUnique: async () => {
        overrides?.trace?.push('persist');
        return null;
      },
    },
  };
  const transactionClient = {
    ...prismaBase,
    $queryRaw: async () => [],
    $executeRaw: async () => 0,
  };
  const prisma = {
    ...prismaBase,
    $transaction:
      (prismaBase as {
        $transaction?: <T>(
          operation: (tx: typeof transactionClient) => Promise<T>,
        ) => Promise<T>;
      }).$transaction
      ?? (async <T>(operation: (tx: typeof transactionClient) => Promise<T>) =>
        operation(transactionClient)),
  };
  return new ShowcaseRelayEventsService(
    config as never,
    botBridge as never,
    cycles as never,
    execution as never,
    prisma as never,
  );
}

test('concurrent relay revisions stay monotonic across API replicas', async () => {
  type StoredEnvelope = {
    action: string;
    direction: string;
    entry: Record<string, unknown>;
    context: Record<string, unknown>;
  };
  let storedEnvelope: StoredEnvelope = {
    action: 'ENTER',
    direction: 'LONG',
    entry: {
      mode: 'EXACT_LIMIT',
      reference: 'SHOWCASE_EXACT_LIMIT',
      exact_limit_price: 63_940,
    },
    context: {
      showcase_event: 'LIMIT_UPDATED',
      showcase_event_id: 'cont-race:LIMIT_UPDATED:4',
      showcase_event_seq: 4,
      showcase_event_at: '2026-07-30T01:00:04.000Z',
    },
  };
  const cloneEnvelope = () =>
    JSON.parse(JSON.stringify(storedEnvelope)) as StoredEnvelope;

  // This mutex models PostgreSQL's transaction-scoped advisory lock. Each
  // service instance has its own in-process queue, so only the DB lock can
  // serialize these two simulated Railway replicas.
  let dbTail = Promise.resolve();
  let advisoryCalls = 0;
  const advisoryKeys: unknown[] = [];
  const prisma = {
    tradingAgent: {
      findUnique: async () => ({ id: 'agent-race' }),
    },
    signalCycle: {
      findUnique: async () => ({
        id: 'cycle-race',
        status: 'INTENT',
        intentEnvelope: cloneEnvelope(),
      }),
      update: async (args: {
        data: { intentEnvelope?: StoredEnvelope };
      }) => {
        const incoming = args.data.intentEnvelope;
        if (incoming) {
          const seq = Number(incoming.context?.showcase_event_seq);
          await new Promise<void>((resolve) =>
            setTimeout(resolve, seq === 6 ? 5 : 30),
          );
          storedEnvelope = JSON.parse(JSON.stringify(incoming)) as StoredEnvelope;
        }
        return {};
      },
    },
    signalCycleEvent: {
      findFirst: async () => null,
      create: async () => ({}),
    },
    $transaction: async <T>(
      operation: (tx: Record<string, unknown>) => Promise<T>,
    ) => {
      const previous = dbTail;
      let release!: () => void;
      const gate = new Promise<void>((resolve) => {
        release = resolve;
      });
      dbTail = previous.then(() => gate);
      let acquired = false;
      const tx = {
        ...prisma,
        $queryRaw: async (
          _queryParts: TemplateStringsArray,
          ...values: unknown[]
        ) => {
          advisoryCalls += 1;
          advisoryKeys.push(values[0]);
          await previous;
          acquired = true;
          return [];
        },
        $executeRaw: async (
          _queryParts: TemplateStringsArray,
          ...values: unknown[]
        ) => {
          // Mirror $queryRaw accounting: the advisory lock is now acquired
          // via $executeRaw (Prisma 6.x throws P2010 on void columns when
          // read back through $queryRaw). The lock key is still the first
          // interpolated value.
          advisoryCalls += 1;
          advisoryKeys.push(values[0]);
          await previous;
          acquired = true;
          return 0;
        },
      };
      try {
        return await operation(tx);
      } finally {
        if (acquired) release();
      }
    },
  };
  const replicaA = createService('dashboard-active', undefined, { prisma });
  const replicaB = createService('dashboard-active', undefined, { prisma });
  const persistA = replicaA as unknown as {
    persistRelayEvent: (
      slug: string,
      body: Record<string, unknown>,
    ) => Promise<boolean>;
  };
  const persistB = replicaB as unknown as typeof persistA;
  const revision = (seq: number, price: number) => ({
    schema: 'dcf-showcase-intent-v1',
    event: 'LIMIT_UPDATED',
    event_id: `cont-race:LIMIT_UPDATED:${seq}`,
    event_seq: seq,
    trade_id: 'cont-race',
    ts: `2026-07-30T01:00:0${seq}.000Z`,
    direction: 'LONG',
    limit_price: price,
    entry_limit_policy: 'micro_sr_structural_limit_v1',
    entry_reason: 'LOCAL_SUPPORT_LIMIT',
    executable: true,
    platform_received_at: `2026-07-30T01:00:0${seq}.100Z`,
  });

  await Promise.all([
    persistA.persistRelayEvent(
      'conservative-btc',
      revision(6, 63_960),
    ),
    persistB.persistRelayEvent(
      'conservative-btc',
      revision(5, 63_950),
    ),
  ]);

  assert.equal(storedEnvelope.context.showcase_event_seq, 6);
  assert.equal(storedEnvelope.entry.exact_limit_price, 63_960);
  assert.equal(advisoryCalls, 2);
  assert.deepEqual(advisoryKeys, [
    'agent-race:cont-race',
    'agent-race:cont-race',
  ]);
});

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
      trade_id: 'cont-00000002',
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
    trade_id: 'cont-00000003',
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
    trade_id: 'cont-1e9ac000',
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
    trade_id: 'cont-1a7e1700',
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
    trade_id: 'cont-a0c0de00',
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
    trade_id: 'cont-51a0ed00',
    direction: 'LONG',
    signal_price: 64_000,
    limit_price: 63_915,
    entry_limit_policy: 'micro_sr_structural_limit_v1',
    entry_reason: 'LOCAL_SUPPORT_LIMIT',
    executable: false,
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
  assert.equal(result.intentCreated, false);
});

test('rejects a signed Type B lifecycle before persistence or execution wake', async () => {
  const secret = 'test-webhook-secret';
  const trace: string[] = [];
  const service = createService('dashboard-active', secret, { trace });
  const body = {
    schema: 'dcf-showcase-intent-v1',
    event: 'ORDER_PLACED' as const,
    trade_id: 'tbhv1-paper-only',
    research_lane: 'TYPE_B_HUNTER_V1',
    direction: 'SHORT',
    limit_price: 64_100,
    entry_limit_policy: 'micro_sr_structural_limit_v1',
    entry_reason: 'LOCAL_RESISTANCE_LIMIT',
    executable: true,
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

  assert.equal(result.ok, false);
  assert.equal(result.reason, 'non_mirrorable_lane');
  assert.equal(result.persisted, false);
  assert.equal(result.intentCreated, false);
  assert.deepEqual(trace, []);
});

test('rejects signed Type B chase and close events before persistence or execution wake', async () => {
  const secret = 'test-webhook-secret';
  for (const lifecycle of [
    {
      event: 'LIMIT_UPDATED' as const,
      event_seq: 3,
      limit_price: 64_075,
      entry_limit_policy: 'micro_sr_structural_limit_v1',
      executable: true,
    },
    {
      event: 'POSITION_CLOSED' as const,
      exit_price: 63_980,
    },
  ]) {
    const trace: string[] = [];
    const service = createService('dashboard-active', secret, { trace });
    const body = {
      schema: 'dcf-showcase-intent-v1',
      trade_id: 'tbhv1-paper-lifecycle',
      research_lane: 'TYPE_B_HUNTER_V1',
      direction: 'SHORT',
      dashboard_owner: true,
      bot_instance_id: 'dashboard-active',
      dashboard_port: 7002,
      ...lifecycle,
    };
    const rawBody = Buffer.from(JSON.stringify(body));
    const signature =
      `sha256=${createHmac('sha256', secret).update(rawBody).digest('hex')}`;

    const result = await service.ingest('conservative-btc', body, {
      rawBody,
      signatureHeader: signature,
    });

    assert.equal(result.ok, false);
    assert.equal(result.reason, 'non_mirrorable_lane');
    assert.equal(result.persisted, false);
    assert.equal(result.intentCreated, false);
    assert.deepEqual(trace, []);
  }
});

test('rejects signed ORDER_PLACED without executable structural exact-limit contract', async () => {
  const secret = 'test-webhook-secret';
  const service = createService('dashboard-active', secret);
  const body = {
    schema: 'dcf-showcase-intent-v1',
    event: 'ORDER_PLACED' as const,
    trade_id: 'cont-1aeac700',
    direction: 'LONG',
    limit_price: 63_915,
    dashboard_owner: true,
    bot_instance_id: 'dashboard-active',
    dashboard_port: 7002,
  };
  const rawBody = Buffer.from(JSON.stringify(body));
  const signature = `sha256=${createHmac('sha256', secret).update(rawBody).digest('hex')}`;
  await assert.rejects(
    service.ingest('conservative-btc', body, {
      rawBody,
      signatureHeader: signature,
    }),
    /requires exact executable limit policy/,
  );
});

test('persists and enriches before waking subscriber execution', async () => {
  const trace: string[] = [];
  const service = createService('dashboard-active', undefined, { trace });
  await service.ingest('conservative-btc', {
    event: 'LIMIT_UPDATED',
    trade_id: 'cont-0ade7100',
    dashboard_owner: true,
    bot_instance_id: 'dashboard-active',
    dashboard_port: 7002,
  });
  await new Promise<void>((resolve) => setImmediate(resolve));
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
    trade_id: 'cont-0a0c1a1a',
    direction: 'LONG',
    dashboard_owner: true,
    bot_instance_id: 'dashboard-active',
    dashboard_port: 7002,
  });
  assert.equal(result.persisted, true);
  assert.equal(updates.length, 0);
});

test('signed APPROVE_PENDING stays visibility-only and does not wake subscriber execution', async () => {
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
    },
  };
  const service = createService('dashboard-active', secret, { prisma, execution });
  const body = {
    schema: 'dcf-showcase-intent-v1',
    event: 'APPROVE_PENDING' as const,
    trade_id: 'cont-ea71c400',
    direction: 'LONG',
    signal_price: 64_000,
    limit_price: 63_915,
    entry_limit_policy: 'micro_sr_structural_limit_v1',
    entry_reason: 'LOCAL_SUPPORT_LIMIT',
    executable: false,
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
  assert.equal(result.intentCreated, false);
  assert.deepEqual(trace, ['enrich']);
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.deepEqual(trace, ['enrich']);
  assert.equal(storedEnvelope.action, undefined);
  assert.equal(storedEnvelope.schema, 'showcase_relay_audit_v2');
});

test('signed ORDER_PLACED persists the exact limit before non-blocking execution wake', async () => {
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
          trace.push('persist');
        }
        return {};
      },
    },
    signalCycleEvent: {
      findFirst: async () => null,
      create: async () => ({}),
    },
  };
  const cycles = {
    wakeFromShowcase: async () => {
      trace.push('canonical');
      return false;
    },
  };
  const execution = {
    wakeNow: async () => {
      trace.push('execution');
      const entry = storedEnvelope.entry as {
        exact_limit_price?: number;
        mode?: string;
        reference?: string;
        offset_pct?: number;
      };
      assert.equal(entry.exact_limit_price, 64_555.25);
      assert.equal(entry.mode, 'EXACT_LIMIT');
      assert.equal(entry.reference, 'SHOWCASE_EXACT_LIMIT');
      assert.equal(entry.offset_pct, 0);
      const context = storedEnvelope.context as {
        signed_showcase_event?: boolean;
        platform_received_at?: string;
      };
      assert.equal(context.signed_showcase_event, true);
      assert.equal(Number.isFinite(Date.parse(context.platform_received_at ?? '')), true);
    },
  };
  const service = createService('dashboard-active', secret, {
    prisma,
    cycles,
    execution,
  });
  const body = {
    schema: 'dcf-showcase-intent-v1',
    event: 'ORDER_PLACED' as const,
    trade_id: 'cont-eaac7111',
    direction: 'SHORT',
    signal_price: 64_540,
    limit_price: 64_555.25,
    event_id: 'cont-fast:ORDER_PLACED:3',
    event_seq: 3,
    entry_limit_policy: 'micro_sr_structural_limit_v1',
    entry_reason: 'LOCAL_RESISTANCE_LIMIT',
    executable: true,
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
  assert.equal(result.persisted, true);
  assert.deepEqual(trace, ['persist']);
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.deepEqual(trace, ['persist', 'execution', 'canonical']);
});

test('signed POSITION_CLOSED durably carries exit evidence into the immediate wake', async () => {
  const secret = 'test-webhook-secret';
  let storedEnvelope: Record<string, unknown> = {
    schema: 'dcf-signal-intent/v1',
    action: 'ENTER',
    direction: 'LONG',
    entry: { exact_limit_price: 64_500 },
    context: {},
  };
  const trace: string[] = [];
  const prisma = {
    tradingAgent: { findUnique: async () => ({ id: 'agent-1' }) },
    signalCycle: {
      findUnique: async (args: { where: Record<string, unknown> }) => {
        if ('agentId_tradeId' in args.where) {
          return {
            id: 'cycle-existing',
            status: 'OPEN',
            intentEnvelope: storedEnvelope,
          };
        }
        return { id: 'cycle-existing', status: 'OPEN' };
      },
      update: async (args: {
        data: {
          intentEnvelope?: Record<string, unknown>;
          status?: string;
        };
      }) => {
        if (args.data.intentEnvelope) {
          storedEnvelope = args.data.intentEnvelope;
          trace.push('persist');
        }
        if (args.data.status === 'CLOSED') trace.push('closed');
        return {};
      },
    },
    signalCycleEvent: {
      findFirst: async () => null,
      create: async () => ({}),
    },
  };
  const execution = {
    wakeNow: async () => {
      trace.push('execution');
      const context = storedEnvelope.context as {
        showcase_event?: string;
        showcase_exit_price?: number;
        showcase_exit_reason?: string;
      };
      assert.equal(context.showcase_event, 'POSITION_CLOSED');
      assert.equal(context.showcase_exit_price, 64_620.5);
      assert.equal(context.showcase_exit_reason, 'PROFIT_LOCK_LADDER');
    },
  };
  const service = createService('dashboard-active', secret, { prisma, execution });
  const body = {
    schema: 'dcf-showcase-intent-v1',
    event: 'POSITION_CLOSED' as const,
    trade_id: 'cont-c105efa5',
    direction: 'LONG',
    signal_price: 64_500,
    exit_price: 64_620.5,
    exit_reason: 'PROFIT_LOCK_LADDER',
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
  assert.equal(typeof result.platform_received_at, 'string');
  assert.deepEqual(trace, ['persist', 'closed']);
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.equal(trace.includes('execution'), true);
});
