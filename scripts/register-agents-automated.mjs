#!/usr/bin/env node
/**
 * Automated agent directory registrations (CLI/API only — no manual web forms).
 * - Fushu API
 * - Spawn API (when THESPAWN_API_KEY in vault)
 * - SAID (when agent-wallet.json is funded)
 */
import { execSync } from 'node:child_process';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

execSync('node scripts/submit-agent-directories.mjs --said', {
  cwd: root,
  stdio: 'inherit',
});
