import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const apiDir = path.join(root, 'apps', 'api');
const schema = process.env.PRISMA_SCHEMA ?? 'prisma/schema.prisma';
const isRailway = Boolean(process.env.RAILWAY_ENVIRONMENT || process.env.RAILWAY_SERVICE_ID);

process.env.NODE_ENV = process.env.NODE_ENV?.trim() || 'production';
if (isRailway && process.env.PRISMA_DB_PUSH !== 'false') {
  process.env.PRISMA_DB_PUSH = 'true';
}

function run(label, command, args, cwd = root, allowFail = false) {
  console.log(`\n[start-api-prod] ${label}`);
  const result = spawnSync(command, args, {
    cwd,
    stdio: 'inherit',
    shell: true,
    env: { ...process.env, NODE_ENV: process.env.NODE_ENV ?? 'production' },
  });
  if (result.status !== 0 && !allowFail) {
    console.error(`[start-api-prod] Failed: ${label}`);
    process.exit(result.status ?? 1);
  }
  return result.status === 0;
}

function runDbPush(label, allowFail = true) {
  return run(
    label,
    'npx',
    ['prisma', 'db', 'push', '--schema', schema, '--skip-generate'],
    root,
    allowFail,
  );
}

function syncDatabaseBlocking() {
  if (process.env.SKIP_DB_SYNC === 'true') {
    console.log('[start-api-prod] SKIP_DB_SYNC=true — skipping schema sync');
    return;
  }

  if (isRailway || process.env.PRISMA_DB_PUSH === 'true') {
    console.log('[start-api-prod] Railway/PRISMA_DB_PUSH — using db push (Neon-safe)');
    const ok = runDbPush('Prisma db push');
    if (!ok) {
      console.warn(
        '[start-api-prod] db push reported issues — continuing (schema may already match Neon)',
      );
    }
    return;
  }

  const migrationsDir = path.join(root, 'prisma', 'migrations');
  const hasMigrations =
    fs.existsSync(migrationsDir) &&
    fs.readdirSync(migrationsDir).some((entry) => !entry.startsWith('.'));

  if (hasMigrations) {
    console.log('[start-api-prod] Prisma migrate deploy');
    const migrate = spawnSync('npx', ['prisma', 'migrate', 'deploy', '--schema', schema], {
      cwd: root,
      stdio: 'inherit',
      shell: true,
    });
    if (migrate.status === 0) return;

    console.warn('[start-api-prod] migrate deploy failed — falling back to db push…');
    runDbPush('Prisma db push (fallback)');
    return;
  }

  console.warn(
    '[start-api-prod] No migrations — set PRISMA_DB_PUSH=true or add prisma/migrations',
  );
}

/** On Railway, listen for healthcheck first — db push runs in background after boot. */
function syncDatabaseBackground() {
  if (process.env.SKIP_DB_SYNC === 'true') return;

  console.log('[start-api-prod] Background db push (Railway — healthcheck listens first)');
  const child = spawn(
    'npx',
    ['prisma', 'db', 'push', '--schema', schema, '--skip-generate'],
    {
      cwd: root,
      stdio: 'inherit',
      shell: true,
      env: { ...process.env, NODE_ENV: process.env.NODE_ENV ?? 'production' },
    },
  );
  child.on('exit', (code) => {
    console.log(`[start-api-prod] Background db push exited with code ${code ?? 'unknown'}`);
  });
}

const distMain = path.join(apiDir, 'dist', 'main.js');
if (!fs.existsSync(distMain)) {
  console.error(`[start-api-prod] Missing ${distMain} — run npm run build --workspace=@dcf/api first`);
  process.exit(1);
}

if (isRailway) {
  // Build phase already ran prisma generate — start API first; defer db push so Neon
  // is not locked during Nest bootstrap (Railway healthcheck needs /api/health quickly).
  setTimeout(() => syncDatabaseBackground(), 25_000);
} else {
  run('Prisma generate', 'npx', ['prisma', 'generate', '--schema', schema]);
  syncDatabaseBlocking();
}

run('Start NestJS API', 'node', ['dist/main.js'], apiDir);
