import assert from 'node:assert/strict';
import test from 'node:test';
import { BotBridgeService } from './bot-bridge.service';

const canonicalState = {
  dashboard_owner: true,
  bot_instance_id: 'dashboard-7002-test-owner',
  dashboard_pid: 1234,
  dashboard_port: 7002,
  price: 64_000,
};

function makeBridge() {
  const config = {
    get: () => undefined,
  };
  const prisma = {
    platformSettings: {
      findUnique: async () => ({
        showcaseBotPublicUrl: 'https://showcase.test',
      }),
    },
  };
  const snapshots = {
    getCachedSnapshot: async () => ({
      snapshot: null,
      at: null,
      snapshot_seq: null,
    }),
  };
  return new BotBridgeService(config as never, prisma as never, snapshots as never);
}

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
