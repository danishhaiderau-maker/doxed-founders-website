import assert from 'node:assert/strict';
import test from 'node:test';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { APPLY_FLAG, REVIEWED_MIGRATIONS, loadReviewedMigrations, parseMode, runReviewedRelaySchema, splitPinnedSqlStatements } from './apply-reviewed-relay-schema-20260920.mjs';

const repoRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const rows = Array.from({ length: 25 }, (_, index) => ({ id: `id-${index}`, migration_name: `m-${index}`, checksum: `sum-${index}`, finished_at: new Date(1000 + index) }));
const state = (migrations, extra = {}) => {
  const sourceNames = rows.map((row) => row.migration_name).concat(REVIEWED_MIGRATIONS.map(([name]) => name)).sort();
  const sourceChecksums = new Map(rows.map((row) => [row.migration_name, row.checksum]));
  for (const [name, hash] of REVIEWED_MIGRATIONS) sourceChecksums.set(name, hash);
  migrations.sourceNames = sourceNames;
  migrations.sourceChecksums = sourceChecksums;
  return { ledgerRows: rows, enabledDdlTriggers: [], presentTables: [], enumPresent: false, presentColumns: [], sourceIndexPresent: false, participantIdType: 'text', ...extra };
};

test('pins the exact two reviewed migration files and hashes', async () => {
  const migrations = await loadReviewedMigrations(repoRoot);
  assert.deepEqual(migrations.reviewed.map(({ name, sha256 }) => [name, sha256]), REVIEWED_MIGRATIONS);
  assert.deepEqual(migrations.reviewed.map(({ sql }) => splitPinnedSqlStatements(sql).length), [15, 2]);
});

test('defaults to dry-run and requires the exact apply confirmation', () => {
  assert.equal(parseMode([]), 'dry-run');
  assert.equal(parseMode(['--dry-run']), 'dry-run');
  assert.equal(parseMode([APPLY_FLAG]), 'apply');
  assert.throws(() => parseMode(['--apply']), /usage/);
});

test('dry-run performs no transaction or SQL execution', async () => {
  const migrations = await loadReviewedMigrations(repoRoot);
  const adapter = { inspect: async () => state(migrations), transaction: async () => assert.fail('transaction touched') };
  const result = await runReviewedRelaySchema({ adapter, migrations, mode: 'dry-run' });
  assert.equal(result.mode, 'dry-run');
  assert.equal(result.applied, 25);
  assert.deepEqual(result.pending, REVIEWED_MIGRATIONS.map(([name]) => name).sort());
});

test('apply uses one bounded transaction and only the two pinned SQL payloads', async () => {
  const migrations = await loadReviewedMigrations(repoRoot);
  const calls = [];
  let post = false;
  const adapter = {
    inspect: async () => state(migrations),
    transaction: async (fn, limits) => {
      calls.push(['limits', limits]);
      await fn({
        setTimeouts: async () => calls.push(['timeouts']),
        inspect: async () => state(migrations, post ? { presentTables: ['RelayPositionReductionAudit', 'SignalCycleParticipantReduction'], enumPresent: true, presentColumns: ['platformReceivedAt', 'sourceEventId', 'sourceEventSeq', 'sourcePayloadSha256'], sourceIndexPresent: true } : {}),
        executePinnedSql: async (sql) => { calls.push(['sql', sql]); if (calls.filter(([kind]) => kind === 'sql').length === 2) post = true; },
      });
    },
  };
  await runReviewedRelaySchema({ adapter, migrations, mode: 'apply' });
  assert.deepEqual(calls[0], ['limits', { maxWaitMs: 3000, timeoutMs: 45000 }]);
  assert.equal(calls.filter(([kind]) => kind === 'timeouts').length, 1);
  assert.deepEqual(calls.filter(([kind]) => kind === 'sql').map(([, sql]) => sql), migrations.reviewed.map(({ sql }) => sql));
});

test('preflight rejects schema presence, DDL triggers, and nonexact pending sets', async () => {
  for (const bad of [
    { presentTables: ['RelayPositionReductionAudit'] },
    { enabledDdlTriggers: [{ evtname: 'ddl_watch' }] },
    { participantIdType: 'uuid' },
  ]) {
    const migrations = await loadReviewedMigrations(repoRoot);
    await assert.rejects(runReviewedRelaySchema({ adapter: { inspect: async () => state(migrations, bad) }, migrations, mode: 'dry-run' }));
  }
});

test('preflight rejects failed, rolled-back, unknown, or checksum-divergent ledger rows', async () => {
  for (const mutate of [
    (ledger) => { ledger[0].finished_at = null; },
    (ledger) => { ledger[0].rolled_back_at = new Date(); },
    (ledger) => { ledger[0].migration_name = 'unknown_migration'; },
    (ledger) => { ledger[0].checksum = 'unexpected'; },
  ]) {
    const migrations = await loadReviewedMigrations(repoRoot);
    const snapshot = state(migrations, { ledgerRows: structuredClone(rows) });
    mutate(snapshot.ledgerRows);
    await assert.rejects(runReviewedRelaySchema({ adapter: { inspect: async () => snapshot }, migrations, mode: 'dry-run' }));
  }
});
