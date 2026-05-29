/**
 * Reset removed desk AI provider defaults before enum migration.
 */
import fs from 'fs';
import path from 'path';
import { spawnSync } from 'child_process';
import { fileURLToPath } from 'url';
import { getVaultDir } from './secrets-vault-path.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const neonPath = path.join(getVaultDir(), '.env.neon');

if (!fs.existsSync(neonPath)) {
  console.log('No vault/.env.neon — skip legacy provider reset');
  process.exit(0);
}

for (const line of fs.readFileSync(neonPath, 'utf8').split('\n')) {
  const trimmed = line.trim();
  if (!trimmed || trimmed.startsWith('#')) continue;
  const idx = trimmed.indexOf('=');
  if (idx < 1) continue;
  const key = trimmed.slice(0, idx).trim();
  let val = trimmed.slice(idx + 1).trim();
  if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
    val = val.slice(1, -1);
  }
  process.env[key] = val;
}

const sql = `
UPDATE "FounderBuilderSettings"
SET "defaultProvider" = 'RULE_BASED'
WHERE "defaultProvider"::text IN ('CURSOR', 'CLAUDE_CODE', 'CODEX', 'WINDSURF', 'OPENCLAW');
`;

const r = spawnSync('npx', ['prisma', 'db', 'execute', '--stdin', '--schema', 'prisma/schema.prisma'], {
  cwd: root,
  input: sql,
  stdio: 'inherit',
  shell: true,
  env: process.env,
});

process.exit(r.status ?? 0);
