/**
 * Production housekeeping: smoke test + status checks + reminders.
 *   npm run housekeeping
 */
const apiUrl = (process.env.API_URL ?? 'https://doxed-founders-website-production.up.railway.app').replace(/\/$/, '');
const webUrl = process.env.PUBLIC_SITE_URL ?? 'https://doxxedcrypto.digital';

const checks = [];

async function check(name, fn) {
  try {
    const result = await fn();
    checks.push({ name, ok: true, detail: result });
    console.log(`  OK  ${name}${result ? ` — ${result}` : ''}`);
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    checks.push({ name, ok: false, detail });
    console.log(`  FAIL ${name} — ${detail}`);
  }
}

console.log('=== Doxxed Crypto — housekeeping ===\n');
console.log(`API: ${apiUrl}`);
console.log(`Web: ${webUrl}\n`);

console.log('Health:');
await check('API /health/live', async () => {
  const r = await fetch(`${apiUrl}/api/health/live`, { signal: AbortSignal.timeout(15000) });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const j = await r.json();
  return j.status ?? 'ok';
});

await check('API /health (db)', async () => {
  const r = await fetch(`${apiUrl}/api/health`, { signal: AbortSignal.timeout(20000) });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const j = await r.json();
  return `db=${j.database ?? j.db ?? '?'}`;
});

console.log('\nFeatures:');
await check('X automation status', async () => {
  const r = await fetch(`${apiUrl}/api/x-social/status`, { signal: AbortSignal.timeout(15000) });
  const j = await r.json();
  if (!j.founderReadSync?.ready) throw new Error('TWITTER_BEARER_TOKEN missing or invalid');
  if (!j.fullyAutomated) return 'partial — check ADMIN_SYNC_JWT or posting tokens';
  return 'fullyAutomated';
});

await check('Web /login', async () => {
  const r = await fetch(`${webUrl}/login`, { signal: AbortSignal.timeout(15000) });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return `${r.status}`;
});

await check('Web /settings/builder', async () => {
  const r = await fetch(`${webUrl}/settings/builder`, { signal: AbortSignal.timeout(15000) });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return `${r.status}`;
});

const failed = checks.filter((c) => !c.ok).length;
console.log('\n--- Reminders ---');
console.log('• Admin TOTP 500? Run: npm run fix:admin-2fa  (or use recovery code on login)');
console.log('• X feed empty? Refresh TWITTER_BEARER_TOKEN in vault, then: npm run finish:x-production');
console.log('• Deploy pending? npm run redeploy:railway && npm run deploy:web');
console.log('• Privacy stack docs: docs/PRIVACY_STACK.md');

console.log(`\nResult: ${checks.length - failed}/${checks.length} checks passed`);
process.exit(failed ? 1 : 0);
