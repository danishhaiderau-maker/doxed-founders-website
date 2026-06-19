#!/usr/bin/env node
/** Regenerate bitfinex-policy-lock.json after an approved policy change. */
import { createHash } from 'crypto';
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const POLICY_FILES = [
  'packages/utils/src/bitfinex-copy-policy.ts',
  'packages/utils/src/subscriber-exit.ts',
  'apps/api/src/trading-agents/signal-subscriber-execution.service.ts',
];

function fileDigest(rel) {
  const abs = join(root, rel);
  const text = readFileSync(abs, 'utf8').replace(/\r\n/g, '\n');
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

function sha256File(rel) {
  if (!existsSync(join(root, rel))) throw new Error(`Missing ${rel}`);
  return fileDigest(rel);
}

const policySrc = readFileSync(join(root, 'packages/utils/src/bitfinex-copy-policy.ts'), 'utf8');
const versionMatch = policySrc.match(/BITFINEX_COPY_POLICY_VERSION\s*=\s*(\d+)/);
const policyVersion = versionMatch ? Number(versionMatch[1]) : 1;

const files = {};
for (const rel of POLICY_FILES) {
  if (!existsSync(join(root, rel))) throw new Error(`Missing ${rel}`);
  files[rel] = sha256File(rel);
}

const lock = {
  policyVersion,
  updatedAt: new Date().toISOString(),
  files,
};

writeFileSync(join(root, 'scripts', 'bitfinex-policy-lock.json'), `${JSON.stringify(lock, null, 2)}\n`);
console.log('Wrote scripts/bitfinex-policy-lock.json', lock);
