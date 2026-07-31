import assert from 'node:assert/strict';
import test from 'node:test';
import { BotBridgeService } from './bot-bridge.service';

const canonicalState = {
  dashboard_owner: true,
  bot_instance_id: 'dashboard-7002-test-owner',
  dashboard_pid: 1234,
  dashboard_port: 7002,
  source_git_rev: 'dc55f47673ff',
  server_ts: new Date().toISOString(),
  price: 64_000,
};

function makeBridge(snapshot: Record<string, unknown> | null = null, at: Date | null = null) {
  const config = {
    get: () => undefined,
  };
  const snapshots = {
    getCachedSnapshot: async () => ({
      snapshot,
      at,
      snapshot_seq: snapshot ? Date.now() : null,
    }),
  };
  return new BotBridgeService(config as never, snapshots as never);
}

test('locks every bridge route to the canonical Fly owner', async () => {
  const config = {
    get: (name: string) =>
      name === 'TRADING_AGENT_BOT_URL'
        ? 'https://retired-railway-or-cloudflare.example'
        : undefined,
  };
  const snapshots = {
    getCachedSnapshot: async () => ({ snapshot: null, at: null, snapshot_seq: null }),
  };
  const bridge = new BotBridgeService(config as never, snapshots as never);

  assert.equal(bridge.getBotUrl(), 'https://doxed-btc-bot.fly.dev');
  assert.equal(await bridge.resolveBotUrl(), 'https://doxed-btc-bot.fly.dev');
  assert.equal(await bridge.resolveShowcaseUrl(), 'https://doxed-btc-bot.fly.dev');
  assert.equal(await bridge.isEnabledAsync(), true);
  assert.equal(bridge.isEnabled(), true);
});

test('requires a recent direct Fly proof before trusting a pushed snapshot', async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response(JSON.stringify(canonicalState), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };

  try {
    const pushed = {
      ...canonicalState,
      server_ts: new Date().toISOString(),
    };
    const unverifiedBridge = makeBridge(pushed, new Date());
    const directState = await unverifiedBridge.fetchStateForExecution(true);
    assert.equal(directState?.bot_instance_id, canonicalState.bot_instance_id);
    assert.equal(directState?.snapshot_source, undefined);
    assert.equal(calls, 1, 'an unverified snapshot must not bypass the fixed Fly URL');

    const verifiedBridge = makeBridge(pushed, new Date());
    const proofRecorder = verifiedBridge as unknown as {
      recordDirectFlyOwnerProof(state: typeof canonicalState): boolean;
    };
    assert.equal(proofRecorder.recordDirectFlyOwnerProof(canonicalState), true);
    const pushedState = await verifiedBridge.fetchStateForExecution(true);
    assert.equal(pushedState?.snapshot_source, 'railway_cache');
    assert.equal(calls, 1, 'matching pushed state may be used after direct proof');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('rejects stale or foreign outbound snapshots and fails closed when tunnel is down', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response('tunnel down', { status: 502 });

  try {
    for (const pushed of [
      {
        ...canonicalState,
        server_ts: new Date(Date.now() - 20_000).toISOString(),
      },
      {
        ...canonicalState,
        dashboard_port: 7003,
        server_ts: new Date().toISOString(),
      },
    ]) {
      const bridge = makeBridge(pushed, new Date());
      assert.equal(await bridge.fetchStateForExecution(true), null);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('coalesces concurrent forced execution fetches into one tunnel request', async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    await new Promise((resolve) => setTimeout(resolve, 20));
    return new Response(JSON.stringify(canonicalState), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };

  try {
    const bridge = makeBridge();
    const results = await Promise.all(
      Array.from({ length: 25 }, () => bridge.fetchStateForExecution(true)),
    );
    assert.equal(calls, 1);
    assert.equal(results.every((state) => state?.bot_instance_id === canonicalState.bot_instance_id), true);

    await bridge.fetchStateForExecution(true);
    assert.equal(calls, 1, 'force must not bypass the short execution cache');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('keeps slow Agent Hub fetches from blocking the execution lane', async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    await new Promise((resolve) => setTimeout(resolve, 20));
    return new Response(JSON.stringify(canonicalState), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };

  try {
    const bridge = makeBridge();
    const [publicState, executionState] = await Promise.all([
      bridge.fetchPublicShowcaseState(true),
      bridge.fetchStateForExecution(true),
    ]);
    assert.equal(calls, 2);
    assert.equal(publicState?.bot_instance_id, canonicalState.bot_instance_id);
    assert.equal(executionState?.bot_instance_id, canonicalState.bot_instance_id);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('returns only a fresh canonical owner from the synchronous execution cache', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(JSON.stringify(canonicalState), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });

  try {
    const bridge = makeBridge();
    assert.equal(bridge.getCachedExecutionState(), null);
    await bridge.fetchStateForExecution(true);
    assert.equal(
      bridge.getCachedExecutionState()?.bot_instance_id,
      canonicalState.bot_instance_id,
    );
    assert.equal(bridge.getCachedExecutionState(-1), null);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('backs off after HTTP 429 instead of falling through into another request', async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response('rate limited', {
      status: 429,
      headers: { 'retry-after': '10' },
    });
  };

  try {
    const bridge = makeBridge();
    const first = await Promise.all(
      Array.from({ length: 20 }, () => bridge.fetchStateForExecution(true)),
    );
    assert.equal(first.every((state) => state === null), true);
    assert.equal(calls, 1);

    assert.equal(await bridge.fetchStateForExecution(true), null);
    assert.equal(calls, 1, 'backoff must suppress immediate retry storms');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('execution polling fails closed after bounded execution and rolling-deploy relay fallbacks', async () => {
  const originalFetch = globalThis.fetch;
  const urls: string[] = [];
  globalThis.fetch = async (input) => {
    urls.push(String(input));
    return new Response('temporary failure', { status: 502 });
  };

  try {
    const bridge = makeBridge();
    assert.equal(await bridge.fetchStateForExecution(true), null);
    assert.deepEqual(urls, [
      'https://doxed-btc-bot.fly.dev/api/relay-execution-state',
      'https://doxed-btc-bot.fly.dev/api/relay-state',
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('canonical control posts require and send the dedicated bot admin token', async () => {
  const originalFetch = globalThis.fetch;
  let capturedHeaders: Headers | null = null;
  globalThis.fetch = async (_input, init) => {
    capturedHeaders = new Headers(init?.headers);
    return new Response(JSON.stringify({ execution_paused: true }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };

  try {
    const config = {
      get: (name: string) =>
        name === 'BOT_ADMIN_TOKEN'
          ? 'admin-only-token'
          : name === 'BOT_CONTROL_SECRET'
            ? 'publisher-secret'
            : undefined,
    };
    const snapshots = {
      getCachedSnapshot: async () => ({
        snapshot: null,
        at: null,
        snapshot_seq: null,
      }),
    };
    const bridge = new BotBridgeService(config as never, snapshots as never);
    const result = await bridge.proxyBotPost('/api/pause');
    assert.equal(result.ok, true);
    assert.ok(capturedHeaders);
    const sentHeaders = capturedHeaders as Headers;
    assert.equal(sentHeaders.get('X-Bot-Admin-Token'), 'admin-only-token');
    assert.equal(sentHeaders.get('X-Bot-Control-Secret'), 'publisher-secret');

    const unconfigured = makeBridge();
    const blocked = await unconfigured.proxyBotPost('/api/pause');
    assert.equal(blocked.ok, false);
    assert.match(String(blocked.error), /BOT_ADMIN_TOKEN/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
