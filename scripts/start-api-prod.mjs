import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const apiDir = path.join(root, 'apps', 'api');
const schema = process.env.PRISMA_SCHEMA ?? 'prisma/schema.prisma';
const isProd = (process.env.NODE_ENV ?? '').trim() === 'production';

process.env.NODE_ENV = isProd ? 'production' : (process.env.NODE_ENV?.trim() || 'production');
if (isProd && process.env.PRISMA_DB_PUSH !== 'false') {
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

function syncDatabaseBackground() {
  if (process.env.SKIP_DB_SYNC === 'true') return;

  console.log('[start-api-prod] Background db push (after API boot)');
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

function syncDatabaseBlocking() {
  if (process.env.SKIP_DB_SYNC === 'true') {
    console.log('[start-api-prod] SKIP_DB_SYNC=true — skipping schema sync');
    return;
  }

  console.log('[start-api-prod] Prisma db push (dev/self-host)');
  const ok = run(
    'Prisma db push',
    'npx',
    ['prisma', 'db', 'push', '--schema', schema, '--skip-generate'],
    root,
    true,
  );
  if (!ok) {
    console.warn('[start-api-prod] db push reported issues — continuing');
  }
}

const distMain = path.join(apiDir, 'dist', 'main.js');
if (!fs.existsSync(distMain)) {
  console.error(`[start-api-prod] Missing ${distMain} — run npm run build --workspace=@dcf/api first`);
  process.exit(1);
}

console.log('[start-api-prod] boot', {
  nodeEnv: process.env.NODE_ENV,
  port: process.env.PORT ?? process.env.API_PORT ?? 4000,
  healthPath: '/api/health/live',
  railwayService: process.env.RAILWAY_SERVICE_ID ?? null,
});

if (isProd) {
  // Production: never block container boot — Railway healthcheck needs a listener within ~2 min.
  // prisma generate already ran in the build phase.
  setTimeout(() => syncDatabaseBackground(), 15_000);
} else {
  run('Prisma generate', 'npx', ['prisma', 'generate', '--schema', schema]);
  syncDatabaseBlocking();
}

run('Start NestJS API', 'node', ['dist/main.js'], apiDir);
