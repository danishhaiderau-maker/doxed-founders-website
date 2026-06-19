#!/usr/bin/env node
/**
 * Fail CI/deploy if frozen Bitfinex copy-policy files change without updating the lock manifest.
 * Run: npm run verify:bitfinex-policy-lock
 * After approved change: npm run lock:bitfinex-policy
 */
import { createHash } from 'crypto';
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const lockPath = join(root, 'scripts', 'bitfinex-policy-lock.json');

const POLICY_FILES = [
  'packages/utils/src/bitfinex-copy-policy.ts',
  'packages/utils/src/subscriber-exit.ts',
  'apps/api/src/trading-agents/signal-subscriber-execution.service.ts',
];

function fileDigest(rel) {
  const abs = join(root, rel);
  if (!existsSync(abs)) throw new Error(`Missing ${rel}`);
  // Normalize line endings so Windows dev + Linux CI agree on policy hashes.
  const text = readFileSync(abs, 'utf8').replace(/\r\n/g, '\n');
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

function sha256File(rel) {
  return fileDigest(rel);
}

function fail(msg) {
  console.error(`\nPOLICY LOCK FAIL: ${msg}`);
  console.error('If this change is intentional, run: npm run lock:bitfinex-policy\n');
  process.exit(1);
}

if (!existsSync(lockPath)) {
  fail('scripts/bitfinex-policy-lock.json missing — run npm run lock:bitfinex-policy');
}

const lock = JSON.parse(readFileSync(lockPath, 'utf8'));
const current = {};
for (const rel of POLICY_FILES) {
  current[rel] = sha256File(rel);
}

const mismatches = [];
for (const rel of POLICY_FILES) {
  if (lock.files?.[rel] !== current[rel]) mismatches.push(rel);
}

const policySrc = readFileSync(join(root, 'packages/utils/src/bitfinex-copy-policy.ts'), 'utf8');
const versionMatch = policySrc.match(/BITFINEX_COPY_POLICY_VERSION\s*=\s*(\d+)/);
const version = versionMatch ? Number(versionMatch[1]) : null;
if (version != null && lock.policyVersion != null && version !== lock.policyVersion) {
  mismatches.push(`BITFINEX_COPY_POLICY_VERSION ${lock.policyVersion} -> ${version}`);
}

if (mismatches.length) {
  fail(`Unapproved policy drift:\n  - ${mismatches.join('\n  - ')}`);
}

console.log('OK  Bitfinex policy lock verified');
console.log(`    version=${lock.policyVersion} files=${POLICY_FILES.length}`);
