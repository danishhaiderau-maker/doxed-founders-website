/**
 * Push Phala CVM env vars to Railway doxed-founders-website and redeploy.
 * Requires vault/.env.phala (see scripts/templates/env.phala.example).
 */
import { loadPhalaCvmEnv } from './lib/read-vault-env.mjs';
import { syncRailwayServiceVars } from './lib/railway-sync.mjs';

function normalizeBase(url) {
  return (url || '').trim().replace(/\/$/, '');
}

const { merged, phalaPath, hasPhalaFile } = loadPhalaCvmEnv();

if (!hasPhalaFile) {
  console.error(`Missing ${phalaPath}`);
  console.error('Copy scripts/templates/env.phala.example → vault/.env.phala');
  process.exit(1);
}

const token = merged.RAILWAY_TOKEN?.trim();
if (!token) {
  console.error('Set RAILWAY_TOKEN in vault/.env.phala or vault/.env.x.secrets');
  process.exit(1);
}

const base =
  normalizeBase(merged.PHALA_CVM_BASE_URL) ||
  normalizeBase(merged.PHALA_CVM_BACKUP_URL?.replace(/\/vault\/backup\/?$/, '')) ||
  normalizeBase(merged.PHALA_CVM_UNWRAP_URL?.replace(/\/secrets\/unwrap\/?$/, ''));

if (!base) {
  console.error('Set PHALA_CVM_BASE_URL in vault/.env.phala (Phala INGRESS HTTPS URL).');
  process.exit(1);
}

const apiKey = merged.PHALA_CVM_API_KEY?.trim() || merged.PHALA_API_KEY?.trim();
if (!apiKey) {
  console.error('Set PHALA_CVM_API_KEY or PHALA_API_KEY in vault/.env.phala');
  process.exit(1);
}

const vars = {
  PHALA_CVM_BACKUP_URL: base,
  PHALA_CVM_UNWRAP_URL: base,
  PHALA_API_KEY: apiKey,
  PHALA_CVM_API_KEY: merged.PHALA_CVM_API_KEY?.trim() || apiKey,
};

if (merged.PHALA_INFERENCE_URL?.trim()) vars.PHALA_INFERENCE_URL = merged.PHALA_INFERENCE_URL.trim();
if (merged.PHALA_MODEL?.trim()) vars.PHALA_MODEL = merged.PHALA_MODEL.trim();
if (merged.PHALA_CVM_WORKLOAD_ID?.trim()) vars.PHALA_CVM_WORKLOAD_ID = merged.PHALA_CVM_WORKLOAD_ID.trim();

console.log('\n=== Apply Phala CVM vars to Railway ===\n');
console.log('PHALA_CVM_BACKUP_URL / UNWRAP_URL →', base);
console.log('Keys set:', Object.keys(vars).join(', '));

const { project, service } = await syncRailwayServiceVars(token, vars);
console.log(`\nRedeploy triggered: ${project} / ${service}`);
console.log('\nNext: npm run probe:phala-cvm');
console.log('Then: npm run smoke:test\n');
