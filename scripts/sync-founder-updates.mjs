/**
 * Sync founder updates from X (Twitter) API once per day via cron.
 * Requires TWITTER_BEARER_TOKEN on Railway and ADMIN_SYNC_JWT for auth.
 *
 * Daily cron (Railway / GitHub Actions) — 9:00 UTC:
 *   0 9 * * * node scripts/sync-founder-updates.mjs
 *
 * Estimated cost at ~20 founders × 5 tweets/day: ~$0.50–2/day on pay-per-use X API.
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
