#!/usr/bin/env node
/**
 * Read-only compatibility check.
 *
 * This command used to create/redeploy a second btc-conservative-agent on
 * Railway and point the API at it. That violates the one-owner architecture.
 * It now validates the source-controlled Fly lock and performs no network or
 * platform mutation.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const lockPath = join(root, 'config', 'fly-canonical.lock.json');
const lock = JSON.parse(readFileSync(lockPath, 'utf8'));

if (
  lock.frozen !== true ||
  lock.desktopBotEnabled !== false ||
  lock.sourceUrl !== 'https://doxed-btc-bot.fly.dev'
) {
  console.error(
    'Canonical Fly runtime lock is missing or inconsistent; refusing any bot URL mutation.',
  );
  process.exit(1);
}

console.log('OK  Fly.io is the sole Conservative BTC runtime.');
console.log(`OK  API bot source URL must remain ${lock.sourceUrl}`);
console.log('OK  Railway bot provisioning is retired; no services or variables were changed.');
