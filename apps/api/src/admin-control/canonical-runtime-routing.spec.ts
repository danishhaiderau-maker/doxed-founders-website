import assert from 'node:assert/strict';
import test from 'node:test';
import { AdminControlService } from './admin-control.service';
import { ShowcaseRuntimeService } from './showcase-runtime.service';

test('admin pause disarms the canonical process without stopping infrastructure', async () => {
  const calls: Array<{ path: string; body: Record<string, unknown> }> = [];
  let invalidations = 0;
  const bridge = {
    proxyBotPost: async (path: string, body: Record<string, unknown>) => {
      calls.push({ path, body });
      return {
        ok: true,
        data: {
          status: 'paused',
          execution_paused: true,
          execution_reason: 'ADMIN_MANUAL',
        },
      };
    },
    invalidateCache: () => {
      invalidations += 1;
    },
  };
  const retiredRuntime = new Proxy(
    {},
    {
      get: () => () => {
        throw new Error('legacy infrastructure control must not be called');
      },
    },
  );
  const service = new AdminControlService(
    {} as never,
    bridge as never,
    {} as never,
    retiredRuntime as never,
  );

  const result = await service.pauseAgentTrading();
  assert.equal(result.ok, true);
  assert.equal(result.paused, true);
  assert.equal(result.killed, false);
  assert.equal(result.runtime, 'fly.io');
  assert.deepEqual(calls, [{ path: '/api/pause', body: {} }]);
  assert.equal(invalidations, 1);
});

test('paper force-flat pauses first, closes exact trades, and proves the final book', async () => {
  const calls: Array<{ path: string; body: Record<string, unknown> }> = [];
  let reads = 0;
  const bridge = {
    proxyBotPost: async (path: string, body: Record<string, unknown>) => {
      calls.push({ path, body });
      if (path === '/api/pause') {
        return { ok: true, data: { status: 'paused', execution_paused: true } };
      }
      return { ok: true, data: { status: 'closed' } };
    },
    invalidateCache: () => undefined,
    fetchPublicShowcaseState: async () => {
      reads += 1;
      return reads === 1
        ? {
            positions: [{ trade_id: 'paper-1' }, { trade_id: 'paper-2' }],
            orders: [{ trade_id: 'order-1' }, { trade_id: 'order-2' }],
          }
        : { positions: [], orders: [] };
    },
  };
  const service = new AdminControlService(
    {} as never,
    bridge as never,
    {} as never,
    {} as never,
  );

  const result = await service.forceFlatShowcasePaper();
  assert.equal(result.ok, true);
  assert.equal(result.paused, true);
  assert.equal(result.cancelledOrders, 2);
  assert.equal(result.closedPositions, 2);
  assert.equal(result.remainingPositions, 0);
  assert.equal(result.remainingOrders, 0);
  assert.deepEqual(calls, [
    { path: '/api/pause', body: {} },
    { path: '/api/orders/cancel', body: { trade_id: 'order-1' } },
    { path: '/api/orders/cancel', body: { trade_id: 'order-2' } },
    { path: '/api/positions/close', body: { trade_id: 'paper-1' } },
    { path: '/api/positions/close', body: { trade_id: 'paper-2' } },
  ]);
});

test('paper force-flat refuses blind closes when the exact Fly book is unavailable', async () => {
  const calls: string[] = [];
  const bridge = {
    proxyBotPost: async (path: string) => {
      calls.push(path);
      return { ok: true, data: { status: 'paused', execution_paused: true } };
    },
    invalidateCache: () => undefined,
    fetchPublicShowcaseState: async () => null,
  };
  const service = new AdminControlService(
    {} as never,
    bridge as never,
    {} as never,
    {} as never,
  );

  const result = await service.forceFlatShowcasePaper();
  assert.equal(result.ok, false);
  assert.equal(result.paused, true);
  assert.deepEqual(calls, ['/api/pause']);
  assert.match(result.message, /no blind closes/i);
});

test('admin resume targets the existing canonical Fly process only', async () => {
  const calls: string[] = [];
  const bridge = {
    fetchHealth: async () => ({ ok: true, source: 'fly-direct' }),
    proxyBotPost: async (path: string) => {
      calls.push(path);
      return { ok: true, data: { status: 'resumed', execution_paused: false } };
    },
    invalidateCache: () => undefined,
    fetchPublicShowcaseState: async () => ({ execution_paused: false }),
  };
  const retiredRuntime = new Proxy(
    {},
    {
      get: () => () => {
        throw new Error('legacy Railway start must not be called');
      },
    },
  );
  const service = new AdminControlService(
    {} as never,
    bridge as never,
    {} as never,
    retiredRuntime as never,
  );

  const result = await service.resumeAgentTrading();
  assert.equal(result.ok, true);
  assert.equal(result.resumed, true);
  assert.equal(result.runtime, 'fly.io');
  assert.deepEqual(calls, ['/api/resume']);
});

test('admin resume fails closed on an ambiguous 2xx response', async () => {
  let postFetches = 0;
  const bridge = {
    fetchHealth: async () => ({ ok: true, source: 'fly-direct' }),
    proxyBotPost: async () => ({ ok: true, data: { status: 'ok' } }),
    invalidateCache: () => undefined,
    fetchPublicShowcaseState: async () => {
      postFetches += 1;
      return { execution_paused: false };
    },
  };
  const service = new AdminControlService(
    {} as never,
    bridge as never,
    {} as never,
    {} as never,
  );

  const result = await service.resumeAgentTrading();
  assert.equal(result.ok, false);
  assert.equal(result.resumed, false);
  assert.equal(postFetches, 0);
  assert.ok('message' in result);
  assert.match(result.message, /explicitly confirm execution_paused=false/);
});

test('admin resume requires a fresh canonical post-fetch to prove the gate is unpaused', async () => {
  const bridge = {
    fetchHealth: async () => ({ ok: true, source: 'fly-direct' }),
    proxyBotPost: async () => ({ ok: true, data: { execution_paused: false } }),
    invalidateCache: () => undefined,
    fetchPublicShowcaseState: async () => ({ execution_paused: true }),
  };
  const service = new AdminControlService(
    {} as never,
    bridge as never,
    {} as never,
    {} as never,
  );

  const result = await service.resumeAgentTrading();
  assert.equal(result.ok, false);
  assert.equal(result.resumed, false);
  assert.ok('message' in result);
  assert.match(result.message, /fresh canonical Fly state/);
});

test('last-seen-only liveness is degraded rather than online', async () => {
  const bridge = {
    isEnabled: () => true,
    fetchPublicShowcaseState: async () => null,
    getLastLiveFetchAt: () => Date.now() - 2_000,
  };
  const service = new AdminControlService(
    {} as never,
    bridge as never,
    {} as never,
    {} as never,
  );

  const result = await service.getPublicAgentStatus();
  assert.equal(result.status, 'degraded');
  assert.match(result.label, /last verified/);
});

test('legacy restart route cannot masquerade as a resume or revive infrastructure', async () => {
  const forbiddenBridge = new Proxy(
    {},
    {
      get: () => () => {
        throw new Error('restart route must not call the bot or infrastructure');
      },
    },
  );
  const service = new AdminControlService(
    {} as never,
    forbiddenBridge as never,
    {} as never,
    {} as never,
  );

  const result = await service.restartAgentRuntime();
  assert.equal(result.ok, false);
  assert.equal(result.retired, true);
  assert.equal(result.runtime, 'fly.io');
});

test('credential settings cannot repoint the canonical bot URL', async () => {
  let writes = 0;
  const prisma = {
    platformSettings: {
      findUnique: async () => ({
        showcaseExchangeProvider: 'bitfinex',
        showcaseAiProvider: 'deepseek',
        showcaseBotPublicUrl: 'https://retired-owner.example',
      }),
      upsert: async () => {
        writes += 1;
      },
      update: async () => {
        writes += 1;
      },
    },
  };
  const runtime = new ShowcaseRuntimeService(prisma as never, {} as never);

  const status = await runtime.getCredentialsStatus();
  assert.equal(status.botPublicUrl, 'https://doxed-btc-bot.fly.dev');
  await assert.rejects(
    runtime.saveShowcaseCredentials('admin', {
      botPublicUrl: 'https://retired-owner.example',
    }),
    /locked to https:\/\/doxed-btc-bot\.fly\.dev/,
  );
  assert.equal(writes, 0);

  const push = await runtime.pushToCanonicalRuntime('admin');
  assert.equal(push.ok, false);
  assert.equal(push.retired, true);
  assert.match(push.message, /Railway runtime push is retired/);
});

test('empty showcase settings default to the canonical Bitfinex exchange', async () => {
  const prisma = {
    platformSettings: {
      findUnique: async () => null,
    },
  };
  const runtime = new ShowcaseRuntimeService(prisma as never, {} as never);
  const status = await runtime.getCredentialsStatus();

  assert.equal(status.exchangeProvider, 'bitfinex');
  assert.equal(status.botPublicUrl, 'https://doxed-btc-bot.fly.dev');
});

test('legacy showcase provider rows cannot change the Conservative BTC display or config', async () => {
  let writes = 0;
  const prisma = {
    platformSettings: {
      findUnique: async () => ({
        showcaseExchangeProvider: 'bybit',
        showcaseAiProvider: 'deepseek',
      }),
      upsert: async () => {
        writes += 1;
      },
    },
  };
  const runtime = new ShowcaseRuntimeService(prisma as never, {} as never);

  assert.equal((await runtime.getCredentialsStatus()).exchangeProvider, 'bitfinex');
  await assert.rejects(
    runtime.saveShowcaseCredentials('admin', { exchangeProvider: 'bybit' }),
    /locked to Bitfinex/,
  );
  assert.equal(writes, 0);
});

test('admin showcase config rejects non-Bitfinex providers before database mutation', async () => {
  let writes = 0;
  const prisma = {
    platformSettings: {
      upsert: async () => {
        writes += 1;
      },
    },
  };
  const service = new AdminControlService(
    prisma as never,
    {} as never,
    {} as never,
    {} as never,
  );

  await assert.rejects(
    service.updateShowcaseConfig('admin', { exchangeProvider: 'bybit' }),
    /locked to Bitfinex/,
  );
  assert.equal(writes, 0);
});
