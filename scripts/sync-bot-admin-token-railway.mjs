#!/usr/bin/env node
/**
 * Align the API service's BOT_ADMIN_TOKEN with the canonical local vault.
 * Secret values are read in-process and are never printed or passed as CLI args.
 */
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { readDotEnv, vaultDir } from './lib/read-vault-env.mjs';
import { syncRailwayServiceVars } from './lib/railway-sync.mjs';

const home = readDotEnv(join(vaultDir, 'home-bot.env'));
const railway = readDotEnv(join(vaultDir, '.env.x.secrets'));
const adminToken = home.BOT_ADMIN_TOKEN?.trim();
const railwayToken = railway.RAILWAY_TOKEN?.trim() || process.env.RAILWAY_TOKEN?.trim();

if (!adminToken) throw new Error('BOT_ADMIN_TOKEN is missing from the canonical home-bot vault.');
if (!railwayToken) throw new Error('RAILWAY_TOKEN is missing from the deployment vault.');

let target;
try {
  target = await syncRailwayServiceVars(railwayToken, { BOT_ADMIN_TOKEN: adminToken });
} catch {
  // The stored API token can expire while the developer CLI remains signed in.
  // Feed the secret through stdin; it is never present in argv/process listings.
  const cli = process.platform === 'win32' ? 'railway.cmd' : 'railway';
  const result = spawnSync(
    cli,
    [
      'variable', 'set', 'BOT_ADMIN_TOKEN', '--stdin',
      '--service', 'doxed-founders-website', '--environment', 'production', '--json',
    ],
    { cwd: process.cwd(), input: adminToken, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] },
  );
  if (result.status !== 0) throw new Error('Railway BOT_ADMIN_TOKEN synchronization failed.');
  target = { project: 'linked-project', service: 'doxed-founders-website' };
}
console.log(JSON.stringify({ ok: true, ...target, variable: 'BOT_ADMIN_TOKEN', redeployTriggered: true }));
