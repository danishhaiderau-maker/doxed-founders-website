import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const apiDir = path.join(root, 'apps', 'api');
const schema = process.env.PRISMA_SCHEMA ?? 'prisma/schema.prisma';
const isRailway = Boolean(process.env.RAILWAY_ENVIRONMENT || process.env.RAILWAY_SERVICE_ID);

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

run('Prisma generate', 'npx', ['prisma', 'generate', '--schema', schema]);

function syncDatabase() {
  if (process.env.SKIP_DB_SYNC === 'true') {
    console.log('[start-api-prod] SKIP_DB_SYNC=true — skipping schema sync');
    return;
  }

  if (isRailway || process.env.PRISMA_DB_PUSH === 'true') {
    console.log('[start-api-prod] Railway/PRISMA_DB_PUSH — using db push (Neon-safe)');
    const ok = run(
      'Prisma db push',
      'npx',
      ['prisma', 'db', 'push', '--schema', schema, '--skip-generate'],
      root,
      true,
    );
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
    run(
      'Prisma db push (fallback)',
      'npx',
      ['prisma', 'db', 'push', '--schema', schema, '--skip-generate'],
      root,
      true,
    );
    return;
  }

  console.warn(
    '[start-api-prod] No migrations — set PRISMA_DB_PUSH=true or add prisma/migrations',
  );
}

syncDatabase();

const distMain = path.join(apiDir, 'dist', 'main.js');
if (!fs.existsSync(distMain)) {
  console.error(`[start-api-prod] Missing ${distMain} — run npm run build --workspace=@dcf/api first`);
  process.exit(1);
}

run('Start NestJS API', 'node', ['dist/main.js'], apiDir);
