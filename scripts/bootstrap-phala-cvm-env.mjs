/**
 * Create vault/.env.phala from template; pre-fill JWT_SECRET from vercel.check.
 */
import { existsSync, readFileSync, writeFileSync } from 'fs';
import { join } from 'path';
import { randomBytes } from 'crypto';
import { loadPhalaCvmEnv, repoRoot, vaultDir } from './lib/read-vault-env.mjs';

const template = join(repoRoot, 'scripts', 'templates', 'env.phala.example');
const target = join(vaultDir, '.env.phala');

if (existsSync(target)) {
  console.log(`Already exists: ${target}`);
  process.exit(0);
}

const { merged } = loadPhalaCvmEnv();
const token = randomBytes(24).toString('base64url');
let body = readFileSync(template, 'utf8');

if (merged.JWT_SECRET) {
  body = body.replace(/^JWT_SECRET=.*$/m, `JWT_SECRET=${merged.JWT_SECRET}`);
}
body = body.replace(/^PHALA_CVM_API_KEY=.*$/m, `PHALA_CVM_API_KEY=${token}`);

writeFileSync(target, body, 'utf8');
console.log(`Created ${target}`);
console.log('Generated PHALA_CVM_API_KEY — use the same value as CVM_WORKLOAD_AUTH_TOKEN on Phala Encrypted Secrets.');
console.log('Next: deploy workers/phala-cvm-workload, set PHALA_CVM_BASE_URL, then npm run apply:railway:phala-cvm');
