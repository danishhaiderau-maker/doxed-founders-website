#!/usr/bin/env node
/**
 * Retired home-owner compatibility command.
 *
 * Older versions started Cloudflare, rewired Railway to the laptop, and could
 * launch a second strategy owner. Keep the familiar command recoverable, but
 * route it only to the safe Fly dashboard/data/analyzer mirror recovery.
 */
import { spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const recovery = join(root, 'scripts', 'fast-recover-global.ps1');

console.log('Home production ownership is retired.');
console.log('Recovering the Fly dashboard/data/analyzer mirror only...');

const result = spawnSync(
  'powershell.exe',
  ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', recovery],
  { cwd: root, stdio: 'inherit' },
);

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);
