import 'reflect-metadata';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { PATH_METADATA } from '@nestjs/common/constants';
import { TradingAgentsController } from './trading-agents.controller';

const read = (name: string) => readFileSync(join(__dirname, name), 'utf8');

test('session-summary is a neutral public alias for analyzer summary data', async () => {
  const calls: string[] = [];
  const tradingAgents = {
    getAnalyzerSummary: async (slug: string) => {
      calls.push(slug);
      return { ok: true, slug };
    },
  };
  const controller = new TradingAgentsController(
    tradingAgents as never,
    {} as never,
    {} as never,
    {} as never,
    {} as never,
  );

  assert.equal(
    Reflect.getMetadata(PATH_METADATA, controller.sessionSummary),
    ':slug/session-summary',
  );
  assert.deepEqual(await controller.sessionSummary('conservative-btc'), {
    ok: true,
    slug: 'conservative-btc',
  });
  assert.deepEqual(calls, ['conservative-btc']);
});

test('showcase-host defaults to Fly and requires an explicit local override', () => {
  const previous = process.env.SHOWCASE_HOST;
  const controller = new TradingAgentsController(
    {} as never,
    {} as never,
    {} as never,
    {} as never,
    {} as never,
  );

  try {
    delete process.env.SHOWCASE_HOST;
    assert.deepEqual(controller.showcaseHost(), { host: 'fly' });

    process.env.SHOWCASE_HOST = ' ';
    assert.deepEqual(controller.showcaseHost(), { host: 'fly' });

    process.env.SHOWCASE_HOST = ' local ';
    assert.deepEqual(controller.showcaseHost(), { host: 'local' });

    process.env.SHOWCASE_HOST = 'FLY';
    assert.deepEqual(controller.showcaseHost(), { host: 'fly' });
  } finally {
    if (previous === undefined) {
      delete process.env.SHOWCASE_HOST;
    } else {
      process.env.SHOWCASE_HOST = previous;
    }
  }
});

test('public API cannot start or stop the sole Fly risk-manager machine', () => {
  const controller = read('./trading-agents.controller.ts');
  const module = read('./trading-agents.module.ts');

  assert.doesNotMatch(controller, /fly-control|FlyControlService|flyControlStatus/);
  assert.doesNotMatch(module, /FlyControlService|fly-control\.service/);
});

test('ops already-flat recovery authenticates before execution and requires exact confirmation', async () => {
  let executions = 0;
  const tradingAgents = {
    getOpsRelayStatus: async (
      _slug: string, _userId: string, token: string,
    ) => {
      if (token !== 'valid-token') throw new Error('Invalid BOT_ADMIN_TOKEN');
      return { status: 'PAUSED' };
    },
  };
  const execution = {
    recoverAlreadyFlatPendingEntries: async () => {
      executions += 1;
      return { recovered: 2 };
    },
  };
  const controller = new TradingAgentsController(
    tradingAgents as never, {} as never, {} as never, {} as never, execution as never,
  );
  const body = {
    userId: 'user-a', participantIds: ['p1', 'p2'],
    confirmation: 'RECOVER_ALREADY_FLAT_PENDING_WITHOUT_CLOSE',
  };
  await assert.rejects(
    controller.opsRecoverAlreadyFlat('cheetah', body, 'wrong-token', undefined),
    /Invalid BOT_ADMIN_TOKEN/,
  );
  assert.equal(executions, 0);
  await assert.rejects(
    controller.opsRecoverAlreadyFlat(
      'cheetah', { ...body, confirmation: 'wrong' }, 'valid-token', undefined,
    ),
    /confirmation must equal/,
  );
  assert.equal(executions, 0);
  assert.deepEqual(
    await controller.opsRecoverAlreadyFlat('cheetah', body, 'valid-token', undefined),
    { recovered: 2 },
  );
  assert.equal(executions, 1);
});

test('ops emergency reconcile requires admin-scoped paused mismatch and explicit confirmation', async () => {
  const calls: unknown[][] = [];
  const tradingAgents = {
    getOpsRelayStatus: async (...args: unknown[]) => {
      calls.push(args);
      return {
        status: 'PAUSED',
        positionMismatchDetectedAt: '2026-08-16T14:28:21.690Z',
        lastError: 'UNPROTECTED_FILL_HELD',
      };
    },
  };
  const execution = {
    requestExecutorEmergencyReconcile: async (...args: unknown[]) => {
      calls.push(args);
      return { flattened: 1, requestId: 'request-1' };
    },
  };
  const controller = new TradingAgentsController(
    tradingAgents as never,
    {} as never,
    {} as never,
    {} as never,
    execution as never,
  );

  await assert.rejects(
    controller.opsEmergencyReconcile(
      'conservative-btc', 'user-1', 'secret', undefined, { confirmation: 'wrong' },
    ),
    /FLATTEN_PAUSED_UNATTRIBUTED_RESIDUAL/,
  );
  assert.deepEqual(calls, [['conservative-btc', 'user-1', 'secret', undefined]]);

  assert.deepEqual(
    await controller.opsEmergencyReconcile(
      'conservative-btc',
      'user-1',
      'secret',
      undefined,
      {
        confirmation: 'FLATTEN_PAUSED_UNATTRIBUTED_RESIDUAL',
        reason: 'incident-2026-08-16',
      },
    ),
    { flattened: 1, requestId: 'request-1', status: 'PAUSED', resumed: false },
  );
  assert.deepEqual(calls[1], ['conservative-btc', 'user-1', 'secret', undefined]);
  assert.deepEqual(calls[2], [
    'user-1',
    'conservative-btc',
    'incident-2026-08-16',
  ]);
});

test('ops emergency reconcile refuses an unpaused or unevidenced instance', async () => {
  let emergencyCalls = 0;
  const execution = {
    requestExecutorEmergencyReconcile: async () => {
      emergencyCalls += 1;
      return { flattened: 1, requestId: 'request-1' };
    },
  };
  for (const status of [
    { status: 'ACTIVE', positionMismatchDetectedAt: 'now', lastError: 'mismatch' },
    { status: 'PAUSED', positionMismatchDetectedAt: null, lastError: null },
  ]) {
    const controller = new TradingAgentsController(
      { getOpsRelayStatus: async () => status } as never,
      {} as never,
      {} as never,
      {} as never,
      execution as never,
    );
    await assert.rejects(
      controller.opsEmergencyReconcile(
        'conservative-btc',
        'user-1',
        undefined,
        'Bearer secret',
        { confirmation: 'FLATTEN_PAUSED_UNATTRIBUTED_RESIDUAL' },
      ),
      /PAUSED instance|mismatch evidence/,
    );
  }
  assert.equal(emergencyCalls, 0);
});
