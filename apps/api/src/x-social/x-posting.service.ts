import { Injectable, Logger } from '@nestjs/common';
import { XSocialPostKind, Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { buildOAuth1Header, readOAuth1Credentials, siteUrl } from './x-oauth1';

type PostResult = { ok: true; tweetId: string; tweetUrl: string } | { ok: false; reason: string };

@Injectable()
export class XPostingService {
  private readonly logger = new Logger(XPostingService.name);
  private cachedUserId: string | null = null;

  constructor(private readonly prisma: PrismaService) {}

  isConfigured(): boolean {
    return readOAuth1Credentials().ok;
  }

  async repostFounderTweet(
    sourceTweetId: string,
    meta: { founderName: string; projectName: string; projectSlug: string },
  ): Promise<PostResult> {
    const dedupeKey = `founder_repost:${sourceTweetId}`;
    if (await this.alreadyPosted(dedupeKey)) {
      return { ok: false, reason: 'Already reposted' };
    }

    const quoteText = this.trimTweet(
      `📢 Doxxed founder update — ${meta.projectName}\n` +
        `Founder: ${meta.founderName}\n` +
        `Track verified builders → ${siteUrl()}/project/${meta.projectSlug}\n` +
        `#doxxed #crypto`,
    );

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
  }): Promise<PostResult> {
    const auth = readOAuth1Credentials();
    if (!auth.ok) return { ok: false, reason: auth.reason };

    const dayKey = new Date().toISOString().slice(0, 10);
    const dedupeKey = `trending:${input.projectId}:${dayKey}`;
    if (await this.alreadyPosted(dedupeKey)) {
      return { ok: false, reason: 'Already posted trending today' };
    }

    const text = this.trimTweet(
      `🐋 Hot paper buys on @${auth.brandHandle} tracker\n` +
        `$${input.ticker} — ${input.projectName}\n` +
        `${input.buyerCount} traders bought in ${input.windowHours}h on ${siteUrl()}\n` +
        (input.founderName ? `Doxxed founder: ${input.founderName} ✅\n` : '') +
        `${siteUrl()}/project/${input.slug}\n` +
        `#doxxed #crypto #whaletracker`,
    );

    const posted = await this.postTweet(text);
    if (posted.ok) {
      await this.logPost(XSocialPostKind.TRENDING_BUYS, dedupeKey, posted.tweetId, posted.tweetUrl, input);
    }
    return posted;
  }

  async postTraderWin(input: {
    userId: string;
    displayName: string;
    projectId: string;
    projectName: string;
    ticker: string;
    slug: string;
    pnlPercent: number;
    thesis: string | null;
    founderName: string | null;
    founderVideoUrl: string | null;
    founderTwitter: string | null;
  }): Promise<PostResult> {
    const dedupeKey = `trader_win:${input.userId}:${input.projectId}`;
    if (await this.alreadyPosted(dedupeKey)) {
      return { ok: false, reason: 'Already shared this trader win' };
    }

    const lines = [
      `🔥 +${Math.round(input.pnlPercent)}% paper gain on $${input.ticker}`,
      `${input.projectName} · ${siteUrl()}/project/${input.slug}`,
      `Trader: ${input.displayName} → ${siteUrl()}/portfolio/${input.userId}`,
    ];
    if (input.founderName) {
      lines.push(`Doxxed founder: ${input.founderName}`);
    }
    if (input.founderTwitter) {
      lines.push(`Founder X: ${input.founderTwitter}`);
    }
    if (input.founderVideoUrl) {
      lines.push(`Founder proof: ${input.founderVideoUrl}`);
    }
    if (input.thesis) {
      lines.push(`Thesis: ${input.thesis}`);
    }
    lines.push(`Discover doxxed founders on ${siteUrl()}`);

    const posted = await this.postTweet(this.trimTweet(lines.join('\n')));
    if (posted.ok) {
      await this.logPost(XSocialPostKind.TRADER_WIN, dedupeKey, posted.tweetId, posted.tweetUrl, input);
    }
    return posted;
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

  private async postTweet(text: string, quoteTweetId?: string): Promise<PostResult> {
    const auth = readOAuth1Credentials();
    if (!auth.ok) return { ok: false, reason: auth.reason };

    const url = 'https://api.twitter.com/2/tweets';
    const authorization = buildOAuth1Header('POST', url, auth.creds);
    const body: { text: string; quote_tweet_id?: string } = { text };
    if (quoteTweetId) body.quote_tweet_id = quoteTweetId;

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

  private trimTweet(text: string, max = 270): string {
    const cleaned = text.replace(/\r\n/g, '\n').trim();
    if (cleaned.length <= max) return cleaned;
    return `${cleaned.slice(0, max - 1)}…`;
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
