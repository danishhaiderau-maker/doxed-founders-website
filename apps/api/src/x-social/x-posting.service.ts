import { Injectable, Logger } from '@nestjs/common';
import { XSocialPostKind, Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { buildOAuth1Header, readOAuth1Credentials, siteUrl } from './x-oauth1';
import { uploadTweetImage } from './x-media-upload.util';
import { XShareMediaService } from './x-share-media.service';
import {
  formatFounderRepostPost,
  formatTraderConvictionPost,
  formatTrendingBuysPost,
  trimTweet,
} from './x-post-copy.util';

type PostResult = { ok: true; tweetId: string; tweetUrl: string } | { ok: false; reason: string };

@Injectable()
export class XPostingService {
  private readonly logger = new Logger(XPostingService.name);
  private cachedUserId: string | null = null;

  constructor(
    private readonly prisma: PrismaService,
    private readonly shareMedia: XShareMediaService,
  ) {}

  isConfigured(): boolean {
    return readOAuth1Credentials().ok;
  }

  async repostFounderTweet(
    sourceTweetId: string,
    meta: {
      founderName: string;
      projectName: string;
      projectSlug: string;
      ticker?: string | null;
    },
  ): Promise<PostResult> {
    const dedupeKey = `founder_repost:${sourceTweetId}`;
    if (await this.alreadyPosted(dedupeKey)) {
      return { ok: false, reason: 'Already reposted' };
    }

    const quoteText = trimTweet(formatFounderRepostPost(meta));
    const quoted = await this.postTweet(quoteText, sourceTweetId);
    if (!quoted.ok) {
      const retweeted = await this.retweet(sourceTweetId);
      if (!retweeted.ok) return retweeted;
      await this.logPost(XSocialPostKind.FOUNDER_REPOST, dedupeKey, retweeted.tweetId, retweeted.tweetUrl, meta);
      return retweeted;
    }

    await this.logPost(XSocialPostKind.FOUNDER_REPOST, dedupeKey, quoted.tweetId, quoted.tweetUrl, meta);
    return quoted;
  }

  async postTrendingBuys(input: {
    projectId: string;
    projectName: string;
    ticker: string;
    slug: string;
    founderName: string | null;
    buyerCount: number;
    windowHours: number;
    totalInvestedUsd: number;
  }): Promise<PostResult> {
    const auth = readOAuth1Credentials();
    if (!auth.ok) return { ok: false, reason: auth.reason };

    const dayKey = new Date().toISOString().slice(0, 10);
    const dedupeKey = `trending:${input.projectId}:${dayKey}`;
    if (await this.alreadyPosted(dedupeKey)) {
      return { ok: false, reason: 'Already posted trending today' };
    }

    const text = trimTweet(
      formatTrendingBuysPost({
        brandHandle: auth.brandHandle,
        ...input,
      }),
    );

    const posted = await this.postTweet(text);
    if (posted.ok) {
      await this.logPost(XSocialPostKind.TRENDING_BUYS, dedupeKey, posted.tweetId, posted.tweetUrl, input);
    }
    return posted;
  }

  async postTraderConviction(input: {
    userId: string;
    displayName: string;
    projectId: string;
    projectName: string;
    ticker: string;
    slug: string;
    investedUsd: number;
    pnlUsd: number;
    pnlPercent: number;
    thesis: string | null;
    founderName: string | null;
    founderHandle: string | null;
    founderTweetId: string | null;
  }): Promise<PostResult> {
    const win = input.pnlPercent >= 0;
    const kind = win ? XSocialPostKind.TRADER_WIN : XSocialPostKind.TRADER_LOSS;
    const dedupeKey = `${win ? 'trader_win' : 'trader_loss'}:${input.userId}:${input.projectId}`;
    if (await this.alreadyPosted(dedupeKey)) {
      return { ok: false, reason: 'Already shared this trader result' };
    }

    const text = trimTweet(
      formatTraderConvictionPost({
        displayName: input.displayName,
        userId: input.userId,
        projectName: input.projectName,
        ticker: input.ticker,
        slug: input.slug,
        investedUsd: input.investedUsd,
        pnlUsd: input.pnlUsd,
        pnlPercent: input.pnlPercent,
        thesis: input.thesis,
        founderName: input.founderName,
        founderHandle: input.founderHandle,
      }),
    );

    const side = win ? 'pump' : 'dump';
    const imageBuffer = this.shareMedia.pickImageBuffer(side);
    let mediaIds: string[] | undefined;

    const auth = readOAuth1Credentials();
    if (imageBuffer && auth.ok) {
      const uploaded = await uploadTweetImage(auth.creds, imageBuffer, `${side}-share.png`);
      if (uploaded.ok) {
        mediaIds = [uploaded.mediaId];
      } else {
        this.logger.warn(`Share image upload skipped: ${uploaded.reason}`);
      }
    }

    let posted = await this.postTweet(text, input.founderTweetId ?? undefined, mediaIds);
    if (!posted.ok && mediaIds?.length && input.founderTweetId) {
      posted = await this.postTweet(text, undefined, mediaIds);
    }
    if (!posted.ok && mediaIds?.length) {
      posted = await this.postTweet(text, input.founderTweetId ?? undefined);
    }
    if (posted.ok) {
      await this.logPost(kind, dedupeKey, posted.tweetId, posted.tweetUrl, input);
    }
    return posted;
  }

  /** @deprecated use postTraderConviction */
  async postTraderWin(input: Parameters<XPostingService['postTraderConviction']>[0]): Promise<PostResult> {
    return this.postTraderConviction(input);
  }

  private async retweet(sourceTweetId: string): Promise<PostResult> {
    const auth = readOAuth1Credentials();
    if (!auth.ok) return { ok: false, reason: auth.reason };

    const userId = await this.getAuthenticatedUserId(auth.creds);
    if (!userId) return { ok: false, reason: 'Could not resolve @Bitbro4crypto user id' };

    const url = `https://api.twitter.com/2/users/${userId}/retweets`;
    const authorization = buildOAuth1Header('POST', url, auth.creds);

    const res = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: authorization,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ tweet_id: sourceTweetId }),
    });

    if (!res.ok) {
      const body = await res.text();
      this.logger.warn(`Retweet failed: ${res.status} ${body}`);
      return { ok: false, reason: `Retweet failed (${res.status})` };
    }

    const data = (await res.json()) as { data?: { retweeted?: boolean; rest_id?: string } };
    const tweetId = data.data?.rest_id ?? sourceTweetId;
    return {
      ok: true,
      tweetId,
      tweetUrl: `https://x.com/${auth.brandHandle}/status/${tweetId}`,
    };
  }

  private async postTweet(
    text: string,
    quoteTweetId?: string,
    mediaIds?: string[],
  ): Promise<PostResult> {
    const auth = readOAuth1Credentials();
    if (!auth.ok) return { ok: false, reason: auth.reason };

    const url = 'https://api.twitter.com/2/tweets';
    const authorization = buildOAuth1Header('POST', url, auth.creds);
    const body: {
      text: string;
      quote_tweet_id?: string;
      media?: { media_ids: string[] };
    } = { text };
    if (quoteTweetId) body.quote_tweet_id = quoteTweetId;
    if (mediaIds?.length) body.media = { media_ids: mediaIds };

    const res = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: authorization,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const errBody = await res.text();
      this.logger.warn(`Tweet post failed: ${res.status} ${errBody}`);
      return { ok: false, reason: `Tweet post failed (${res.status})` };
    }

    const data = (await res.json()) as { data?: { id: string } };
    const tweetId = data.data?.id;
    if (!tweetId) return { ok: false, reason: 'No tweet id returned' };

    return {
      ok: true,
      tweetId,
      tweetUrl: `https://x.com/${auth.brandHandle}/status/${tweetId}`,
    };
  }

  private async getAuthenticatedUserId(creds: import('./x-oauth1').OAuth1Credentials): Promise<string | null> {
    if (this.cachedUserId) return this.cachedUserId;

    const url = 'https://api.twitter.com/2/users/me';
    const authorization = buildOAuth1Header('GET', url, creds);
    const res = await fetch(url, { headers: { Authorization: authorization } });
    if (!res.ok) return null;

    const data = (await res.json()) as { data?: { id: string } };
    this.cachedUserId = data.data?.id ?? null;
    return this.cachedUserId;
  }

  private async alreadyPosted(dedupeKey: string): Promise<boolean> {
    const row = await this.prisma.xSocialPostLog.findUnique({ where: { dedupeKey } });
    return Boolean(row);
  }

  private async logPost(
    kind: XSocialPostKind,
    dedupeKey: string,
    tweetId: string,
    tweetUrl: string,
    metadata: unknown,
  ) {
    await this.prisma.xSocialPostLog.create({
      data: {
        kind,
        dedupeKey,
        tweetId,
        tweetUrl,
        metadata: metadata as Prisma.InputJsonValue,
      },
    });
  }
}
