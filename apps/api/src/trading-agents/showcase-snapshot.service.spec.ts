import assert from 'node:assert/strict';
import test from 'node:test';
import { BadRequestException, UnauthorizedException } from '@nestjs/common';
import { createHmac } from 'node:crypto';
import { ShowcaseSnapshotService } from './showcase-snapshot.service';

const CONTROL_SECRET = 'control-secret';

function testConfig(extra: Record<string, string> = {}) {
  const values: Record<string, string> = { BOT_CONTROL_SECRET: CONTROL_SECRET, ...extra };
  return { get: (key: string) => values[key] };
}

function canonicalSnapshot(overrides: Record<string, unknown> = {}) {
  return {
    dashboard_owner: true,
    dashboard_port: 7002,
    bot_instance_id: 'dashboard-7002-test',
    source_git_rev: 'dc55f47673ff',
    // FIX 2: canonical Fly declares its public dashboard URL via
    // DASHBOARD_PUBLIC_URL. The lock-enforced snapshot service rejects
    // snapshots whose dashboard_url is not the canonical Fly URL.
    dashboard_url: 'https://doxed-btc-bot.fly.dev/',
    server_ts: new Date().toISOString(),
    ...overrides,
  };
}

function signedBody(
  snapshot: Record<string, unknown>,
  snapshotSeq = Date.now(),
) {
  const snapshotJson = JSON.stringify(
    Object.fromEntries(
      Object.entries(snapshot).sort(([left], [right]) => left.localeCompare(right)),
    ),
  );
  return {
    snapshot_seq: snapshotSeq,
    snapshot,
    snapshot_json: snapshotJson,
    snapshot_hmac: createHmac('sha256', CONTROL_SECRET)
      .update(`${snapshotSeq}.${snapshotJson}`, 'utf8')
      .digest('hex'),
  };
}

function makeService(previous = 0n) {
  let storedSeq = previous;
  let storedSnapshot: unknown = null;
  const config = testConfig();
  const prisma = {
    platformSettings: {
      findUnique: async () => ({
        showcaseRelaySnapshotSeq: storedSeq,
        showcaseRelaySnapshot: storedSnapshot,
        showcaseRelaySnapshotAt: null,
      }),
      upsert: async (args: {
        update: {
          showcaseRelaySnapshotSeq: bigint;
          showcaseRelaySnapshot: unknown;
        };
      }) => {
        storedSeq = args.update.showcaseRelaySnapshotSeq;
        storedSnapshot = args.update.showcaseRelaySnapshot;
        return {};
      },
    },
  };
  return {
    service: new ShowcaseSnapshotService(config as never, prisma as never),
    storedSeq: () => storedSeq,
  };
}

test('accepts a fresh canonical owner with a restart-safe monotonic sequence', async () => {
  const previous = 1_750_000_000_000n;
  const { service, storedSeq } = makeService(previous);
  const next = Number(previous + 100n);
  const result = await service.ingest(signedBody(canonicalSnapshot(), next));
  assert.deepEqual(result, { ok: true, snapshot_seq: next });
  assert.equal(storedSeq(), BigInt(next));
});

test('skips a stale publisher sequence without replacing the cached snapshot', async () => {
  const previous = 1_750_000_000_100n;
  const { service, storedSeq } = makeService(previous);
  const result = await service.ingest(
    signedBody(canonicalSnapshot(), Number(previous - 1n)),
  );
  assert.deepEqual(result, {
    ok: true,
    skipped: true,
    snapshot_seq: Number(previous),
  });
  assert.equal(storedSeq(), previous);
});

test('rejects malformed, stale, and foreign snapshot publishers', async () => {
  const { service } = makeService();
  const cases = [
    signedBody(canonicalSnapshot(), 0),
    signedBody(canonicalSnapshot({ dashboard_port: 7003 })),
    signedBody(
      canonicalSnapshot({
        server_ts: new Date(Date.now() - 121_000).toISOString(),
      }),
    ),
  ];
  for (const body of cases) {
    await assert.rejects(
      () => service.ingest(body),
      (error: unknown) => error instanceof BadRequestException,
    );
  }
});

test('rejects unsigned and tampered snapshots before persistence', async () => {
  const { service } = makeService();
  const unsigned = {
    snapshot_seq: Date.now(),
    snapshot: canonicalSnapshot(),
  };
  await assert.rejects(
    () => service.ingest(unsigned),
    (error: unknown) => error instanceof UnauthorizedException,
  );

  const signed = signedBody(canonicalSnapshot());
  signed.snapshot_json = signed.snapshot_json.replace(
    '"dashboard_port":7002',
    '"dashboard_port":7003',
  );
  await assert.rejects(
    () => service.ingest(signed),
    (error: unknown) => error instanceof UnauthorizedException,
  );
});

// ─────────────────────────────────────────────────────────────────────────────
// FIX 2 — Fly-canonical owner proof at the snapshot ingestion boundary.
// ─────────────────────────────────────────────────────────────────────────────

test('FIX 2: rejects a snapshot whose dashboard_url is not canonical Fly', async () => {
  const { service } = makeService();
  const desktopSnapshot = canonicalSnapshot({
    // A desktop publisher (rogue legacy owner) reports a loopback URL.
    dashboard_url: 'http://127.0.0.1:7002/',
    bot_instance_id: 'dashboard-7002-pid-670-stale',
  });
  await assert.rejects(
    () => service.ingest(signedBody(desktopSnapshot)),
    (error: unknown) =>
      error instanceof BadRequestException
      && /desktop publishers cannot be canonical/i.test(error.message),
  );
});

test('FIX 2: accepts a snapshot whose dashboard_url matches canonical Fly', async () => {
  const { service, storedSeq } = makeService();
  const flySnapshot = canonicalSnapshot({
    dashboard_url: 'https://doxed-btc-bot.fly.dev/',
    bot_instance_id: 'dashboard-7002-pid-1234-fly',
    source_git_rev: '8afc5715c0ab',
  });
  const result = await service.ingest(signedBody(flySnapshot));
  assert.equal(result.ok, true);
  assert.equal(storedSeq() > 0n, true);
});

// ── In-memory snapshot holder: Postgres is restart recovery only ──────────

type FakeRow = {
  showcaseRelaySnapshot: unknown;
  showcaseRelaySnapshotSeq: bigint | null;
  showcaseRelaySnapshotAt: Date | null;
};

const FULL_SELECT = {
  showcaseRelaySnapshot: true,
  showcaseRelaySnapshotSeq: true,
  showcaseRelaySnapshotAt: true,
};
const HEAD_SELECT = { showcaseRelaySnapshotSeq: true, showcaseRelaySnapshotAt: true };

function fakeStore(initial: FakeRow | null = null) {
  let row: FakeRow | null = initial;
  const reads: Array<'full' | 'head'> = [];
  const writes: FakeRow[] = [];
  const prisma = {
    platformSettings: {
      findUnique: async (args: { where: unknown; select: Record<string, boolean> }) => {
        assert.deepEqual(args.where, { id: 'default' });
        if (JSON.stringify(args.select) === JSON.stringify(FULL_SELECT)) reads.push('full');
        else if (JSON.stringify(args.select) === JSON.stringify(HEAD_SELECT)) reads.push('head');
        else assert.fail(`unexpected select ${JSON.stringify(args.select)}`);
        if (!row) return null;
        return args.select.showcaseRelaySnapshot
          ? { ...row }
          : { showcaseRelaySnapshotSeq: row.showcaseRelaySnapshotSeq, showcaseRelaySnapshotAt: row.showcaseRelaySnapshotAt };
      },
      upsert: async (args: { select: unknown; update: FakeRow }) => {
        assert.deepEqual(args.select, { id: true });
        row = { ...args.update };
        writes.push(row);
        return { id: 'default' };
      },
    },
  };
  return {
    prisma,
    reads,
    writes,
    setRow: (next: FakeRow | null) => {
      row = next;
    },
  };
}

function manualClock(start = Date.now()) {
  let now = start;
  return { now: () => now, advance: (ms: number) => { now += ms; } };
}

test('ingest keeps the snapshot in memory and serves reads without touching Postgres', async () => {
  const store = fakeStore();
  const clock = manualClock();
  const service = new ShowcaseSnapshotService(testConfig() as never, store.prisma as never, clock.now);
  const snapshot = canonicalSnapshot({ positions: [{ quantity: '0.00004' }] });
  await service.ingest(signedBody(snapshot, 1_000));
  const readsAfterIngest = store.reads.length;
  const first = await service.getCachedSnapshot();
  const second = await service.getCachedSnapshot();
  assert.equal(store.reads.length, readsAfterIngest, 'reads must be served from memory');
  assert.deepEqual(first.snapshot, snapshot);
  assert.equal(first.snapshot_seq, 1_000);
  assert.equal(first.at?.getTime(), clock.now());
  ((first.snapshot as unknown as { positions: unknown[] }).positions).push('mutated');
  assert.deepEqual(second.snapshot, snapshot, 'callers get independent copies');
  assert.deepEqual((await service.getCachedSnapshot()).snapshot, snapshot);
});

test('Postgres persistence is throttled to at most once per 60 s', async () => {
  const store = fakeStore();
  const clock = manualClock();
  const service = new ShowcaseSnapshotService(testConfig() as never, store.prisma as never, clock.now);
  let seq = 10_000;
  await service.ingest(signedBody(canonicalSnapshot(), ++seq));
  assert.equal(store.writes.length, 1, 'first push after boot persists for recovery');
  for (let i = 0; i < 29; i += 1) {
    clock.advance(2_000);
    await service.ingest(signedBody(canonicalSnapshot(), ++seq));
  }
  assert.equal(store.writes.length, 1, '2 s pushes inside 60 s do not write');
  clock.advance(2_000);
  await service.ingest(signedBody(canonicalSnapshot(), ++seq));
  assert.equal(store.writes.length, 2);
  assert.equal(store.writes[1].showcaseRelaySnapshotSeq, BigInt(seq));
  assert.equal(store.reads.filter((r) => r === 'full').length, 1, 'only the one boot read');
});

test('a sub-60 s SHOWCASE_SNAPSHOT_PERSIST_MS is clamped to 60 s', async () => {
  const store = fakeStore();
  const clock = manualClock();
  const service = new ShowcaseSnapshotService(
    testConfig({ SHOWCASE_SNAPSHOT_PERSIST_MS: '2000' }) as never,
    store.prisma as never,
    clock.now,
  );
  await service.ingest(signedBody(canonicalSnapshot(), 1));
  clock.advance(10_000);
  await service.ingest(signedBody(canonicalSnapshot(), 2));
  assert.equal(store.writes.length, 1);
});

test('restart recovery loads the persisted snapshot with its original timestamp and keeps seq monotonic', async () => {
  const persistedAt = new Date(Date.now() - 5 * 60_000);
  const persisted = canonicalSnapshot({ restored: true });
  const store = fakeStore({
    showcaseRelaySnapshot: persisted,
    showcaseRelaySnapshotSeq: 5_000n,
    showcaseRelaySnapshotAt: persistedAt,
  });
  const service = new ShowcaseSnapshotService(testConfig() as never, store.prisma as never);
  const restored = await service.getCachedSnapshot();
  assert.deepEqual(restored.snapshot, persisted);
  assert.equal(restored.snapshot_seq, 5_000);
  assert.equal(restored.at?.getTime(), persistedAt.getTime(), 'restored copy must look stale, not fresh');

  assert.deepEqual(await service.ingest(signedBody(canonicalSnapshot(), 4_999)), {
    ok: true, skipped: true, snapshot_seq: 5_000,
  });
  assert.equal(store.writes.length, 0);
  assert.deepEqual(await service.ingest(signedBody(canonicalSnapshot(), 5_001)), {
    ok: true, snapshot_seq: 5_001,
  });
  assert.equal((await service.getCachedSnapshot()).snapshot_seq, 5_001);
});

test('restart with no persisted row returns no snapshot', async () => {
  const store = fakeStore(null);
  const service = new ShowcaseSnapshotService(testConfig() as never, store.prisma as never);
  assert.deepEqual(await service.getCachedSnapshot(), { snapshot: null, snapshot_seq: 0, at: null });
});

test('non-receiving readers fetch the blob only when the persisted seq changes', async () => {
  const at = new Date('2026-09-05T06:00:00Z');
  const store = fakeStore({
    showcaseRelaySnapshot: canonicalSnapshot({ v: 1 }),
    showcaseRelaySnapshotSeq: 42n,
    showcaseRelaySnapshotAt: at,
  });
  const service = new ShowcaseSnapshotService(testConfig() as never, store.prisma as never);
  assert.equal((await service.getCachedSnapshot()).snapshot?.v, 1);
  assert.equal((await service.getCachedSnapshot()).snapshot?.v, 1);
  assert.equal((await service.getCachedSnapshot()).snapshot?.v, 1);
  assert.deepEqual(store.reads, ['head', 'full', 'head', 'head']);

  store.setRow({
    showcaseRelaySnapshot: canonicalSnapshot({ v: 2 }),
    showcaseRelaySnapshotSeq: 43n,
    showcaseRelaySnapshotAt: at,
  });
  assert.equal((await service.getCachedSnapshot()).snapshot?.v, 2);
  assert.deepEqual(store.reads.slice(4), ['head', 'full']);
  store.setRow({ showcaseRelaySnapshot: [], showcaseRelaySnapshotSeq: 44n, showcaseRelaySnapshotAt: at });
  assert.deepEqual(await service.getCachedSnapshot(), { snapshot: null, snapshot_seq: 44, at });
});

test('worker pulls from the API peer, sends since_seq, and reuses its copy when unchanged', async () => {
  const store = fakeStore();
  const clock = manualClock();
  const originalFetch = globalThis.fetch;
  const urls: string[] = [];
  const at = new Date(clock.now()).toISOString();
  const snapshot = canonicalSnapshot({ from: 'peer' });
  globalThis.fetch = (async (input: string | URL, init?: RequestInit) => {
    urls.push(String(input));
    assert.equal(new Headers(init?.headers).get('X-Bot-Control-Secret'), CONTROL_SECRET);
    const body = String(input).includes('since_seq=77')
      ? { unchanged: true, snapshot_seq: 77, at }
      : { snapshot, snapshot_seq: 77, at };
    return new Response(JSON.stringify(body), { status: 200 });
  }) as typeof fetch;
  try {
    const service = new ShowcaseSnapshotService(
      testConfig({ SHOWCASE_SNAPSHOT_PEER_URL: 'http://api.railway.internal:8080/' }) as never,
      store.prisma as never,
      clock.now,
    );
    const first = await service.getCachedSnapshot();
    assert.deepEqual(first.snapshot, snapshot);
    assert.equal(first.at?.toISOString(), at);
    await service.getCachedSnapshot();
    assert.equal(urls.length, 1, 'calls inside 500 ms are coalesced');
    clock.advance(1_000);
    const second = await service.getCachedSnapshot();
    assert.deepEqual(second.snapshot, snapshot);
    assert.deepEqual(urls, [
      'http://api.railway.internal:8080/api/internal/showcase-snapshot/latest',
      'http://api.railway.internal:8080/api/internal/showcase-snapshot/latest?since_seq=77',
    ]);
    assert.deepEqual(store.reads, [], 'peer path never touches Postgres');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('worker falls back to the seq-gated persisted copy when the peer is down', async () => {
  const persistedAt = new Date(Date.now() - 90_000);
  const store = fakeStore({
    showcaseRelaySnapshot: canonicalSnapshot(),
    showcaseRelaySnapshotSeq: 9n,
    showcaseRelaySnapshotAt: persistedAt,
  });
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => {
    throw new Error('ECONNREFUSED');
  }) as typeof fetch;
  try {
    const service = new ShowcaseSnapshotService(
      testConfig({ SHOWCASE_SNAPSHOT_PEER_URL: 'http://api.railway.internal:8080' }) as never,
      store.prisma as never,
    );
    const result = await service.getCachedSnapshot();
    assert.equal(result.snapshot_seq, 9);
    assert.equal(result.at?.getTime(), persistedAt.getTime());
    assert.deepEqual(store.reads, ['head', 'full']);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('peer endpoint returns unchanged for a matching since_seq and the full snapshot otherwise', async () => {
  const store = fakeStore();
  const service = new ShowcaseSnapshotService(testConfig() as never, store.prisma as never);
  await service.ingest(signedBody(canonicalSnapshot(), 300));
  const unchanged = await service.getLatestForPeer(300);
  assert.equal(unchanged.unchanged, true);
  assert.equal(unchanged.snapshot_seq, 300);
  const full = await service.getLatestForPeer(299);
  assert.equal('snapshot' in full && full.snapshot?.dashboard_port, 7002);
  assert.equal(typeof full.at, 'string');
});

test('a failed persist does not fail the push and retries on the next one', async () => {
  let failNext = true;
  let writes = 0;
  const prisma = {
    platformSettings: {
      findUnique: async () => null,
      upsert: async () => {
        writes += 1;
        if (failNext) {
          failNext = false;
          throw new Error('P1001 connection refused');
        }
        return { id: 'default' };
      },
    },
  };
  const service = new ShowcaseSnapshotService(testConfig() as never, prisma as never);
  assert.deepEqual(await service.ingest(signedBody(canonicalSnapshot(), 1)), { ok: true, snapshot_seq: 1 });
  assert.deepEqual(await service.ingest(signedBody(canonicalSnapshot(), 2)), { ok: true, snapshot_seq: 2 });
  assert.equal(writes, 2);
  assert.equal((await service.getCachedSnapshot()).snapshot_seq, 2);
});
