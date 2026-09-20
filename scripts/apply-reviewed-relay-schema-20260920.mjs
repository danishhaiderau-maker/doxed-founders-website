import { createHash } from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const APPLY_FLAG = '--apply-reviewed-relay-schema-20260920';
export const REVIEWED_MIGRATIONS = Object.freeze([
  ['20260824120000_relay_position_reduction_audit', '5b66b57077eb0d0c6848627563ccad6ab59a951170faf7f2985481fbb0548b1e'],
  ['20260903090000_signal_cycle_source_event_ack', '2b8272f05cd7fd423214d2dd194f18c76bc1787192b7f86587c563a09f7297f6'],
  ['20260920143000_relay_reduction_index_names', '4e6fcdca256d651da21bc07cddefac27a9ccae021b951357f7c43d6e2e66c945'],
]);
const ALLOWED_HISTORICAL_MISMATCH = '20260711220000_founder_economics_mvp';
const EXPECTED_APPLIED = 25;
const EXPECTED_TABLES = ['RelayPositionReductionAudit', 'SignalCycleParticipantReduction'];
const EXPECTED_COLUMNS = ['platformReceivedAt', 'sourceEventId', 'sourceEventSeq', 'sourcePayloadSha256'];
const EXPECTED_ENUM = 'ParticipantReductionPhase';
const EXPECTED_INDEX = 'SignalCycleEvent_sourceEventId_key';

const sha256 = (value) => createHash('sha256').update(value).digest('hex');
export const splitPinnedSqlStatements = (sql) => sql.split(';').map((statement) => statement.trim()).filter(Boolean);
const canonicalRows = (rows) => rows.map((row) => Object.fromEntries(
  Object.keys(row).sort().map(key => [key, row[key] instanceof Date ? row[key].toISOString() : row[key]])
)).sort((a, b) => String(a.id).localeCompare(String(b.id)));
export function checksumVariants(bytes) {
  const raw = bytes.toString('utf8');
  const lf = raw.replace(/\r\n/g, '\n');
  return new Set([sha256(bytes), sha256(lf), sha256(lf.replace(/\n/g, '\r\n'))]);
}
const RENAMED_INDEXES = [
  'SignalCycleParticipantReduction_participantId_sourceEventSe_key',
  'SignalCycleParticipantReduction_participantId_phase_created_idx',
];
const OLD_INDEXES = [
  'SignalCycleParticipantReduction_participantId_sourceEventSeq_ke',
  'SignalCycleParticipantReduction_participantId_phase_createdAt_i',
];
const ROLLBACK_NAME = '20260706120000_phase15_trust_layer';
const ROLLBACK_AT = '2026-07-07T03:25:03.197Z';
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
    sourceChecksums.set(name, checksumVariants(bytes));
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
  const rows = rawRows.filter(row => row.rolled_back_at == null);
  const rolled = rawRows.filter(row => row.rolled_back_at != null);
  if (rawRows.length !== 26 || rows.length !== EXPECTED_APPLIED
      || new Set(rawRows.map(row => row.id)).size !== 26
      || rows.some(row => !row.id || !row.checksum || row.finished_at == null)
      || rolled.length !== 1 || rolled[0].migration_name !== ROLLBACK_NAME
      || new Date(rolled[0].rolled_back_at).toISOString() !== ROLLBACK_AT
      || rolled[0].finished_at != null) {
    throw new Error('ledger must contain 25 applied plus exact reviewed historical rollback');
  }
  const applied = new Set(rows.map(row => row.migration_name));
  if (applied.size !== 25 || !applied.has(ROLLBACK_NAME)) throw new Error('applied migration identities are not unique/complete');
  if (rawRows.some(row => !migrations.sourceChecksums.has(row.migration_name))) throw new Error('migration ledger contains unknown rows');
  const mismatches = rawRows.filter(row => !migrations.sourceChecksums.get(row.migration_name).has(row.checksum));
  if (mismatches.some(row => row.migration_name !== ALLOWED_HISTORICAL_MISMATCH || row.rolled_back_at != null)
      || mismatches.length > 1) throw new Error('migration ledger contains an unreviewed checksum mismatch');
  const pending = migrations.sourceNames.filter((name) => !applied.has(name));
  if (JSON.stringify(pending) !== JSON.stringify(REVIEWED_MIGRATIONS.map(([name]) => name).sort())) {
    throw new Error('pending migrations are not the exact reviewed three');
  }
  if ((state.activeDdl ?? []).length) throw new Error('concurrent DDL is not allowed');
  if ((state.enabledDdlTriggers ?? []).length) throw new Error('enabled DDL event triggers are not allowed');
  if ((state.presentTables ?? []).length || state.enumPresent || (state.presentColumns ?? []).length || state.sourceIndexPresent || (state.renamedIndexes ?? []).length || (state.oldIndexes ?? []).length) {
    throw new Error('reviewed additive schema is not fully absent');
  }
  if (state.participantIdType !== 'text') throw new Error('SignalCycleParticipant.id is not text');
  return { applied: rows.length, pending, ledgerFingerprint: ledgerFingerprint(rawRows), historicalMismatch: mismatches.map((row) => row.migration_name) };
}

export function validatePostSchema(state, expectedLedgerFingerprint) {
  if (ledgerFingerprint(state.ledgerRows ?? []) !== expectedLedgerFingerprint) throw new Error('migration ledger changed during DDL');
  if (JSON.stringify([...(state.presentTables ?? [])].sort()) !== JSON.stringify([...EXPECTED_TABLES].sort())) throw new Error('reviewed tables were not created');
  if (!state.enumPresent) throw new Error('reviewed enum was not created');
  if (JSON.stringify([...(state.presentColumns ?? [])].sort()) !== JSON.stringify([...EXPECTED_COLUMNS].sort())) throw new Error('reviewed columns were not created');
  if (!state.sourceIndexPresent) throw new Error('reviewed source event index was not created');
  if (JSON.stringify([...(state.renamedIndexes ?? [])].sort()) !== JSON.stringify([...RENAMED_INDEXES].sort()) || (state.oldIndexes ?? []).length) throw new Error('reviewed index renames incomplete');
}

export async function runReviewedRelaySchema({ adapter, migrations, mode, expectedLedgerFingerprint }) {
  if (mode !== 'dry-run' && mode !== 'apply') throw new Error('invalid execution mode');
  const outer = await adapter.inspect();
  const preflight = validatePreflight(outer, migrations);
  if (mode === 'dry-run') return { ok: true, mode, ...preflight, reviewedHashes: migrations.reviewed.map(({ name, sha256 }) => ({ name, sha256 })) };
  if (!/^[a-f0-9]{64}$/.test(expectedLedgerFingerprint ?? '') || expectedLedgerFingerprint !== preflight.ledgerFingerprint) throw new Error('apply requires exact reviewed EXPECTED_LEDGER_SHA256 from fresh dry-run');
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
  const ledgerRows = await db.$queryRawUnsafe('SELECT * FROM "_prisma_migrations" ORDER BY migration_name');
  const activeDdl = await db.$queryRawUnsafe("SELECT pid FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND state='active' AND query ~* '^\\s*(ALTER|CREATE|DROP|TRUNCATE|REINDEX)\\s'");
  const enabledDdlTriggers = await db.$queryRawUnsafe("SELECT evtname FROM pg_event_trigger WHERE evtenabled <> 'D' ORDER BY evtname");
  const presentTables = (await db.$queryRawUnsafe(`SELECT relname FROM pg_class WHERE oid IN (to_regclass('public."RelayPositionReductionAudit"'), to_regclass('public."SignalCycleParticipantReduction"')) ORDER BY relname`)).map((row) => row.relname);
  const [{ enum_present }] = await db.$queryRawUnsafe(`SELECT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE t.typname = '${EXPECTED_ENUM}' AND n.nspname='public') AS enum_present`);
  const presentColumns = (await db.$queryRawUnsafe(`SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='SignalCycleEvent' AND column_name = ANY(ARRAY['sourceEventId','sourcePayloadSha256','sourceEventSeq','platformReceivedAt']) ORDER BY column_name`)).map((row) => row.column_name);
  const [{ index_present }] = await db.$queryRawUnsafe(`SELECT to_regclass('public."${EXPECTED_INDEX}"') IS NOT NULL AS index_present`);
  const [{ participant_id_type }] = await db.$queryRawUnsafe(`SELECT format_type(a.atttypid,a.atttypmod) AS participant_id_type FROM pg_attribute a WHERE a.attrelid=to_regclass('public."SignalCycleParticipant"') AND a.attname='id' AND NOT a.attisdropped`);
  const reductionIndexes = (await db.$queryRawUnsafe("SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename='SignalCycleParticipantReduction'")).map(row => row.indexname);
  return { ledgerRows, activeDdl, renamedIndexes: reductionIndexes.filter(name => RENAMED_INDEXES.includes(name)), oldIndexes: reductionIndexes.filter(name => OLD_INDEXES.includes(name)), enabledDdlTriggers, presentTables, enumPresent: enum_present, presentColumns, sourceIndexPresent: index_present, participantIdType: participant_id_type };
}

export function prismaAdapter(prisma) {
  return {
    inspect: () => inspectDb(prisma),
    transaction: (fn, limits) => prisma.$transaction(async (tx) => fn({
      inspect: () => inspectDb(tx),
      setTimeouts: async () => {
        await tx.$executeRawUnsafe("SET LOCAL lock_timeout = '3s'");
        await tx.$executeRawUnsafe("SET LOCAL statement_timeout = '30s'");
        await tx.$executeRawUnsafe('SET LOCAL search_path = public, pg_catalog');
        const [lock] = await tx.$queryRawUnsafe('SELECT pg_try_advisory_xact_lock(20260920, 143000) AS acquired');
        if (!lock.acquired) throw new Error('reviewed schema transaction lock unavailable');
      },
      executePinnedSql: async (sql) => {
        // These three hash-pinned files contain only plain additive DDL. Execute
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
    console.log(JSON.stringify(await runReviewedRelaySchema({ adapter: prismaAdapter(prisma), migrations, mode, expectedLedgerFingerprint: process.env.EXPECTED_LEDGER_SHA256 })));
  } finally {
    await prisma.$disconnect();
  }
}
