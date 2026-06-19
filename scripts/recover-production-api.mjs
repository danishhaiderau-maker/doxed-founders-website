#!/usr/bin/env node
/**
 * Recover production when Railway API returns 502/503.
 * Safe to run on a schedule (e.g. every 15 min) after billing limits reset.
 */
import { spawnSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const API_HEALTH =
  process.env.API_HEALTH_URL ??
  'https://doxed-founders-website-production.up.railway.app/api/health';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

async function checkHealth() {
  try {
    const res = await fetch(API_HEALTH, { signal: AbortSignal.timeout(15_000) });
    if (!res.ok) return { ok: false, reason: `HTTP ${res.status}` };
    const body = await res.json();
    const healthy =
      body?.status === 'ok' &&
      body?.services?.api === 'ok' &&
      body?.services?.database === 'ok';
    return { ok: healthy, reason: healthy ? 'ok' : JSON.stringify(body) };
  } catch (err) {
    return { ok: false, reason: err instanceof Error ? err.message : String(err) };
  }
}

console.log(`\n[recover] Checking ${API_HEALTH}`);
const health = await checkHealth();

if (health.ok) {
  console.log('[recover] API healthy — no action needed.');
  process.exit(0);
}

console.warn(`[recover] API unhealthy (${health.reason}) — triggering Railway redeploy…`);
const redeploy = spawnSync('node', ['scripts/railway-redeploy-all.mjs'], {
  cwd: root,
  stdio: 'inherit',
  shell: true,
});

if (redeploy.status !== 0) {
  console.error('[recover] Redeploy script failed');
  process.exit(redeploy.status ?? 1);
}

console.log('[recover] Redeploy triggered. Wait 3–5 min and re-run smoke-test.');
