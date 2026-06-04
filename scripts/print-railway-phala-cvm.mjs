/**
 * Writes Railway Raw Editor block for Phala CVM vars (no deploy).
 */
import { writeFileSync } from 'fs';
import { join } from 'path';
import { loadPhalaCvmEnv, repoRoot, vaultDir } from './lib/read-vault-env.mjs';

function normalizeBase(url) {
  return (url || '').trim().replace(/\/$/, '');
}

const { merged, phalaPath, hasPhalaFile } = loadPhalaCvmEnv();
const base =
  normalizeBase(merged.PHALA_CVM_BASE_URL) ||
  normalizeBase(merged.PHALA_CVM_BACKUP_URL?.replace(/\/vault\/backup\/?$/, '')) ||
  normalizeBase(merged.PHALA_CVM_UNWRAP_URL?.replace(/\/secrets\/unwrap\/?$/, ''));

const lines = [];
const add = (k, v) => {
  if (v?.trim()) lines.push(`${k}=${v.trim()}`);
};

if (base) {
  add('PHALA_CVM_BACKUP_URL', base);
  add('PHALA_CVM_UNWRAP_URL', base);
}
add('PHALA_API_KEY', merged.PHALA_API_KEY);
add('PHALA_CVM_API_KEY', merged.PHALA_CVM_API_KEY || merged.PHALA_API_KEY);
add('PHALA_INFERENCE_URL', merged.PHALA_INFERENCE_URL);
add('PHALA_MODEL', merged.PHALA_MODEL);
add('PHALA_CVM_WORKLOAD_ID', merged.PHALA_CVM_WORKLOAD_ID);

const out = join(vaultDir, 'railway-phala-cvm-paste.env');
const fallback = join(repoRoot, 'railway-phala-cvm-paste.env');

if (!hasPhalaFile) {
  console.error(`Missing ${phalaPath}`);
  console.error('Copy scripts/templates/env.phala.example → vault/.env.phala and fill values.');
  process.exit(1);
}

if (!base) {
  console.error('Set PHALA_CVM_BASE_URL in vault/.env.phala (Phala INGRESS URL after CVM deploy).');
  process.exit(1);
}

const target = lines.length ? out : fallback;
writeFileSync(target, `${lines.join('\n')}\n`);
console.log(`Wrote ${lines.length} vars to ${target}`);
console.log('Paste into Railway → doxed-founders-website → Variables → Raw Editor, then redeploy.');
