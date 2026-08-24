import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import test from 'node:test';
import {
  ShowcaseRelayEventsService,
  canClaimExpiredCycleForCurrentGeneration,
  exactLifecycleRevisionMatches,
  relayIntentEnvelope,
  positionReducedEvidence,
  isReductionEvidenceIdentity,
  reductionAuditMatches,
  isReductionSequenceStale,
  shouldApplyExactLifecycleUpdate,
} from './showcase-relay-events.service';

test('POSITION_REDUCED accepts only reconciled reduce-only evidence', () => {
  const valid = positionReducedEvidence({
    schema: 'dcf-showcase-intent-v1',
    event: 'POSITION_REDUCED',
    event_id: 'reduce-event-1',
    event_seq: 4,
    ts: new Date().toISOString(),
    prior_qty: 0.03, reduced_qty: 0.01, remaining_qty: 0.02,
    fill_price: 64_250, reduction_id: 'reduce-command-1',
  });
  assert.equal(valid?.reductionId, 'reduce-command-1');
  assert.equal(valid?.remainingQty, 0.02);

  assert.equal(positionReducedEvidence({
    schema: 'dcf-showcase-intent-v1', event: 'POSITION_REDUCED',
    event_id: 'bad', reduction_id: 'bad-command', event_seq: 1,
    ts: new Date().toISOString(), prior_qty: 0.03, reduced_qty: 0.01,
    remaining_qty: 0.025, fill_price: 64_250,
  }), undefined);
  assert.equal(positionReducedEvidence({
    schema: 'dcf-showcase-intent-v1', event: 'POSITION_REDUCED',
    event_id: 'missing-reduction-id', event_seq: 1,
    ts: new Date().toISOString(), prior_qty: 0.03, reduced_qty: 0.01,
    remaining_qty: 0.02, fill_price: 64_250,
  }), undefined);
  assert.equal(isReductionEvidenceIdentity('o29ps-a', 'OFFSET_029_ATR_PROTECTED'), true);
  assert.equal(isReductionEvidenceIdentity('o29rd-a', 'OFFSET_029_ATR_REGIME'), true);
  assert.equal(isReductionEvidenceIdentity('o29ps-a', 'OFFSET_029_ATR_REGIME'), false);
  assert.equal(isReductionEvidenceIdentity('cont-a', 'CONTINUOUS'), false);
  assert.equal(reductionAuditMatches({
    tradeId: 'o29ps-a', eventSeq: 4, priorQty: 0.03, reducedQty: 0.01,
    remainingQty: 0.02, fillPrice: 64250,
  }, valid!, 'o29ps-a'), true);
  assert.equal(reductionAuditMatches({
    tradeId: 'o29ps-a', eventSeq: 4, priorQty: 0.03, reducedQty: 0.02,
    remainingQty: 0.01, fillPrice: 64250,
  }, valid!, 'o29ps-a'), false);
  assert.equal(isReductionSequenceStale(4, 3), true);
  assert.equal(isReductionSequenceStale(4, 4), true);
  assert.equal(isReductionSequenceStale(4, 5), false);
});

test('durable receipt matches the exact canonical event id, sequence, and limit', () => {
  const current = {
    action: 'ENTER',
    trade_id: 'cont-race',
    entry: { exact_limit_price: 63_167, exact_qty_btc: 0.02361 },
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
      qty: 0.02361,
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
      qty: 0.02361,
    }),
    false,
  );
});

test('signed executable envelope preserves the exact showcase quantity', () => {
  const envelope = relayIntentEnvelope('cycle-q', 'cont-exact-qty', {
    schema: 'dcf-showcase-intent-v1',
    event: 'ORDER_PLACED',
    trade_id: 'cont-exact-qty',
    ts: '2026-08-11T15:55:44.546Z',
    platform_received_at: '2026-08-11T15:55:44.653Z',
    direction: 'SHORT',
    executable: true,
    entry_limit_policy: 'micro_sr_structural_limit_v1',
    limit_price: 63_614.55,
    qty: 0.02361832782239017,
  }) as { entry?: { exact_qty_btc?: number } };
  assert.equal(envelope.entry?.exact_qty_btc, 0.02361832782239017);
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
    requestExecutorWake: async () => {
      overrides?.trace?.push('execution');
    },
  };
  const prismaBase = {
    signalCycleParticipant: { findFirst: async () => null },
    tradingAgentInstance: { findFirst: async () => null },
    ...(overrides?.prisma ?? {
      tradingAgent: {
        findUnique: async () => {
          overrides?.trace?.push('persist');
          return null;
        },
      },
    }),
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
    qty: 0.02361,
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
  // Each revision takes one canonical-state lock and one audit-idempotency
  // lock; the executor wake begins between those two durable transactions.
  assert.equal(advisoryCalls, 4);
  assert.deepEqual(advisoryKeys, [
    'agent-race:cont-race',
    'agent-race:cont-race',
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

test('rejects a signed executable order that omits exact showcase quantity', async () => {
  const secret = 'test-webhook-secret';
  const service = createService('dashboard-active', secret);
  const body = {
    schema: 'dcf-showcase-intent-v1',
    event: 'ORDER_PLACED' as const,
    trade_id: 'cont-0a0c1a1b',
    direction: 'SHORT',
    limit_price: 63_614.55,
    entry_limit_policy: 'micro_sr_structural_limit_v1',
    executable: true,
    dashboard_owner: true,
    bot_instance_id: 'dashboard-active',
    dashboard_port: 7002,
  };
  const rawBody = Buffer.from(JSON.stringify(body));
  const signature = `sha256=${createHmac('sha256', secret).update(rawBody).digest('hex')}`;
  await assert.rejects(
    service.ingest('conservative-btc', body, { rawBody, signatureHeader: signature }),
    /requires exact executable limit policy and quantity/,
  );
});

test('unsigned lifecycle is persisted for audit but cannot wake subscriber execution', async () => {
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
  assert.deepEqual(trace, ['persist']);
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
    requestExecutorWake: async () => {
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
  let preWakeAt: string | undefined;
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
    requestExecutorPreWake: (_event: string, _tradeId: string, receivedAt?: string) => {
      trace.push('prewake');
      preWakeAt = receivedAt;
    },
    requestExecutorWake: async (_event: string, _tradeId: string, receivedAt?: string) => {
      trace.push('execution');
      assert.equal(receivedAt, preWakeAt);
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
    qty: 0.02361,
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
  assert.deepEqual(trace, ['prewake', 'persist', 'execution']);
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.deepEqual(trace, ['prewake', 'persist', 'execution', 'canonical']);
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
    requestExecutorPreWake: (
      trigger: string,
      tradeId: string | null,
      receivedAt?: string,
      signedClose?: {
        exitPrice?: number;
        exitReason?: string;
        sourceEventAtMs?: number;
        platformReceivedAtMs?: number;
      },
    ) => {
      trace.push('prewake');
      assert.equal(trigger, 'POSITION_CLOSED');
      assert.equal(tradeId, 'cont-c105efa5');
      assert.equal(typeof receivedAt, 'string');
      assert.equal(signedClose?.exitPrice, 64_620.5);
      assert.equal(signedClose?.exitReason, 'PROFIT_LOCK_LADDER');
      assert.equal(signedClose?.platformReceivedAtMs, Date.parse(receivedAt!));
      assert.equal(typeof signedClose?.sourceEventAtMs, 'number');
    },
    requestExecutorWake: async () => {
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
    event_id: 'close-cont-c105efa5-7',
    event_seq: 7,
    ts: new Date().toISOString(),
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
  assert.deepEqual(trace, ['prewake', 'persist', 'closed', 'execution']);
});

test('signed ORDER_EXPIRED carries exact generation without sliding platform fallback TTL', async () => {
  const secret = 'test-webhook-secret';
  const expiresAtWrites: unknown[] = [];
  let storedEnvelope: Record<string, unknown> = {
    action: 'ENTER', direction: 'SHORT', entry: { mode: 'EXACT_LIMIT', exact_limit_price: 64_417.03 },
    context: { showcase_event_seq: 1 },
  };
  const prisma = {
    tradingAgent: { findUnique: async () => ({ id: 'agent-1' }) },
    signalCycle: {
      findUnique: async () => ({ id:'cycle-expiry', status:'PENDING_ENTRY', intentEnvelope:storedEnvelope }),
      update: async (args: { data: { intentEnvelope?: Record<string, unknown>; expiresAt?: unknown } }) => {
        if (args.data.intentEnvelope) storedEnvelope = args.data.intentEnvelope;
        expiresAtWrites.push(args.data.expiresAt);
        return {};
      },
    },
    signalCycleEvent: { findFirst: async()=>null, create: async()=>({}) },
  };
  const wakes: unknown[][] = [];
  const execution = {
    requestExecutorPreWake: (...args: unknown[]) => wakes.push(args),
    requestExecutorWake: async (...args: unknown[]) => wakes.push(args),
  };
  const service = createService('dashboard-active', secret, { prisma, execution });
  const body = {
    schema:'dcf-showcase-intent-v1', event:'ORDER_EXPIRED' as const,
    trade_id:'cont-143962d491f6', direction:'SHORT', event_seq:1,
    event_id:'cont-143962d491f6:ORDER_EXPIRED:1:2026-08-11T13:14:12.703492+00:00',
    limit_price:64_417.03, reason:'SIGNAL_TTL_EXPIRED',
    ts:'2026-08-11T13:14:12.703492+00:00',
    source_created_at:'2026-08-11T12:44:12.310797+00:00',
    source_expires_at:'2026-08-11T13:14:12.703492+00:00',
    research_lane:'CONTINUOUS', dashboard_owner:true,
    bot_instance_id:'dashboard-active', dashboard_port:7002,
  };
  const rawBody=Buffer.from(JSON.stringify(body));
  const signature=`sha256=${createHmac('sha256',secret).update(rawBody).digest('hex')}`;
  const result=await service.ingest('conservative-btc',body,{rawBody,signatureHeader:signature});
  assert.equal(result.ok,true);
  assert.equal(wakes[0]?.[0],'ORDER_EXPIRED');
  assert.equal((wakes[0]?.[3] as {eventSeq?:number})?.eventSeq,1);
  assert.ok(expiresAtWrites.every((value)=>value===undefined));
  const context=storedEnvelope.context as Record<string,unknown>;
  assert.equal(context.source_expires_at,body.source_expires_at);
});

test('signed POSITION_OPENED persists source fill evidence and queues the fill wake', async () => {
  const secret = 'test-webhook-secret';
  const trace: string[] = [];
  const createdEvents: Array<{ eventType: string; payload: Record<string, unknown> }> = [];
  const prisma = {
    tradingAgent: { findUnique: async () => ({ id: 'agent-1' }) },
    signalCycle: {
      findUnique: async (args: { where: Record<string, unknown> }) =>
        'agentId_tradeId' in args.where
          ? { id: 'cycle-existing', status: 'PENDING_ENTRY', intentEnvelope: { action: 'ENTER' } }
          : null,
      update: async () => ({}),
    },
    signalCycleEvent: {
      findFirst: async () => null,
      create: async (args: { data: { eventType: string; payload: Record<string, unknown> } }) => {
        createdEvents.push(args.data);
        return {};
      },
    },
  };
  const execution = {
    requestExecutorPreWake: (trigger: string, tradeId: string, receivedAt?: string) => {
      trace.push(`prewake:${trigger}:${tradeId}:${typeof receivedAt}`);
    },
    requestExecutorWake: async (trigger: string, tradeId: string, receivedAt?: string) => {
      trace.push(`wake:${trigger}:${tradeId}:${typeof receivedAt}`);
    },
  };
  const service = createService('dashboard-active', secret, { prisma, execution });
  const body = {
    schema: 'dcf-showcase-intent-v1',
    event: 'POSITION_OPENED' as const,
    trade_id: 'cont-f111aabbccdd',
    direction: 'SHORT',
    ts: '2026-08-12T12:57:14.865Z',
    fill_price: 64_000,
    qty: 0.03125,
    research_lane: 'CONTINUOUS',
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
  assert.deepEqual(trace, [
    'prewake:POSITION_OPENED:cont-f111aabbccdd:string',
    'wake:POSITION_OPENED:cont-f111aabbccdd:string',
  ]);
  assert.equal(createdEvents.at(-1)?.eventType, 'POSITION_OPENED');
  assert.equal(createdEvents.at(-1)?.payload.fill_price, 64_000);
});

test('signed POSITION_CLOSED reuses a deterministic cycle whose tradeId was relinked', async () => {
  const secret = 'test-webhook-secret';
  let createCalls = 0;
  let closed = false;
  let storedEnvelope: Record<string, unknown> = {
    action: 'ENTER',
    direction: 'SHORT',
    entry: { exact_limit_price: 65_256.66 },
    context: { showcase_event: 'LIMIT_UPDATED' },
  };
  const prisma = {
    tradingAgent: { findUnique: async () => ({ id: 'agent-1' }) },
    signalCycle: {
      findUnique: async (args: { where: Record<string, unknown> }) => {
        if ('agentId_tradeId' in args.where) return null;
        return {
          id: 'cyc_rel_cont7051a335b325',
          agentId: 'agent-1',
          status: closed ? 'CLOSED' : 'OPEN',
          intentEnvelope: storedEnvelope,
        };
      },
      create: async () => {
        createCalls += 1;
        throw new Error('must not create over a relinked deterministic cycle');
      },
      update: async (args: {
        data: { intentEnvelope?: Record<string, unknown>; status?: string };
      }) => {
        if (args.data.intentEnvelope) storedEnvelope = args.data.intentEnvelope;
        if (args.data.status === 'CLOSED') closed = true;
        return {};
      },
    },
    signalCycleEvent: {
      findFirst: async () => null,
      create: async () => ({}),
    },
  };
  const service = createService('dashboard-active', secret, { prisma });
  const body = {
    schema: 'dcf-showcase-intent-v1',
    event: 'POSITION_CLOSED' as const,
    trade_id: 'cont-7051a335b325',
    direction: 'SHORT',
    exit_price: 65_284,
    exit_reason: 'THESIS_FAST_CUT',
    event_id: 'close-cont-7051a335b325-9',
    event_seq: 9,
    ts: new Date().toISOString(),
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
  assert.equal(createCalls, 0);
  assert.equal(closed, true);
  const context = storedEnvelope.context as Record<string, unknown>;
  assert.equal(context.showcase_event, 'POSITION_CLOSED');
  assert.equal(context.showcase_exit_price, 65_284);
});

test('POSITION_CLOSED with no copy participant while relay is paused is acknowledged without a wake', async () => {
  const secret = 'test-webhook-secret';
  const createdEvents: Array<{ eventType: string; payload: Record<string, unknown> }> = [];
  const prisma = {
    tradingAgent: { findUnique: async () => ({ id: 'agent-1' }) },
    signalCycleParticipant: { findFirst: async () => null },
    tradingAgentInstance: {
      findFirst: async (args: { where?: { status?: string | { in?: string[] } } }) => {
        const status = args.where?.status;
        const allowed = typeof status === 'string' ? [status] : status?.in ?? [];
        if (allowed.includes('PAUSED') || allowed.includes('ACTIVE')) {
          return { id: 'cheetah-paused', userId: 'user-1' };
        }
        return null;
      },
    },
    signalCycle: {
      findUnique: async () => ({
        id: 'cycle-e33a',
        status: 'INTENT',
        intentEnvelope: { action: 'ENTER', context: {} },
      }),
      findFirst: async () => ({ id: 'cycle-e33a' }),
      create: async () => {
        throw new Error('must not create a retrospective cycle participant path');
      },
      update: async () => ({}),
    },
    signalCycleEvent: {
      findFirst: async () => null,
      create: async (args: { data: { eventType: string; payload: Record<string, unknown> } }) => {
        createdEvents.push({ eventType: args.data.eventType, payload: args.data.payload });
        return {};
      },
    },
  };
  const trace: string[] = [];
  const execution = {
    requestExecutorWake: async () => {
      trace.push('wake');
    },
    requestExecutorPreWake: () => {
      trace.push('prewake');
    },
  };
  const cycles = {
    wakeFromShowcase: async () => {
      trace.push('reconcile');
      return false;
    },
  };
  const service = createService('dashboard-active', secret, { prisma, execution, cycles, trace });
  const body = {
    schema: 'dcf-showcase-intent-v1',
    event: 'POSITION_CLOSED' as const,
    trade_id: 'cont-e33a384d01e6',
    research_lane: 'CONTINUOUS',
    direction: 'LONG',
    exit_price: 63_500,
    exit_reason: 'THESIS_FAST_CUT',
    event_id: 'close-cont-e33a384d01e6-1',
    event_seq: 1,
    ts: new Date().toISOString(),
    dashboard_owner: true,
    bot_instance_id: 'dashboard-active',
    dashboard_port: 7002,
  };
  const rawBody = Buffer.from(JSON.stringify(body));
  const signature = `sha256=${createHmac('sha256', secret).update(rawBody).digest('hex')}`;
  const result = await service.ingest('conservative-btc', body, {
    rawBody,
    signatureHeader: signature,
  }) as Record<string, unknown>;

  assert.equal(result.ok, true);
  assert.equal(result.accepted, true);
  assert.equal(result.action, 'NO_COPY_PARTICIPANT');
  assert.equal(result.reason, 'RELAY_WAS_PAUSED_AT_SOURCE_ENTRY');
  assert.equal(result.exchange_mutation, false);
  assert.equal(result.negative_evidence, 'SHOWCASE_ONLY_RELAY_PAUSED');
  assert.equal(trace.includes('wake'), false);
  assert.equal(trace.includes('prewake'), false);
  assert.equal(trace.includes('reconcile'), false);
  assert.equal(
    createdEvents.some((event) => event.payload?.type === 'SHOWCASE_ONLY_RELAY_PAUSED'),
    true,
  );
});

test('signed POSITION_CLOSED still wakes when no-copy lookup tables are missing', async () => {
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
      findUnique: async () => ({
        id: 'cycle-existing',
        status: 'OPEN',
        intentEnvelope: storedEnvelope,
      }),
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
    requestExecutorPreWake: () => {
      trace.push('prewake');
    },
    requestExecutorWake: async () => {
      trace.push('execution');
    },
  };
  const service = createService('dashboard-active', secret, {
    prisma: {
      ...prisma,
      signalCycleParticipant: undefined,
      tradingAgentInstance: undefined,
    },
    execution,
  });
  const body = {
    schema: 'dcf-showcase-intent-v1',
    event: 'POSITION_CLOSED' as const,
    trade_id: 'cont-c105efa5',
    direction: 'LONG',
    signal_price: 64_500,
    exit_price: 64_620.5,
    exit_reason: 'PROFIT_LOCK_LADDER',
    event_id: 'close-cont-c105efa5-lookup-guard',
    event_seq: 7,
    ts: new Date().toISOString(),
    dashboard_owner: true,
    bot_instance_id: 'dashboard-active',
    dashboard_port: 7002,
  };
  const rawBody = Buffer.from(JSON.stringify(body));
  const signature = `sha256=${createHmac('sha256', secret).update(rawBody).digest('hex')}`;
  const result = await service.ingest('conservative-btc', body, {
    rawBody,
    signatureHeader: signature,
  }) as Record<string, unknown>;

  assert.equal(result.ok, true);
  assert.equal(result.action, undefined);
  assert.deepEqual(trace, ['prewake', 'persist', 'closed', 'execution']);
});

test('ORDER_PLACED may claim a current-generation EXPIRED cycle but never a CLOSED one', () => {
  const incoming = {
    event: 'ORDER_PLACED' as const,
    schema: 'dcf-showcase-intent-v1',
    trade_id: 'cont-expired-claim',
    executable: true,
    entry_limit_policy: 'micro_sr_structural_limit_v1',
    limit_price: 64_200,
    qty: 0.031,
    event_seq: 2,
    ts: '2026-08-18T05:00:00.000Z',
  };
  assert.equal(
    canClaimExpiredCycleForCurrentGeneration({
      status: 'EXPIRED',
      current: { action: 'ENTER', context: { showcase_event: 'APPROVE_PENDING', showcase_event_seq: 1 } },
      incoming,
    }),
    true,
  );
  assert.equal(
    canClaimExpiredCycleForCurrentGeneration({
      status: 'CLOSED',
      current: { action: 'ENTER', context: { showcase_event: 'APPROVE_PENDING' } },
      incoming,
    }),
    false,
  );
  assert.equal(
    canClaimExpiredCycleForCurrentGeneration({
      status: 'EXPIRED',
      current: { action: 'ENTER', context: { showcase_event: 'POSITION_CLOSED' } },
      incoming,
    }),
    false,
  );
});

test('POSITION_CLOSED with no copy participant while Cheetah is ACTIVE is acknowledged without a wake', async () => {
  const secret = 'test-webhook-secret';
  const createdEvents: Array<{ eventType: string; payload: Record<string, unknown> }> = [];
  const prisma = {
    tradingAgent: { findUnique: async () => ({ id: 'agent-1' }) },
    signalCycleParticipant: { findFirst: async () => null },
    tradingAgentInstance: {
      findFirst: async () => ({ id: 'cheetah-active', userId: 'user-1', status: 'ACTIVE' }),
    },
    signalCycle: {
      findUnique: async () => ({
        id: 'cycle-paper-only',
        status: 'INTENT',
        intentEnvelope: { action: 'ENTER', context: {} },
      }),
      findFirst: async () => ({ id: 'cycle-paper-only' }),
      create: async () => {
        throw new Error('must not create a retrospective cycle participant path');
      },
      update: async () => ({}),
    },
    signalCycleEvent: {
      findFirst: async () => null,
      create: async (args: { data: { eventType: string; payload: Record<string, unknown> } }) => {
        createdEvents.push({ eventType: args.data.eventType, payload: args.data.payload });
        return {};
      },
    },
  };
  const trace: string[] = [];
  const execution = {
    requestExecutorWake: async () => { trace.push('wake'); },
    requestExecutorPreWake: () => { trace.push('prewake'); },
  };
  const cycles = {
    wakeFromShowcase: async () => {
      trace.push('reconcile');
      return false;
    },
  };
  const service = createService('dashboard-active', secret, { prisma, execution, cycles, trace });
  const body = {
    schema: 'dcf-showcase-intent-v1',
    event: 'POSITION_CLOSED' as const,
    trade_id: 'cont-08224c821212',
    research_lane: 'CONTINUOUS',
    direction: 'SHORT',
    exit_price: 64_249,
    exit_reason: 'PROFIT_LOCK_LADDER',
    event_id: 'close-cont-08224c821212-1',
    event_seq: 4,
    ts: new Date().toISOString(),
    dashboard_owner: true,
    bot_instance_id: 'dashboard-active',
    dashboard_port: 7002,
  };
  const rawBody = Buffer.from(JSON.stringify(body));
  const signature = `sha256=${createHmac('sha256', secret).update(rawBody).digest('hex')}`;
  const result = await service.ingest('conservative-btc', body, {
    rawBody,
    signatureHeader: signature,
  }) as Record<string, unknown>;
  assert.equal(result.ok, true);
  assert.equal(result.action, 'NO_COPY_PARTICIPANT');
  assert.equal(result.exchange_mutation, false);
  assert.equal(trace.includes('wake'), false);
  assert.equal(trace.includes('prewake'), false);
});

test('ORDER_EXPIRED with a research TTL reason is acked without flatten wake', async () => {
  const secret = 'test-webhook-secret';
  const prisma = {
    tradingAgent: { findUnique: async () => ({ id: 'agent-1' }) },
    signalCycle: {
      findFirst: async () => ({ id: 'cycle-ttl', agentId: 'agent-1', status: 'INTENT', intentEnvelope: {} }),
      findUnique: async () => ({ id: 'cycle-ttl', agentId: 'agent-1', status: 'INTENT', intentEnvelope: {} }),
      create: async () => ({ id: 'cycle-ttl' }),
      update: async () => ({}),
    },
    signalCycleEvent: { findFirst: async () => null, create: async () => ({}) },
    $executeRaw: async () => 1,
    $transaction: async (fn: (tx: unknown) => Promise<unknown>) => fn(prisma),
  };
  const trace: string[] = [];
  const execution = {
    requestExecutorWake: async () => { trace.push('wake'); },
    requestExecutorPreWake: () => { trace.push('prewake'); },
  };
  const service = createService('dashboard-active', secret, { prisma, execution, trace });
  const body = {
    schema: 'dcf-showcase-intent-v1',
    event: 'ORDER_EXPIRED' as const,
    trade_id: 'cont-143962d491f7',
    research_lane: 'CONTINUOUS',
    direction: 'SHORT',
    reason: 'VIRTUAL_TOUCH_BEFORE_SELECTED_ENTRY',
    event_id: 'exp-1',
    event_seq: 1,
    limit_price: 64_278.17,
    ts: new Date().toISOString(),
    source_expires_at: new Date().toISOString(),
    dashboard_owner: true,
    bot_instance_id: 'dashboard-active',
    dashboard_port: 7002,
  };
  const rawBody = Buffer.from(JSON.stringify(body));
  const signature = `sha256=${createHmac('sha256', secret).update(rawBody).digest('hex')}`;
  const result = await service.ingest('conservative-btc', body, {
    rawBody,
    signatureHeader: signature,
  }) as Record<string, unknown>;
  assert.equal(result.ok, true);
  assert.equal(result.action, 'ORDER_EXPIRED_ACKED');
  assert.equal(result.exchange_mutation, false);
  assert.equal(trace.includes('wake'), false);
});
