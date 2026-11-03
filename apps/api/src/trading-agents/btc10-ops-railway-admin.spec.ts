import 'reflect-metadata';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { UnauthorizedException } from '@nestjs/common';
import test from 'node:test';
import { assertBotAdminToken } from './live-copy-ops.service';
import { TradingAgentsController } from './trading-agents.controller';

const controllerPath = join(__dirname, 'trading-agents.controller.ts');
const readController = () => readFileSync(controllerPath, 'utf8');

/** Every mutating cookie-free ops POST — admin-token only (not MONITOR_READ_TOKEN). */
const ADMIN_OPS_POSTS = [
  ':slug/ops/rearm',
  ':slug/ops/force-flat-disarm',
  ':slug/ops/position-flatten',
  ':slug/ops/emergency-reconcile',
  ':slug/ops/recover-already-flat',
  ':slug/ops/pause',
  ':slug/ops/live-copy/cancel-orphan-stop',
  ':slug/ops/refresh-flat-audit',
] as const;

function routeBody(src: string, postPath: string): string {
  const marker = `@Post('${postPath}')`;
  const i = src.indexOf(marker);
  assert.ok(i > 0, `missing ${marker}`);
  const next = src.indexOf('@Post(', i + marker.length);
  return src.slice(i, next > 0 ? next : undefined);
}

function assertAdminTokenBeforeWork(body: string): void {
  assert.ok(
    body.includes('assertBotAdminToken(adminHeader, authorization)'),
    'missing assertBotAdminToken',
  );
  assert.equal(
    body.includes('assertBotReadToken'),
    false,
    'admin route must not call assertBotReadToken',
  );
  const authIdx = body.indexOf('assertBotAdminToken(adminHeader, authorization)');
  const firstAwait = body.indexOf('await ');
  if (firstAwait >= 0) {
    assert.ok(authIdx < firstAwait, 'admin auth must run before any await');
  }
}

function assertEmergencyReconcileAdminContract(src: string): void {
  const body = routeBody(src, ':slug/ops/emergency-reconcile');
  assertAdminTokenBeforeWork(body);
  assert.ok(
    body.indexOf('assertBotAdminToken') < body.indexOf('getOpsRelayStatus'),
    'admin auth must run before ops status load',
  );
  assert.ok(body.includes("'FLATTEN_PAUSED_UNATTRIBUTED_RESIDUAL'"));
  assert.equal(/adminHeader\}|authorization\}/.test(body), false);
}

function assertRecoverAlreadyFlatAdminContract(src: string): void {
  const body = routeBody(src, ':slug/ops/recover-already-flat');
  assertAdminTokenBeforeWork(body);
  assert.ok(
    body.indexOf('assertBotAdminToken') < body.indexOf('getOpsRelayStatus'),
    'admin auth must run before ops status load',
  );
  assert.ok(body.includes("'RECOVER_ALREADY_FLAT_PENDING_WITHOUT_CLOSE'"));
  assert.equal(/adminHeader\}|authorization\}/.test(body), false);
}

function assertOpsPauseAdminContract(src: string): void {
  const body = routeBody(src, ':slug/ops/pause');
  assertAdminTokenBeforeWork(body);
  assert.ok(
    body.indexOf('assertBotAdminToken') < body.indexOf('getOpsRelayStatus'),
    'admin auth must run before ops status load',
  );
  assert.ok(body.includes("'PAUSE_AND_SETTLE_MANAGED_ENTRIES'"));
  assert.equal(/adminHeader\}|authorization\}/.test(body), false);
}

function assertRefreshFlatAuditAdminContract(src: string): void {
  const body = routeBody(src, ':slug/ops/refresh-flat-audit');
  assertAdminTokenBeforeWork(body);
  assert.ok(
    body.indexOf('assertBotAdminToken') < body.indexOf('getOpsRelayStatus'),
    'admin auth must run before ops status load',
  );
  assert.ok(body.includes("'REFRESH_PAUSED_FLAT_AUDIT'"));
  assert.equal(/adminHeader\}|authorization\}/.test(body), false);
}

function isUnauthorized401(err: unknown): boolean {
  return err instanceof UnauthorizedException
    && err.getStatus() === 401
    && /Invalid BOT_ADMIN_TOKEN/.test(String(err.message));
}

test('assertBotAdminToken refuses MONITOR_READ_TOKEN via X-Bot-Admin-Token and Bearer', () => {
  const prev = {
    admin: process.env.BOT_ADMIN_TOKEN,
    monitor: process.env.MONITOR_READ_TOKEN,
  };
  process.env.BOT_ADMIN_TOKEN = 'admin-token-btc10';
  process.env.MONITOR_READ_TOKEN = 'monitor-token-btc10';
  try {
    assert.throws(() => assertBotAdminToken('monitor-token-btc10'), UnauthorizedException);
    assert.throws(() => assertBotAdminToken(undefined, 'Bearer monitor-token-btc10'), UnauthorizedException);
    assert.doesNotThrow(() => assertBotAdminToken('admin-token-btc10'));
    assert.doesNotThrow(() => assertBotAdminToken(undefined, 'Bearer admin-token-btc10'));
  } finally {
    if (prev.admin === undefined) delete process.env.BOT_ADMIN_TOKEN;
    else process.env.BOT_ADMIN_TOKEN = prev.admin;
    if (prev.monitor === undefined) delete process.env.MONITOR_READ_TOKEN;
    else process.env.MONITOR_READ_TOKEN = prev.monitor;
  }
});

test('ops POST inventory is complete and every admin route requires assertBotAdminToken first', () => {
  const src = readController();
  const discovered = [...src.matchAll(/@Post\('([^']*ops[^']*)'\)/g)].map((m) => m[1]).sort();
  assert.deepEqual(
    discovered,
    [...ADMIN_OPS_POSTS].sort(),
    'ops POST roster drifted — update ADMIN_OPS_POSTS and auth coverage',
  );
  for (const path of ADMIN_OPS_POSTS) {
    assertAdminTokenBeforeWork(routeBody(src, path));
  }
});

test('ops/emergency-reconcile route is admin-token only (not monitor read)', () => {
  assertEmergencyReconcileAdminContract(readController());
});

test('ops/recover-already-flat route is admin-token only (not monitor read)', () => {
  assertRecoverAlreadyFlatAdminContract(readController());
});

test('ops/pause route is admin-token only (not monitor read)', () => {
  assertOpsPauseAdminContract(readController());
});

test('ops/refresh-flat-audit route is admin-token only (not monitor read)', () => {
  assertRefreshFlatAuditAdminContract(readController());
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

test('mutation proof: ops/pause without assertBotAdminToken fails contract', () => {
  const mutant = readController().replace(
    'assertBotAdminToken(adminHeader, authorization);\n    await this.tradingAgents.getOpsRelayStatus(\n      slug, body?.userId, adminHeader, authorization,\n    );\n    if (body?.confirmation !== \'PAUSE_AND_SETTLE_MANAGED_ENTRIES\')',
    'await this.tradingAgents.getOpsRelayStatus(\n      slug, body?.userId, adminHeader, authorization,\n    );\n    if (body?.confirmation !== \'PAUSE_AND_SETTLE_MANAGED_ENTRIES\')',
  );
  assert.throws(() => assertOpsPauseAdminContract(mutant));
});

test('mutation proof: ops/pause with admin assert after getOpsRelayStatus fails contract', () => {
  const src = readController();
  const body = routeBody(src, ':slug/ops/pause');
  const reordered = body
    .replace(
      'assertBotAdminToken(adminHeader, authorization);\n    await this.tradingAgents.getOpsRelayStatus(\n      slug, body?.userId, adminHeader, authorization,\n    );',
      'await this.tradingAgents.getOpsRelayStatus(\n      slug, body?.userId, adminHeader, authorization,\n    );\n    assertBotAdminToken(adminHeader, authorization);',
    );
  const mutant = src.replace(body, reordered);
  assert.throws(() => assertOpsPauseAdminContract(mutant));
});

test('mutation proof: refresh-flat-audit without assertBotAdminToken fails contract', () => {
  const mutant = readController().replace(
    /assertBotAdminToken\(adminHeader, authorization\);\n    const status = await this\.tradingAgents\.getOpsRelayStatus\(\n      slug, body\?\.userId, adminHeader, authorization,\n    \);/,
    'const status = await this.tradingAgents.getOpsRelayStatus(\n      slug, body?.userId, adminHeader, authorization,\n    );',
  );
  assert.throws(() => assertRefreshFlatAuditAdminContract(mutant));
});

test('mutation proof: refresh-flat-audit with admin assert after getOpsRelayStatus fails contract', () => {
  const src = readController();
  const body = routeBody(src, ':slug/ops/refresh-flat-audit');
  const reordered = body
    .replace(
      'assertBotAdminToken(adminHeader, authorization);\n    const status = await this.tradingAgents.getOpsRelayStatus(\n      slug, body?.userId, adminHeader, authorization,\n    );',
      'const status = await this.tradingAgents.getOpsRelayStatus(\n      slug, body?.userId, adminHeader, authorization,\n    );\n    assertBotAdminToken(adminHeader, authorization);',
    );
  const mutant = src.replace(body, reordered);
  assert.throws(() => assertRefreshFlatAuditAdminContract(mutant));
});

test('MONITOR_READ_TOKEN → 401 on every admin ops POST via both headers; admin passes both', async () => {
  const prev = {
    admin: process.env.BOT_ADMIN_TOKEN,
    monitor: process.env.MONITOR_READ_TOKEN,
  };
  process.env.BOT_ADMIN_TOKEN = 'admin-token-btc10';
  process.env.MONITOR_READ_TOKEN = 'monitor-token-btc10';

  const counters = {
    status: 0,
    rearmFlat: 0,
    rearmPause: 0,
    forceFlat: 0,
    positionFlat: 0,
    emergency: 0,
    recover: 0,
    pause: 0,
    orphan: 0,
    audit: 0,
    l27: 0,
  };

  const tradingAgents = {
    getOpsRelayStatus: async () => {
      counters.status += 1;
      return {
        status: 'PAUSED',
        relayExecutionMode: 'PAUSED',
        relayArmedAt: null,
        realTradingConfirmedAt: null,
        positionMismatchDetectedAt: '2026-08-16T14:28:21.690Z',
        lastError: 'mismatch',
      };
    },
  };
  const instances = {
    assertLiveArmL27: async () => {
      counters.l27 += 1;
    },
    setInstancePaused: async (_uid: string, _slug: string, paused: boolean) => {
      if (paused) counters.pause += 1;
      else counters.rearmPause += 1;
      return { ok: true, status: paused ? 'PAUSED' : 'ACTIVE' };
    },
    refreshPausedFlatAudit: async () => {
      counters.audit += 1;
      return { accepted: true, status: 'PAUSED', resumed: false, armed: false, flat: true };
    },
  };
  const execution = {
    emergencyFlattenOpenCopyLots: async () => {
      counters.rearmFlat += 1;
      return { flattened: 1 };
    },
    forceFlatDisarmAccount: async () => {
      counters.forceFlat += 1;
      counters.positionFlat += 1;
      return {
        verdict: 'FLAT_NO_STOPS',
        flattened: 1,
        cancelled: 0,
        errors: [],
        fenced: true,
        dbReconciled: true,
      };
    },
    requestExecutorEmergencyReconcile: async () => {
      counters.emergency += 1;
      return { flattened: 1 };
    },
    recoverAlreadyFlatPendingEntries: async () => {
      counters.recover += 1;
      return { recovered: 1 };
    },
    cancelOrphanReduceOnlyStopWhenFlat: async () => {
      counters.orphan += 1;
      return { cancelled: true, orderId: 99 };
    },
  };
  const liveCopyOps = {
    resolveUserId: async (userId?: string) => userId!.trim(),
  };
  const controller = new TradingAgentsController(
    tradingAgents as never,
    instances as never,
    {} as never,
    {} as never,
    execution as never,
    liveCopyOps as never,
  );

  type Invoke = (adminHeader?: string, authorization?: string) => Promise<unknown>;
  const routes: { name: string; invoke: Invoke }[] = [
    {
      name: 'ops/rearm',
      invoke: (h, a) => controller.opsRearm(
        'conservative-btc', 'user-1', h, a, { confirmation: 'FORCE_FLAT_THEN_REARM' },
      ),
    },
    {
      name: 'ops/force-flat-disarm',
      invoke: (h, a) => controller.opsForceFlatDisarm(
        'conservative-btc', 'user-1', h, a, { confirmation: 'FORCE_FLAT_AND_DISARM' },
      ),
    },
    {
      name: 'ops/position-flatten',
      invoke: (h, a) => controller.opsPositionFlatten(
        'conservative-btc', 'user-1', h, a, { confirmation: 'FLATTEN_POSITION_ONLY' },
      ),
    },
    {
      name: 'ops/emergency-reconcile',
      invoke: (h, a) => controller.opsEmergencyReconcile(
        'conservative-btc', 'user-1', h, a, { confirmation: 'FLATTEN_PAUSED_UNATTRIBUTED_RESIDUAL' },
      ),
    },
    {
      name: 'ops/recover-already-flat',
      invoke: (h, a) => controller.opsRecoverAlreadyFlat(
        'conservative-btc',
        {
          userId: 'user-1',
          participantIds: ['p1'],
          confirmation: 'RECOVER_ALREADY_FLAT_PENDING_WITHOUT_CLOSE',
        },
        h,
        a,
      ),
    },
    {
      name: 'ops/pause',
      invoke: (h, a) => controller.opsPause(
        'conservative-btc',
        { userId: 'user-1', confirmation: 'PAUSE_AND_SETTLE_MANAGED_ENTRIES' },
        h,
        a,
      ),
    },
    {
      name: 'ops/live-copy/cancel-orphan-stop',
      invoke: (h, a) => controller.opsCancelOrphanReduceOnlyStop(
        'conservative-btc',
        {
          userId: 'user-1',
          orderId: 99,
          confirmation: 'CANCEL_ORPHAN_REDUCE_ONLY_STOP_WHEN_FLAT',
        },
        h,
        a,
      ),
    },
    {
      name: 'ops/refresh-flat-audit',
      invoke: (h, a) => controller.opsRefreshFlatAudit(
        'conservative-btc',
        { userId: 'user-1', confirmation: 'REFRESH_PAUSED_FLAT_AUDIT' },
        h,
        a,
      ),
    },
  ];
  assert.equal(routes.length, ADMIN_OPS_POSTS.length);

  try {
    for (const route of routes) {
      await assert.rejects(
        () => route.invoke('monitor-token-btc10', undefined),
        isUnauthorized401,
        `${route.name}: X-Bot-Admin-Token=MONITOR_READ_TOKEN must 401`,
      );
      await assert.rejects(
        () => route.invoke(undefined, 'Bearer monitor-token-btc10'),
        isUnauthorized401,
        `${route.name}: Bearer MONITOR_READ_TOKEN must 401`,
      );
    }

    assert.equal(counters.status, 0);
    assert.equal(counters.rearmFlat, 0);
    assert.equal(counters.rearmPause, 0);
    assert.equal(counters.forceFlat, 0);
    assert.equal(counters.positionFlat, 0);
    assert.equal(counters.emergency, 0);
    assert.equal(counters.recover, 0);
    assert.equal(counters.pause, 0);
    assert.equal(counters.orphan, 0);
    assert.equal(counters.audit, 0);
    assert.equal(counters.l27, 0);

    // Admin via X-Bot-Admin-Token — one pass per route.
    for (const route of routes) {
      await route.invoke('admin-token-btc10', undefined);
    }
    assert.equal(counters.rearmFlat, 1);
    assert.equal(counters.rearmPause, 1);
    assert.equal(counters.l27, 1);
    assert.equal(counters.emergency, 1);
    assert.equal(counters.recover, 1);
    assert.ok(counters.pause >= 1); // force-flat-disarm + pause
    assert.equal(counters.orphan, 1);
    assert.equal(counters.audit, 1);
    assert.ok(counters.forceFlat >= 1);
    assert.ok(counters.status >= 1);

    // Reset mutation counters for Bearer pass (status may accumulate).
    const statusBeforeBearer = counters.status;
    counters.rearmFlat = 0;
    counters.rearmPause = 0;
    counters.l27 = 0;
    counters.emergency = 0;
    counters.recover = 0;
    counters.pause = 0;
    counters.orphan = 0;
    counters.audit = 0;
    counters.forceFlat = 0;
    counters.positionFlat = 0;

    for (const route of routes) {
      await route.invoke(undefined, 'Bearer admin-token-btc10');
    }
    assert.equal(counters.rearmFlat, 1);
    assert.equal(counters.rearmPause, 1);
    assert.equal(counters.l27, 1);
    assert.equal(counters.emergency, 1);
    assert.equal(counters.recover, 1);
    assert.ok(counters.pause >= 1);
    assert.equal(counters.orphan, 1);
    assert.equal(counters.audit, 1);
    assert.ok(counters.forceFlat >= 1);
    assert.ok(counters.status > statusBeforeBearer);
  } finally {
    if (prev.admin === undefined) delete process.env.BOT_ADMIN_TOKEN;
    else process.env.BOT_ADMIN_TOKEN = prev.admin;
    if (prev.monitor === undefined) delete process.env.MONITOR_READ_TOKEN;
    else process.env.MONITOR_READ_TOKEN = prev.monitor;
  }
});
