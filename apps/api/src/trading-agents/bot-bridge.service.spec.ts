import assert from 'node:assert/strict';
import test from 'node:test';
import { BotBridgeService } from './bot-bridge.service';

const canonicalState = {
  dashboard_owner: true,
  bot_instance_id: 'dashboard-7002-test-owner',
  dashboard_pid: 1234,
  dashboard_port: 7002,
  source_git_rev: 'dc55f47673ff',
  // FIX 2: canonical Fly declares its public dashboard URL via
  // DASHBOARD_PUBLIC_URL in /api/state. A desktop process would report a
  // loopback/LAN URL; the lock-enforced bridge rejects non-Fly URLs.
  dashboard_url: 'https://doxed-btc-bot.fly.dev/',
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

test('canonical cumulative-state fallback authenticates to protected Fly /api/state', async () => {
  const originalFetch = globalThis.fetch;
  let capturedHeaders: Headers | null = null;
  globalThis.fetch = async (_input, init) => {
    capturedHeaders = new Headers(init?.headers);
    return new Response(JSON.stringify(canonicalState), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };

  try {
    const config = {
      get: (name: string) => name === 'BOT_ADMIN_TOKEN' ? 'fly-admin-token' : undefined,
    };
    const snapshots = {
      getCachedSnapshot: async () => ({ snapshot: null, at: null, snapshot_seq: null }),
    };
    const bridge = new BotBridgeService(config as never, snapshots as never);
    const state = await bridge.fetchShowcaseCanonicalState(true);
    assert.equal(state?.bot_instance_id, canonicalState.bot_instance_id);
    assert.ok(capturedHeaders);
    assert.equal(
      (capturedHeaders as Headers).get('X-Bot-Admin-Token'),
      'fly-admin-token',
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('cumulative metrics bypass the slim Railway snapshot and fetch full Fly state', async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  const fullState = {
    ...canonicalState,
    account_balance: 512.5,
    session_pnl_usd: 12.5,
    trade_count_session: 7,
    analytics: { total_trades: 7, win_rate: 71.4 },
  };
  globalThis.fetch = async () => {
    calls += 1;
    return new Response(JSON.stringify(fullState), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };

  try {
    const slimSnapshot = {
      ...canonicalState,
      server_ts: new Date().toISOString(),
      snapshot_source: 'railway_cache',
    };
    const config = {
      get: (name: string) => name === 'BOT_ADMIN_TOKEN' ? 'fly-admin-token' : undefined,
    };
    const snapshots = {
      getCachedSnapshot: async () => ({
        snapshot: slimSnapshot,
        at: new Date(),
        snapshot_seq: Date.now(),
      }),
    };
    const bridge = new BotBridgeService(config as never, snapshots as never);
    const metrics = await bridge.fetchCumulativeSessionMetrics();

    assert.equal(calls, 1, 'full-session analytics must make a direct Fly request');
    assert.equal(metrics?.current_balance, 512.5);
    assert.equal(metrics?.total_pnl_usd, 12.5);
    assert.equal(metrics?.trade_count, 7);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('cumulative metrics derive win rate from aggregate wins and losses when the bot field is absent', async () => {
  const originalFetch = globalThis.fetch;
  const fullState = {
    ...canonicalState,
    account_balance: 491.15,
    session_pnl_usd: -8.85,
    trade_count_session: 20,
    analytics: { total_trades: 20, wins: 9, losses: 11 },
  };
  globalThis.fetch = async () => new Response(JSON.stringify(fullState), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });

  try {
    const config = {
      get: (name: string) => name === 'BOT_ADMIN_TOKEN' ? 'fly-admin-token' : undefined,
    };
    const snapshots = {
      getCachedSnapshot: async () => ({ snapshot: null, at: null, snapshot_seq: null }),
    };
    const bridge = new BotBridgeService(config as never, snapshots as never);
    const metrics = await bridge.fetchCumulativeSessionMetrics();

    assert.equal(metrics?.trade_count, 20);
    assert.equal(metrics?.win_rate, 45);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

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

test('isFlyHealthReachable succeeds on lightweight probe without falling through to /api/state', async () => {
  const originalFetch = globalThis.fetch;
  const urls: string[] = [];
  globalThis.fetch = async (input) => {
    const url = String(input);
    urls.push(url);
    // Lightweight /api/ping succeeds — mirrors the case where Fly is healthy
    // enough to answer health probes but the dashboard's heavy /api/state
    // fetch could still time out under intermittent cross-region latency.
    if (url.endsWith('/api/ping') || url.endsWith('/health')) {
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }
    return new Response('not found', { status: 404 });
  };

  try {
    const bridge = makeBridge();
    const reachable = await bridge.isFlyHealthReachable();
    assert.equal(reachable, true);
    // Critical: the lightweight probe must NEVER trigger a heavy /api/state
    // fetch — that was the original cause of the dashboard-vs-bot-health
    // contradiction (slow state fetch flapped while fast probe stayed green).
    assert.ok(
      urls.every((u) => !u.includes('/api/state') && !u.includes('/api/relay')),
      `unexpected heavy fetch: ${urls.join(', ')}`,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('isFlyHealthReachable returns false when both /api/ping and /health fail', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response('gateway timeout', { status: 504 });

  try {
    const bridge = makeBridge();
    const reachable = await bridge.isFlyHealthReachable();
    assert.equal(reachable, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// FIX 2 — Fly-canonical owner proof. Desktop 7002 must never be canonical.
// ─────────────────────────────────────────────────────────────────────────────

test('FIX 2: rejects a desktop-shaped direct owner proof when the lock is enforced', () => {
  // Mirror the runtime check performed by `recordDirectFlyOwnerProof`
  // when FLY_CANONICAL_LOCK_ENFORCED is true (the committed
  // config/fly-canonical.lock.json sets desktopBotEnabled=false).
  const desktopState = {
    ...canonicalState,
    // A rogue desktop process reports a loopback URL, not the Fly URL.
    dashboard_url: 'http://127.0.0.1:7002/',
    bot_instance_id: 'dashboard-7002-pid-670-deadbeef',
  };
  const bridge = makeBridge();
  const recorder = bridge as unknown as {
    recordDirectFlyOwnerProof(state: typeof desktopState): boolean;
  };
  // The desktop dashboard_url fails isFlyDeclaredDashboardUrl regardless
  // of the rest of the owner metadata, so the proof is rejected.
  assert.equal(recorder.recordDirectFlyOwnerProof(desktopState), false);
  // No cached identity is published for a rejected proof.
  assert.equal(bridge.getCachedDashboardOwnerIdentity(), null);
});

test('FIX 2: accepts a Fly-shaped direct owner proof when the lock is enforced', () => {
  const flyState = {
    ...canonicalState,
    dashboard_url: 'https://doxed-btc-bot.fly.dev/',
    bot_instance_id: 'dashboard-7002-pid-1234-feedface',
    source_git_rev: '8afc5715c0ab',
  };
  const bridge = makeBridge();
  const recorder = bridge as unknown as {
    recordDirectFlyOwnerProof(state: typeof flyState): boolean;
  };
  assert.equal(recorder.recordDirectFlyOwnerProof(flyState), true);
  const identity = bridge.getCachedDashboardOwnerIdentity();
  assert.equal(identity?.instanceId, flyState.bot_instance_id);
  assert.equal(identity?.port, 7002);
});

test('FIX 2: evicts a stale direct owner proof on the next read after TTL', async () => {
  // A successful direct proof establishes the cached identity...
  const bridge = makeBridge();
  const recorder = bridge as unknown as {
    recordDirectFlyOwnerProof(state: typeof canonicalState): boolean;
    directFlyOwnerProof: { seenAt: number } | null;
  };
  assert.equal(recorder.recordDirectFlyOwnerProof(canonicalState), true);
  assert.equal(bridge.getCachedDashboardOwnerIdentity()?.instanceId, canonicalState.bot_instance_id);

  // Simulate the 60s TTL elapsing by rewinding the recorded seenAt. The
  // next read must treat the record as stale and return null (fail closed).
  // This is the eviction contract that prevents a stale desktop-pid-*
  // claim from authorizing relay events after the real Fly owner changes.
  recorder.directFlyOwnerProof!.seenAt = Date.now() - 61_000;
  assert.equal(bridge.getCachedDashboardOwnerIdentity(), null);

  // And the explicit eviction helper clears the field outright.
  bridge.evictStaleDirectFlyOwnerProof();
  assert.equal(recorder.directFlyOwnerProof, null);
});

test('FIX 2: rejects a desktop-mirrored direct fetch when the lock is enforced', async () => {
  const originalFetch = globalThis.fetch;
  // The desktop :7002 proxy (scripts/fly-dashboard-proxy.py) adds
  // `X-Desktop-Mirror: fly` to every response. Even if the body looks
  // like a canonical Fly payload (because the proxy is forwarding Fly's
  // state), the lock-enforced bridge must reject it as a direct owner
  // proof source — only Fly itself may establish that proof.
  globalThis.fetch = async () =>
    new Response(JSON.stringify(canonicalState), {
      status: 200,
      headers: {
        'content-type': 'application/json',
        'x-desktop-mirror': 'fly',
      },
    });

  try {
    const bridge = makeBridge();
    const state = await bridge.fetchStateForExecution(true);
    // Owner proof was refused, so execution must hold (null return).
    assert.equal(state, null);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('FIX 2: requires the source-controlled lock to be enforced before applying the desktop guard', () => {
  // This is a static contract assertion, not a runtime bypass. The lock
  // file is read once at module load; if it's missing or not frozen,
  // FLY_CANONICAL_LOCK_ENFORCED is false and the desktop_url check is
  // skipped (legacy behavior). The committed lock in this repo IS
  // enforced, which is exactly what blocks a stale desktop publisher.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const lockModule = require('./fly-canonical-lock');
  assert.equal(lockModule.FLY_CANONICAL_LOCK_ENFORCED, true);
  assert.equal(
    lockModule.isFlyDeclaredDashboardUrl('https://doxed-btc-bot.fly.dev/'),
    true,
  );
  assert.equal(
    lockModule.isFlyDeclaredDashboardUrl('http://127.0.0.1:7002/'),
    false,
  );
  assert.equal(
    lockModule.isFlyDeclaredDashboardUrl('https://evil.example/'),
    false,
  );
  assert.equal(lockModule.isFlyDeclaredDashboardUrl(undefined), false);
});
