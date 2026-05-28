import { Injectable, Logger } from '@nestjs/common';
import { readOAuth1Credentials } from './x-oauth1';
import { XShareMediaService } from './x-share-media.service';

export function getXAutomationStatus(shareMedia?: XShareMediaService) {
  const bearer = Boolean(process.env.TWITTER_BEARER_TOKEN?.trim());
  const posting = readOAuth1Credentials();
  const twitterClientId =
    process.env.TWITTER_CLIENT_ID?.trim() || process.env.TWITTER_API_KEY?.trim();
  const twitterClientSecret =
    process.env.TWITTER_CLIENT_SECRET?.trim() || process.env.TWITTER_API_SECRET?.trim();
  const loginReady = Boolean(twitterClientId && twitterClientSecret);
  const cronJwt = Boolean(process.env.ADMIN_SYNC_JWT?.trim());
  const siteUrl = (process.env.PUBLIC_SITE_URL ?? 'https://doxxedcrypto.digital').replace(/\/$/, '');
  const shareImages = shareMedia?.imageCount() ?? { pump: 0, dump: 0 };

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
        ? 'API keys present — ensure same TWITTER_CLIENT_ID/SECRET on Vercel; verify /api/auth/providers shows twitter'
        : 'Set TWITTER_CLIENT_ID + TWITTER_CLIENT_SECRET on Vercel for web login',
    },
    dailyCron: {
      ready: cronJwt,
      hint: cronJwt
        ? 'ADMIN_SYNC_JWT set — run npm run sync:x-social-daily or GitHub Actions cron'
        : 'Set ADMIN_SYNC_JWT (admin API token) for automated daily sync',
    },
    shareImages: {
      pump: shareImages.pump,
      dump: shareImages.dump,
      ready: shareImages.pump > 0 && shareImages.dump > 0,
      hint:
        shareImages.pump > 0 && shareImages.dump > 0
          ? `${shareImages.pump} pump + ${shareImages.dump} images for user manual flex share (not auto-posted from @Bitbro4crypto)`
          : 'Run npm run prepare:x-share-images to bundle pump/dump meme images',
    },
    brandAccountPosts: {
      founderUpdates: true,
      trendingBuys24h: true,
      traderWinLoss: process.env.X_AUTO_POST_TRADER_RESULTS === 'true',
      hint: '@Bitbro4crypto only auto-posts founder updates + 24h trending clusters unless X_AUTO_POST_TRADER_RESULTS=true',
    },
    fullyAutomated: bearer && posting.ok && cronJwt,
  };
}
