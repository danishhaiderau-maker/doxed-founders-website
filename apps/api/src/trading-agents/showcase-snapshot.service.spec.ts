import assert from 'node:assert/strict';
import test from 'node:test';
import { BadRequestException } from '@nestjs/common';
import { ShowcaseSnapshotService } from './showcase-snapshot.service';

function canonicalSnapshot(overrides: Record<string, unknown> = {}) {
  return {
    dashboard_owner: true,
    dashboard_port: 7002,
    bot_instance_id: 'dashboard-7002-test',
    source_git_rev: 'dc55f47673ff',
    server_ts: new Date().toISOString(),
    ...overrides,
  };
}

function makeService(previous = 0n) {
  let storedSeq = previous;
  let storedSnapshot: unknown = null;
  const config = { get: () => 'control-secret' };
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
  const result = await service.ingest({
    snapshot_seq: next,
    snapshot: canonicalSnapshot(),
  });
  assert.deepEqual(result, { ok: true, snapshot_seq: next });
  assert.equal(storedSeq(), BigInt(next));
});

test('skips a stale publisher sequence without replacing the cached snapshot', async () => {
  const previous = 1_750_000_000_100n;
  const { service, storedSeq } = makeService(previous);
  const result = await service.ingest({
    snapshot_seq: Number(previous - 1n),
    snapshot: canonicalSnapshot(),
  });
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
    { snapshot_seq: 0, snapshot: canonicalSnapshot() },
    {
      snapshot_seq: Date.now(),
      snapshot: canonicalSnapshot({ dashboard_port: 7003 }),
    },
    {
      snapshot_seq: Date.now(),
      snapshot: canonicalSnapshot({
        server_ts: new Date(Date.now() - 121_000).toISOString(),
      }),
    },
  ];
  for (const body of cases) {
    await assert.rejects(
      () => service.ingest(body),
      (error: unknown) => error instanceof BadRequestException,
    );
  }
});
