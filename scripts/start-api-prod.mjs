import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const apiDir = path.join(root, 'apps', 'api');
const schema = process.env.PRISMA_SCHEMA ?? 'prisma/schema.prisma';

function run(label, command, args, cwd = root) {
  console.log(`\n[start-api-prod] ${label}`);
  const result = spawnSync(command, args, {
    cwd,
    stdio: 'inherit',
    shell: true,
  });
  if (result.status !== 0) {
    console.error(`[start-api-prod] Failed: ${label}`);
    process.exit(result.status ?? 1);
  }
}

run('Prisma generate', 'npx', ['prisma', 'generate', '--schema', schema]);

const migrationsDir = path.join(root, 'prisma', 'migrations');
const hasMigrations =
  fs.existsSync(migrationsDir) &&
  fs.readdirSync(migrationsDir).some((entry) => !entry.startsWith('.'));

if (hasMigrations) {
  console.log('\n[start-api-prod] Prisma migrate deploy');
  const migrate = spawnSync('npx', ['prisma', 'migrate', 'deploy', '--schema', schema], {
    cwd: root,
    stdio: 'inherit',
    shell: true,
  });
  if (migrate.status !== 0) {
    console.warn(
      '[start-api-prod] migrate deploy failed (common on Neon db-push baselines). Falling back to db push…',
    );
    run('Prisma db push (fallback)', 'npx', [
      'prisma',
      'db',
      'push',
      '--schema',
      schema,
      '--skip-generate',
    ]);
  }
} else if (process.env.PRISMA_DB_PUSH === 'true') {
  run('Prisma db push', 'npx', ['prisma', 'db', 'push', '--schema', schema]);
} else {
  console.warn(
    '[start-api-prod] No prisma/migrations found. Set PRISMA_DB_PUSH=true for first Neon deploy, or run npm run db:migrate locally.',
  );
}

run('Start NestJS API', 'node', ['dist/main.js'], apiDir);
