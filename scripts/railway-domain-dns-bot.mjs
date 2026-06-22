#!/usr/bin/env node
/**
 * Add bot tunnel CNAME on Railway-purchased domain (doxxedcrypto.digital).
 * Tries Cloudflare, Name.com API, and Railway UI automation when possible.
 */
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const skipBrowser = args.includes('--no-browser');

const r = spawnSync(
  process.execPath,
  [
    path.join(repoRoot, 'scripts', 'apply-bot-dns-auto.mjs'),
    ...(skipBrowser ? ['--skip-browser'] : []),
  ],
  { cwd: repoRoot, stdio: 'inherit' },
);
process.exit(r.status ?? 1);

