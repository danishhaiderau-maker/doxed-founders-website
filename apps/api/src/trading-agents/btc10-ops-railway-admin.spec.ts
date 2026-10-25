import 'reflect-metadata';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { UnauthorizedException } from '@nestjs/common';
import test from 'node:test';
import { TradingAgentsController } from './trading-agents.controller';

const controllerPath = join(__dirname, 'trading-agents.controller.ts');
const readController = () => readFileSync(controllerPath, 'utf8');

function routeBody(src: string, postPath: string): string {
  const marker = `@Post('${postPath}')`;
  const i = src.indexOf(marker);
  assert.ok(i > 0, `missing ${marker}`);
  const next = src.indexOf('@Post(', i + marker.length);
  return src.slice(i, next > 0 ? next : undefined);
}

function assertEmergencyReconcileAdminContract(src: string): void {
  const body = routeBody(src, ':slug/ops/emergency-reconcile');
  assert.ok(body.includes('assertBotAdminToken(adminHeader, authorization)'));
  assert.ok(
    body.indexOf('assertBotAdminToken') < body.indexOf('getOpsRelayStatus'),
    'admin auth must run before ops status load',
  );
  assert.ok(body.includes("'FLATTEN_PAUSED_UNATTRIBUTED_RESIDUAL'"));
  assert.equal(/adminHeader\}|authorization\}/.test(body), false);
}

function assertRecoverAlreadyFlatAdminContract(src: string): void {
  const body = routeBody(src, ':slug/ops/recover-already-flat');
  assert.ok(body.includes('assertBotAdminToken(adminHeader, authorization)'));
  assert.ok(
    body.indexOf('assertBotAdminToken') < body.indexOf('getOpsRelayStatus'),
    'admin auth must run before ops status load',
  );
  assert.ok(body.includes("'RECOVER_ALREADY_FLAT_PENDING_WITHOUT_CLOSE'"));
  assert.equal(/adminHeader\}|authorization\}/.test(body), false);
}

test('ops/emergency-reconcile route is admin-token only (not monitor read)', () => {
  assertEmergencyReconcileAdminContract(readController());
});

test('ops/recover-already-flat route is admin-token only (not monitor read)', () => {
  assertRecoverAlreadyFlatAdminContract(readController());
});

test('mutation proof: emergency-reconcile without assertBotAdminToken fails contract', () => {
  const mutant = readController().replace(
    /assertBotAdminToken\(adminHeader, authorization\);\n    const status = await this\.tradingAgents\.getOpsRelayStatus\(\n      slug, userId, adminHeader, authorization,\n    \);/,
    'const status = await this.tradingAgents.getOpsRelayStatus(\n      slug, userId, adminHeader, authorization,\n    );',
  );
  assert.throws(() => assertEmergencyReconcileAdminContract(mutant));
});

test('mutation proof: emergency-reconcile with admin assert after getOpsRelayStatus fails contract', () => {
  const src = readController();
  const body = routeBody(src, ':slug/ops/emergency-reconcile');
  const reordered = body
    .replace(
      'assertBotAdminToken(adminHeader, authorization);\n    const status = await this.tradingAgents.getOpsRelayStatus(\n      slug, userId, adminHeader, authorization,\n    );',
      'const status = await this.tradingAgents.getOpsRelayStatus(\n      slug, userId, adminHeader, authorization,\n    );\n    assertBotAdminToken(adminHeader, authorization);',
    );
  const mutant = src.replace(body, reordered);
  assert.throws(() => assertEmergencyReconcileAdminContract(mutant));
});

test('mutation proof: recover-already-flat without assertBotAdminToken fails contract', () => {
  const mutant = readController().replace(
    'assertBotAdminToken(adminHeader, authorization);\n    await this.tradingAgents.getOpsRelayStatus(\n      slug, body?.userId, adminHeader, authorization,\n    );',
    'await this.tradingAgents.getOpsRelayStatus(\n      slug, body?.userId, adminHeader, authorization,\n    );',
  );
  assert.throws(() => assertRecoverAlreadyFlatAdminContract(mutant));
});

test('mutation proof: recover-already-flat with admin assert after getOpsRelayStatus fails contract', () => {
  const src = readController();
  const body = routeBody(src, ':slug/ops/recover-already-flat');
  const reordered = body
    .replace(
      'assertBotAdminToken(adminHeader, authorization);\n    await this.tradingAgents.getOpsRelayStatus(\n      slug, body?.userId, adminHeader, authorization,\n    );',
      'await this.tradingAgents.getOpsRelayStatus(\n      slug, body?.userId, adminHeader, authorization,\n    );\n    assertBotAdminToken(adminHeader, authorization);',
    );
  const mutant = src.replace(body, reordered);
  assert.throws(() => assertRecoverAlreadyFlatAdminContract(mutant));
});

test('MONITOR_READ_TOKEN cannot invoke emergency-reconcile or recover-already-flat', async () => {
  const prev = {
    admin: process.env.BOT_ADMIN_TOKEN,
    monitor: process.env.MONITOR_READ_TOKEN,
  };
  process.env.BOT_ADMIN_TOKEN = 'admin-token-btc10';
  process.env.MONITOR_READ_TOKEN = 'monitor-token-btc10';
  let statusCalls = 0;
  let emergencyCalls = 0;
  let recoverCalls = 0;
  const tradingAgents = {
    getOpsRelayStatus: async () => {
      statusCalls += 1;
      return {
        status: 'PAUSED',
        positionMismatchDetectedAt: '2026-08-16T14:28:21.690Z',
        lastError: 'mismatch',
      };
    },
  };
  const execution = {
    requestExecutorEmergencyReconcile: async () => {
      emergencyCalls += 1;
      return { flattened: 1 };
    },
    recoverAlreadyFlatPendingEntries: async () => {
      recoverCalls += 1;
      return { recovered: 1 };
    },
  };
  const controller = new TradingAgentsController(
    tradingAgents as never,
    {} as never,
    {} as never,
    {} as never,
    execution as never,
  );
  try {
    await assert.rejects(
      controller.opsEmergencyReconcile(
        'conservative-btc',
        'user-1',
        'monitor-token-btc10',
        undefined,
        { confirmation: 'FLATTEN_PAUSED_UNATTRIBUTED_RESIDUAL' },
      ),
      (err: unknown) => err instanceof UnauthorizedException
        && /Invalid BOT_ADMIN_TOKEN/.test(String((err as UnauthorizedException).message)),
    );
    await assert.rejects(
      controller.opsEmergencyReconcile(
        'conservative-btc',
        'user-1',
        undefined,
        'Bearer monitor-token-btc10',
        { confirmation: 'FLATTEN_PAUSED_UNATTRIBUTED_RESIDUAL' },
      ),
      (err: unknown) => err instanceof UnauthorizedException,
    );
    await assert.rejects(
      controller.opsRecoverAlreadyFlat(
        'conservative-btc',
        {
          userId: 'user-1',
          participantIds: ['p1'],
          confirmation: 'RECOVER_ALREADY_FLAT_PENDING_WITHOUT_CLOSE',
        },
        'monitor-token-btc10',
        undefined,
      ),
      (err: unknown) => err instanceof UnauthorizedException,
    );
    assert.equal(statusCalls, 0);
    assert.equal(emergencyCalls, 0);
    assert.equal(recoverCalls, 0);

    await controller.opsEmergencyReconcile(
      'conservative-btc',
      'user-1',
      'admin-token-btc10',
      undefined,
      { confirmation: 'FLATTEN_PAUSED_UNATTRIBUTED_RESIDUAL' },
    );
    assert.equal(emergencyCalls, 1);
    assert.ok(statusCalls >= 1);
  } finally {
    if (prev.admin === undefined) delete process.env.BOT_ADMIN_TOKEN;
    else process.env.BOT_ADMIN_TOKEN = prev.admin;
    if (prev.monitor === undefined) delete process.env.MONITOR_READ_TOKEN;
    else process.env.MONITOR_READ_TOKEN = prev.monitor;
  }
});
