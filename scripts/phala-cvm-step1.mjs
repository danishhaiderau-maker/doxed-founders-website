/**
 * Step 1 orchestrator: Phala CVM deploy + Railway env + probe.
 */
import { spawnSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { loadPhalaCvmEnv } from './lib/read-vault-env.mjs';

const scriptsDir = dirname(fileURLToPath(import.meta.url));
const root = join(scriptsDir, '..');

function run(label, script) {
  console.log(`\n=== ${label} ===\n`);
  const res = spawnSync('node', [join(scriptsDir, script)], {
    cwd: root,
    stdio: 'inherit',
    shell: process.platform === 'win32',
  });
  return res.status ?? 1;
}

const { merged } = loadPhalaCvmEnv();
const hasCloudKey = Boolean(
  merged.PHALA_CLOUD_API_KEY?.trim() ||
    (merged.PHALA_API_KEY?.trim()?.startsWith('phak_') && merged.PHALA_API_KEY.trim()),
);
const hasBase = Boolean(merged.PHALA_CVM_BASE_URL?.trim());
const hasRailway = Boolean(merged.RAILWAY_TOKEN?.trim());

if (!hasBase) {
  if (!hasCloudKey) {
    console.error('Blocked: add PHALA_CLOUD_API_KEY to vault/.env.phala');
    console.error('  https://cloud.phala.network/dashboard/tokens');
    console.error('Then: npm run deploy:phala-cvm');
    process.exit(1);
  }
  const code = run('Deploy Phala CVM', 'deploy-phala-cvm.mjs');
  if (code !== 0) process.exit(code);
}

const { merged: after } = loadPhalaCvmEnv();
if (!after.PHALA_CVM_BASE_URL?.trim()) {
  console.error('PHALA_CVM_BASE_URL still empty after deploy.');
  process.exit(1);
}

if (!hasRailway) {
  console.error('RAILWAY_TOKEN missing in vault/.env.x.secrets — cannot apply Railway vars.');
  process.exit(1);
}

const applyCode = run('Apply Railway Phala CVM vars', 'apply-railway-phala-cvm.mjs');
if (applyCode !== 0) process.exit(applyCode);

const probeCode = run('Probe production', 'probe-phala-cvm-workload.mjs');
process.exit(probeCode);
