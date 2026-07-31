import assert from 'node:assert/strict';
import test from 'node:test';
import { BadRequestException, UnauthorizedException } from '@nestjs/common';
import { createHmac } from 'node:crypto';
import { ShowcaseSnapshotService } from './showcase-snapshot.service';

const CONTROL_SECRET = 'control-secret';

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
  const config = { get: () => CONTROL_SECRET };
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
