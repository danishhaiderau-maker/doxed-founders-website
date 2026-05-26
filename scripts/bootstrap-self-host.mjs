import { spawnSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const envFile = path.join(root, '.env.self-host');
const envExample = path.join(root, '.env.self-host.example');
const logPath = path.join(root, 'debug-acf3ea.log');
const sessionId = 'acf3ea';
const runId = 'bootstrap-self-host';

function log(hypothesisId, message, data = {}) {
  const line = JSON.stringify({
    sessionId,
    runId,
    hypothesisId,
    location: 'bootstrap-self-host.mjs',
    message,
    data,
    timestamp: Date.now(),
  });
  fs.appendFileSync(logPath, `${line}\n`);
  console.log(`[${hypothesisId}] ${message}`, data.ok ?? data.error ?? '');
}

function secret() {
  return crypto.randomBytes(32).toString('base64url');
}

function run(label, command, args, extraEnv = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    shell: true,
    encoding: 'utf8',
    env: { ...process.env, ...extraEnv },
  });
  const ok = result.status === 0;
  log('H-run', label, {
    ok,
    status: result.status,
    stderr: (result.stderr || '').slice(0, 500),
    stdout: (result.stdout || '').slice(0, 300),
  });
  if (!ok) {
    throw new Error(`${label} failed (exit ${result.status})`);
  }
  return result;
}

const isWin = process.platform === 'win32';
const npx = isWin ? 'npx.cmd' : 'npx';

function stopDevServers() {
  if (!isWin) return;
  log('H-stop', 'stopping dev servers on 3000/4000', {});
  spawnSync(
    'powershell',
    ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'scripts/stop-dev.ps1'],
    { cwd: root, shell: true, stdio: 'ignore' },
  );
  spawnSync('ping', ['-n', '4', '127.0.0.1'], { shell: true, stdio: 'ignore' });
}

function runWithRetry(label, command, args, extraEnv = {}, attempts = 3) {
  let lastError;
  for (let i = 1; i <= attempts; i += 1) {
    try {
      return run(`${label}${i > 1 ? ` (retry ${i})` : ''}`, command, args, extraEnv);
    } catch (err) {
      lastError = err;
      const msg = err instanceof Error ? err.message : String(err);
      if (i < attempts && /EPERM|operation not permitted/i.test(msg)) {
        stopDevServers();
        continue;
      }
      throw err;
    }
  }
  throw lastError;
}

function loadEnvFile(filePath) {
  const env = {};
  if (!fs.existsSync(filePath)) return env;
  for (const line of fs.readFileSync(filePath, 'utf8').split('\n')) {
    const match = line.match(/^\s*([A-Z_][A-Z0-9_]*)=(.*)$/);
    if (!match) continue;
    env[match[1]] = match[2].trim().replace(/^"|"$/g, '');
  }
  return env;
}

function upsertEnvValue(content, key, value) {
  const pattern = new RegExp(`^${key}=.*$`, 'm');
  const line = `${key}="${value}"`;
  return pattern.test(content) ? content.replace(pattern, line) : `${content.trimEnd()}\n${line}\n`;
}

try {
  log('H1', 'bootstrap start', { root });

  if (!fs.existsSync(envFile)) {
    fs.copyFileSync(envExample, envFile);
    log('H1', 'created .env.self-host from example', { ok: true });
  }

  let content = fs.readFileSync(envFile, 'utf8');
  content = upsertEnvValue(content, 'JWT_SECRET', secret());
  content = upsertEnvValue(content, 'NEXTAUTH_SECRET', secret());
  content = upsertEnvValue(content, 'NEXTAUTH_URL', 'http://127.0.0.1:3000');
  content = upsertEnvValue(content, 'CORS_ORIGINS', 'http://127.0.0.1:3000');
  content = upsertEnvValue(content, 'API_URL', 'http://127.0.0.1:4000');
  content = upsertEnvValue(content, 'NEXT_PUBLIC_API_URL', 'http://127.0.0.1:4000');
  content = upsertEnvValue(content, 'DATABASE_URL', 'file:./prisma/selfhost.db');
  content = upsertEnvValue(content, 'PRISMA_SCHEMA', 'prisma/schema.sqlite.prisma');
  if (!content.includes('DEV_DB=sqlite')) {
    content += 'DEV_DB=sqlite\n';
  }
  fs.writeFileSync(envFile, content);
  log('H2', 'secrets and localhost URLs written', { ok: true });

  const selfHostEnv = loadEnvFile(envFile);
  process.env.SQLITE_DB = 'selfhost.db';
  stopDevServers();
  run('generate-sqlite-schema', 'node', ['scripts/generate-sqlite-schema.mjs']);

  const schemaPath = path.join(root, 'prisma', 'schema.sqlite.prisma');
  const schemaText = fs.readFileSync(schemaPath, 'utf8');
  log('H3', 'schema sqlite db path', {
    ok: schemaText.includes('selfhost.db'),
    hasSelfhost: schemaText.includes('selfhost.db'),
  });

  runWithRetry('prisma generate', npx, ['prisma', 'generate', '--schema', 'prisma/schema.sqlite.prisma'], selfHostEnv);
  runWithRetry('prisma db push', npx, ['prisma', 'db push', '--schema', 'prisma/schema.sqlite.prisma'], selfHostEnv);

  const dbPath = path.join(root, 'prisma', 'selfhost.db');
  log('H4', 'database file exists', { ok: fs.existsSync(dbPath), path: dbPath });

  run('db seed', npx, ['tsx', 'prisma/seed.ts'], {
    ...selfHostEnv,
    DATABASE_URL: 'file:./prisma/selfhost.db',
  });

  log('SUMMARY', 'bootstrap complete', {
    ok: true,
    envFile,
    dbExists: fs.existsSync(dbPath),
  });
  process.exit(0);
} catch (err) {
  log('SUMMARY', 'bootstrap failed', {
    ok: false,
    error: err instanceof Error ? err.message : String(err),
  });
  process.exit(1);
}
