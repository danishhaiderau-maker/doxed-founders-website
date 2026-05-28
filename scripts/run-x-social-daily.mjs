/**
 * Daily X social automation for @Bitbro4crypto:
 * 1. Sync doxxed founder tweets + repost to @Bitbro4crypto
 * 2. Scan trending paper buys + post to X + in-app alerts
 * 3. Scan 50%+ trader wins + post to X + in-app alerts
 *
 * Schedule once daily (e.g. 9:00 UTC):
 *   0 9 * * * node scripts/run-x-social-daily.mjs
 *
 * Requires API_URL and ADMIN_SYNC_JWT on Railway/cron runner.
 */
const apiUrl = (process.env.API_URL ?? 'http://127.0.0.1:4000').replace(/\/$/, '');
const adminJwt = process.env.ADMIN_SYNC_JWT?.trim();

if (!adminJwt) {
  console.error('Set ADMIN_SYNC_JWT (admin bearer token).');
  process.exit(1);
}

async function post(path) {
  const res = await fetch(`${apiUrl}/api${path}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${adminJwt}` },
  });
  const body = await res.text();
  console.log(path, res.status, body);
  return res.ok;
}

const founderOk = await post('/founder-updates/sync-x');
const socialOk = await post('/x-social/daily-run');

process.exit(founderOk && socialOk ? 0 : 1);
