import { createHash } from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const APPLY_FLAG = '--apply-reviewed-relay-schema-20260920';
export const REVIEWED_MIGRATIONS = Object.freeze([
  ['20260824120000_relay_position_reduction_audit', '5b66b57077eb0d0c6848627563ccad6ab59a951170faf7f2985481fbb0548b1e'],
  ['20260903090000_signal_cycle_source_event_ack', '2b8272f05cd7fd423214d2dd194f18c76bc1787192b7f86587c563a09f7297f6'],
]);
const ALLOWED_HISTORICAL_MISMATCH = '20260711220000_founder_economics_mvp';
const EXPECTED_APPLIED = 25;
const EXPECTED_TABLES = ['RelayPositionReductionAudit', 'SignalCycleParticipantReduction'];
const EXPECTED_COLUMNS = ['platformReceivedAt', 'sourceEventId', 'sourceEventSeq', 'sourcePayloadSha256'];
const EXPECTED_ENUM = 'ParticipantReductionPhase';
const EXPECTED_INDEX = 'SignalCycleEvent_sourceEventId_key';

const sha256 = (value) => createHash('sha256').update(value).digest('hex');
export const splitPinnedSqlStatements = (sql) => sql.split(';').map((statement) => statement.trim()).filter(Boolean);
const canonicalRows = (rows) => rows.map((row) => ({
  id: String(row.id),
  migration_name: String(row.migration_name),
  checksum: String(row.checksum),
  finished_at: row.finished_at instanceof Date ? row.finished_at.toISOString() : String(row.finished_at),
})).sort((a, b) => a.migration_name.localeCompare(b.migration_name));
export const ledgerFingerprint = (rows) => sha256(JSON.stringify(canonicalRows(rows)));

export function parseMode(argv) {
  if (argv.length === 0 || (argv.length === 1 && argv[0] === '--dry-run')) return 'dry-run';
  if (argv.length === 1 && argv[0] === APPLY_FLAG) return 'apply';
  throw new Error(`usage: node scripts/apply-reviewed-relay-schema-20260920.mjs [--dry-run|${APPLY_FLAG}]`);
}

export async function loadReviewedMigrations(repoRoot) {
  const migrationRoot = join(repoRoot, 'prisma', 'migrations');
  const sourceNames = (await readdir(migrationRoot, { withFileTypes: true }))
    .filter((entry) => entry.isDirectory()).map((entry) => entry.name).sort();
  const sourceChecksums = new Map();
  for (const name of sourceNames) {
    const bytes = await readFile(join(migrationRoot, name, 'migration.sql'));
    sourceChecksums.set(name, sha256(bytes));
  }
  const reviewed = [];
  for (const [name, expectedSha256] of REVIEWED_MIGRATIONS) {
    const sql = await readFile(join(migrationRoot, name, 'migration.sql'));
    const actualSha256 = sha256(sql);
    if (actualSha256 !== expectedSha256) throw new Error(`reviewed migration hash mismatch: ${name}`);
    reviewed.push({ name, sha256: actualSha256, sql: sql.toString('utf8') });
  }
  return { reviewed, sourceNames, sourceChecksums };
}

export function validatePreflight(state, migrations) {
  const rawRows = state.ledgerRows ?? [];
  const rows = canonicalRows(state.ledgerRows ?? []);
  if (
    rows.length !== EXPECTED_APPLIED
    || rawRows.some((row) => !row.id || !row.checksum || row.finished_at == null || row.rolled_back_at != null)
  ) {
    throw new Error(`migration ledger must contain exactly ${EXPECTED_APPLIED} finished rows`);
  }
  const applied = new Set(rows.map((row) => row.migration_name));
  const unknown = rows.filter((row) => !migrations.sourceChecksums.has(row.migration_name));
  if (unknown.length) throw new Error('migration ledger contains unknown rows');
  const mismatches = rows.filter((row) => migrations.sourceChecksums.get(row.migration_name) !== row.checksum);
  if (mismatches.some((row) => row.migration_name !== ALLOWED_HISTORICAL_MISMATCH)) {
    throw new Error('migration ledger contains an unreviewed checksum mismatch');
  }
  const pending = migrations.sourceNames.filter((name) => !applied.has(name));
  if (JSON.stringify(pending) !== JSON.stringify(REVIEWED_MIGRATIONS.map(([name]) => name).sort())) {
    throw new Error('pending migrations are not the exact reviewed pair');
  }
  if ((state.enabledDdlTriggers ?? []).length) throw new Error('enabled DDL event triggers are not allowed');
  if ((state.presentTables ?? []).length || state.enumPresent || (state.presentColumns ?? []).length || state.sourceIndexPresent) {
    throw new Error('reviewed additive schema is not fully absent');
  }
  if (state.participantIdType !== 'text') throw new Error('SignalCycleParticipant.id is not text');
  return { applied: rows.length, pending, ledgerFingerprint: ledgerFingerprint(rows), historicalMismatch: mismatches.map((row) => row.migration_name) };
}

export function validatePostSchema(state, expectedLedgerFingerprint) {
  if (ledgerFingerprint(state.ledgerRows ?? []) !== expectedLedgerFingerprint) throw new Error('migration ledger changed during DDL');
  if (JSON.stringify([...(state.presentTables ?? [])].sort()) !== JSON.stringify([...EXPECTED_TABLES].sort())) throw new Error('reviewed tables were not created');
  if (!state.enumPresent) throw new Error('reviewed enum was not created');
  if (JSON.stringify([...(state.presentColumns ?? [])].sort()) !== JSON.stringify([...EXPECTED_COLUMNS].sort())) throw new Error('reviewed columns were not created');
  if (!state.sourceIndexPresent) throw new Error('reviewed source event index was not created');
}

export async function runReviewedRelaySchema({ adapter, migrations, mode }) {
  if (mode !== 'dry-run' && mode !== 'apply') throw new Error('invalid execution mode');
  const outer = await adapter.inspect();
  const preflight = validatePreflight(outer, migrations);
  if (mode === 'dry-run') return { ok: true, mode, ...preflight, reviewedHashes: migrations.reviewed.map(({ name, sha256 }) => ({ name, sha256 })) };
  await adapter.transaction(async (tx) => {
    await tx.setTimeouts();
    const inside = await tx.inspect();
    const repeated = validatePreflight(inside, migrations);
    if (repeated.ledgerFingerprint !== preflight.ledgerFingerprint) throw new Error('migration ledger changed before DDL');
    for (const migration of migrations.reviewed) await tx.executePinnedSql(migration.sql);
    validatePostSchema(await tx.inspect(), preflight.ledgerFingerprint);
  }, { maxWaitMs: 3000, timeoutMs: 45000 });
  return { ok: true, mode, ledgerFingerprint: preflight.ledgerFingerprint, reviewedHashes: migrations.reviewed.map(({ name, sha256 }) => ({ name, sha256 })) };
}

async function inspectDb(db) {
  const ledgerRows = await db.$queryRawUnsafe('SELECT id, migration_name, checksum, finished_at, rolled_back_at FROM "_prisma_migrations" ORDER BY migration_name');
  const enabledDdlTriggers = await db.$queryRawUnsafe("SELECT evtname FROM pg_event_trigger WHERE evtenabled <> 'D' ORDER BY evtname");
  const presentTables = (await db.$queryRawUnsafe(`SELECT relname FROM pg_class WHERE oid IN (to_regclass('public."RelayPositionReductionAudit"'), to_regclass('public."SignalCycleParticipantReduction"')) ORDER BY relname`)).map((row) => row.relname);
  const [{ enum_present }] = await db.$queryRawUnsafe(`SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = '${EXPECTED_ENUM}') AS enum_present`);
  const presentColumns = (await db.$queryRawUnsafe(`SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='SignalCycleEvent' AND column_name = ANY(ARRAY['sourceEventId','sourcePayloadSha256','sourceEventSeq','platformReceivedAt']) ORDER BY column_name`)).map((row) => row.column_name);
  const [{ index_present }] = await db.$queryRawUnsafe(`SELECT to_regclass('public."${EXPECTED_INDEX}"') IS NOT NULL AS index_present`);
  const [{ participant_id_type }] = await db.$queryRawUnsafe(`SELECT format_type(a.atttypid,a.atttypmod) AS participant_id_type FROM pg_attribute a WHERE a.attrelid=to_regclass('public."SignalCycleParticipant"') AND a.attname='id' AND NOT a.attisdropped`);
  return { ledgerRows, enabledDdlTriggers, presentTables, enumPresent: enum_present, presentColumns, sourceIndexPresent: index_present, participantIdType: participant_id_type };
}

export function prismaAdapter(prisma) {
  return {
    inspect: () => inspectDb(prisma),
    transaction: (fn, limits) => prisma.$transaction(async (tx) => fn({
      inspect: () => inspectDb(tx),
      setTimeouts: async () => {
        await tx.$executeRawUnsafe("SET LOCAL lock_timeout = '3s'");
        await tx.$executeRawUnsafe("SET LOCAL statement_timeout = '30s'");
      },
      executePinnedSql: async (sql) => {
        // These two hash-pinned files contain only plain additive DDL. Execute
        // each exact constituent statement because Prisma's prepared-query
        // transport does not accept a multi-command SQL string.
        for (const statement of splitPinnedSqlStatements(sql)) await tx.$executeRawUnsafe(statement);
      },
    }), { maxWait: limits.maxWaitMs, timeout: limits.timeoutMs }),
  };
}

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];
if (isMain) {
  const mode = parseMode(process.argv.slice(2));
  const repoRoot = dirname(dirname(fileURLToPath(import.meta.url)));
  const migrations = await loadReviewedMigrations(repoRoot);
  const { PrismaClient } = await import('@prisma/client');
  const prisma = new PrismaClient();
  try {
    console.log(JSON.stringify(await runReviewedRelaySchema({ adapter: prismaAdapter(prisma), migrations, mode })));
  } finally {
    await prisma.$disconnect();
  }
}
