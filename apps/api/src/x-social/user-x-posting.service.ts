import { BadRequestException, Injectable, Logger } from '@nestjs/common';
import { appendPlatformXShareFooter } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { buildOAuth1Header } from './x-oauth1';

export type UserTweetResult =
  | { ok: true; tweetId: string; tweetUrl: string }
  | { ok: false; reason: string };

@Injectable()
export class UserXPostingService {
  private readonly logger = new Logger(UserXPostingService.name);

  constructor(private readonly prisma: PrismaService) {}

  async canUserPost(userId: string) {
    const account = await this.prisma.oAuthAccount.findFirst({
      where: { userId, provider: 'twitter' },
      include: { user: { select: { twitterHandle: true } } },
    });
    return {
      connected: Boolean(account),
      canPost: Boolean(account?.accessToken && account.accessTokenSecret),
      twitterHandle: account?.user.twitterHandle ?? null,
    };
  }

  async postTweet(userId: string, text: string, mediaIds?: string[]): Promise<UserTweetResult> {
    const account = await this.prisma.oAuthAccount.findFirst({
      where: { userId, provider: 'twitter' },
      include: { user: { select: { twitterHandle: true } } },
    });
    if (!account?.accessToken || !account.accessTokenSecret) {
      return { ok: false, reason: 'X not connected — sign in with X to enable posting.' };
    }

    const consumerKey = process.env.TWITTER_API_KEY?.trim();
    const consumerSecret = process.env.TWITTER_API_SECRET?.trim();
    if (!consumerKey || !consumerSecret) {
      return { ok: false, reason: 'Platform X API not configured.' };
    }

    const creds = {
      consumerKey,
      consumerSecret,
      accessToken: account.accessToken,
      accessTokenSecret: account.accessTokenSecret,
    };

    const url = 'https://api.twitter.com/2/tweets';
    const authorization = buildOAuth1Header('POST', url, creds);
    const body: { text: string; media?: { media_ids: string[] } } = {
      text: appendPlatformXShareFooter(text).slice(0, 280),
    };
    if (mediaIds?.length) body.media = { media_ids: mediaIds };

    const res = await fetch(url, {
      method: 'POST',
      headers: { Authorization: authorization, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const errBody = await res.text();
      this.logger.warn(`User tweet failed: ${res.status} ${errBody}`);
      throw new BadRequestException(
        'Could not post to X. Sign out and sign in with X again if your token expired.',
      );
    }

    const data = (await res.json()) as { data?: { id: string } };
    const tweetId = data.data?.id;
    if (!tweetId) return { ok: false, reason: 'X did not return a tweet id' };

    const handle = account.user.twitterHandle?.replace(/^@/, '') ?? 'i';
    return { ok: true, tweetId, tweetUrl: `https://x.com/${handle}/status/${tweetId}` };
  }
}
