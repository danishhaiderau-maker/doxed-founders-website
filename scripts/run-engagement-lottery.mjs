/**
 * Daily engagement lottery — credits $500–$2,000 paper cash to ~0.2% of active users.
 * Schedule once daily (e.g. 10:00 UTC):
 *   0 10 * * * node scripts/run-engagement-lottery.mjs
 *
 * Requires API_URL and ADMIN_SYNC_JWT on Railway/cron runner.
 */
const apiUrl = (process.env.API_URL ?? 'http://127.0.0.1:4000').replace(/\/$/, '');
const adminJwt = process.env.ADMIN_SYNC_JWT?.trim();

if (!adminJwt) {
  console.error('Set ADMIN_SYNC_JWT (admin bearer token).');
  process.exit(1);
}

const res = await fetch(`${apiUrl}/api/engagement-rewards/daily-lottery`, {
  method: 'POST',
  headers: { Authorization: `Bearer ${adminJwt}` },
});
const body = await res.text();
console.log('engagement-rewards/daily-lottery', res.status, body);
process.exit(res.ok ? 0 : 1);
