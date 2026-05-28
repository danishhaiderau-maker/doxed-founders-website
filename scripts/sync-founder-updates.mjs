/**
 * Sync founder updates from X (Twitter) API every ~6 hours via cron.
 * Requires TWITTER_BEARER_TOKEN and DATABASE_URL.
 *
 * Example cron (Railway/Vercel cron/GitHub Actions):
 *   0 0,6,12,18 * * * node scripts/sync-founder-updates.mjs
 */
const apiUrl = (process.env.API_URL ?? 'http://127.0.0.1:4000').replace(/\/$/, '');
const adminJwt = process.env.ADMIN_SYNC_JWT?.trim();

if (!adminJwt) {
  console.error('Set ADMIN_SYNC_JWT to an admin user JWT (or call POST /founder-updates/sync-x as admin).');
  process.exit(1);
}

const res = await fetch(`${apiUrl}/api/founder-updates/sync-x`, {
  method: 'POST',
  headers: { Authorization: `Bearer ${adminJwt}` },
});

const body = await res.text();
console.log(res.status, body);
process.exit(res.ok ? 0 : 1);
