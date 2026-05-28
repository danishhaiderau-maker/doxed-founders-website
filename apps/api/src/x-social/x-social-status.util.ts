import { readOAuth1Credentials } from './x-oauth1';

export function getXAutomationStatus() {
  const bearer = Boolean(process.env.TWITTER_BEARER_TOKEN?.trim());
  const posting = readOAuth1Credentials();
  const twitterClientId =
    process.env.TWITTER_CLIENT_ID?.trim() || process.env.TWITTER_API_KEY?.trim();
  const twitterClientSecret =
    process.env.TWITTER_CLIENT_SECRET?.trim() || process.env.TWITTER_API_SECRET?.trim();
  const loginReady = Boolean(twitterClientId && twitterClientSecret);
  const cronJwt = Boolean(process.env.ADMIN_SYNC_JWT?.trim());
  const siteUrl = (process.env.PUBLIC_SITE_URL ?? 'https://doxxedcrypto.digital').replace(/\/$/, '');

  return {
    brandHandle: process.env.X_BRAND_HANDLE?.replace(/^@/, '') ?? 'Bitbro4crypto',
    siteUrl,
    founderReadSync: {
      ready: bearer,
      hint: bearer
        ? 'Daily founder tweet scan enabled'
        : 'Set TWITTER_BEARER_TOKEN on Railway',
    },
    brandPosting: {
      ready: posting.ok,
      hint: posting.ok
        ? `@${posting.brandHandle} posting enabled`
        : posting.reason,
    },
    userXLogin: {
      ready: loginReady,
      callbackUrl: `${siteUrl}/api/auth/callback/twitter`,
      hint: loginReady
        ? 'Set same TWITTER_CLIENT_ID/SECRET on Vercel for web login'
        : 'Set TWITTER_CLIENT_ID + TWITTER_CLIENT_SECRET (OAuth 2.0) on Vercel',
    },
    dailyCron: {
      ready: cronJwt,
      hint: cronJwt
        ? 'ADMIN_SYNC_JWT set — run npm run sync:x-social-daily or GitHub Actions cron'
        : 'Set ADMIN_SYNC_JWT (admin API token) for automated daily sync',
    },
    fullyAutomated: bearer && posting.ok && loginReady && cronJwt,
  };
}
