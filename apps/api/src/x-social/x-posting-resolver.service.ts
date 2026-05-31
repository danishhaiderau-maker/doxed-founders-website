import { Injectable, Logger } from '@nestjs/common';
import { appendPlatformXShareFooter, fitXShareTextWithFooter } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { buildOAuth1Header, verifyOAuth1Credentials } from './x-oauth1';
import {
  looksLikeOAuth2RefreshToken,
  postTweetOAuth2,
  refreshOAuth2AccessToken,
  verifyOAuth2AccessToken,
} from './x-oauth2';

type TwitterAccount = {
  id: string;
  accessToken: string | null;
  accessTokenSecret: string | null;
  user: { twitterHandle: string | null };
};

@Injectable()
export class XPostingResolverService {
  private readonly logger = new Logger(XPostingResolverService.name);

  constructor(private readonly prisma: PrismaService) {}

  async getConnectionStatus(userId: string) {
    const account = await this.loadAccount(userId);
    if (!account?.accessToken) {
      return {
        connected: false,
        canPostInstantly: false,
        tokenExpired: false,
        twitterHandle: null as string | null,
        message: 'Connect X at sign-in to post instantly — no download or paste.',
      };
    }

    const oauth1Ok = await this.tryOAuth1Verify(account);
    if (oauth1Ok.ok) {
      return {
        connected: true,
        canPostInstantly: true,
        tokenExpired: false,
        twitterHandle: account.user.twitterHandle,
        message: 'Post Proof of Conviction to your X in one tap.',
      };
    }

    const oauth2Ok = await this.tryOAuth2Verify(account);
    if (oauth2Ok.ok) {
      return {
        connected: true,
        canPostInstantly: true,
        tokenExpired: false,
        twitterHandle: account.user.twitterHandle ?? oauth2Ok.username ?? null,
        message: 'Post Proof of Conviction to your X in one tap.',
      };
    }

    const expired = oauth1Ok.expired || oauth2Ok.expired;
    return {
      connected: true,
      canPostInstantly: false,
      tokenExpired: expired,
      twitterHandle: account.user.twitterHandle,
      message: expired
        ? 'X session expired — reconnect X to post in one tap (composer still works).'
        : 'Reconnect X to enable one-click posting.',
    };
  }

  async postTweet(
    userId: string,
    text: string,
    mediaIds?: string[],
  ): Promise<
    | { ok: true; tweetId: string; tweetUrl: string; handle: string }
    | { ok: false; reason: string }
  > {
    const account = await this.loadAccount(userId);
    if (!account?.accessToken) {
      return { ok: false, reason: 'X not connected — sign in with X to enable posting.' };
    }

    const payload = fitXShareTextWithFooter(text);
    const handle = account.user.twitterHandle?.replace(/^@/, '') ?? 'i';

    const oauth1 = await this.postOAuth1(account, payload, mediaIds);
    if (oauth1.ok) {
      return { ok: true, tweetId: oauth1.tweetId, tweetUrl: `https://x.com/${handle}/status/${oauth1.tweetId}`, handle };
    }

    const oauth2 = await this.postOAuth2(account, payload, mediaIds);
    if (oauth2.ok) {
      return { ok: true, tweetId: oauth2.tweetId, tweetUrl: `https://x.com/${handle}/status/${oauth2.tweetId}`, handle };
    }

    return {
      ok: false,
      reason:
        oauth2.reason ??
        'Could not post to X. Reconnect X from the share modal, or use Open X composer.',
    };
  }

  oauth1Credentials(account: TwitterAccount) {
    const consumerKey = process.env.TWITTER_API_KEY?.trim();
    const consumerSecret = process.env.TWITTER_API_SECRET?.trim();
    if (!consumerKey || !consumerSecret || !account.accessToken || !account.accessTokenSecret) {
      return null;
    }
    if (looksLikeOAuth2RefreshToken(account.accessTokenSecret)) return null;
    return {
      consumerKey,
      consumerSecret,
      accessToken: account.accessToken,
      accessTokenSecret: account.accessTokenSecret,
    };
  }

  private async loadAccount(userId: string): Promise<TwitterAccount | null> {
    return this.prisma.oAuthAccount.findFirst({
      where: { userId, provider: 'twitter' },
      include: { user: { select: { twitterHandle: true } } },
    });
  }

  private async tryOAuth1Verify(account: TwitterAccount) {
    const creds = this.oauth1Credentials(account);
    if (!creds) return { ok: false, expired: false };
    const check = await verifyOAuth1Credentials(creds);
    return { ok: check.ok, expired: Boolean(check.expired) };
  }

  private async tryOAuth2Verify(account: TwitterAccount) {
    let accessToken = account.accessToken!;
    let check = await verifyOAuth2AccessToken(accessToken);
    if (!check.ok && check.expired && looksLikeOAuth2RefreshToken(account.accessTokenSecret)) {
      const refreshed = await refreshOAuth2AccessToken(account.accessTokenSecret!);
      if (refreshed.ok) {
        accessToken = refreshed.accessToken;
        await this.prisma.oAuthAccount.update({
          where: { id: account.id },
          data: {
            accessToken: refreshed.accessToken,
            accessTokenSecret: refreshed.refreshToken ?? account.accessTokenSecret,
          },
        });
        check = await verifyOAuth2AccessToken(accessToken);
      }
    }
    return { ok: check.ok, expired: Boolean(check.expired), username: check.username };
  }

  private async postOAuth1(account: TwitterAccount, text: string, mediaIds?: string[]) {
    const creds = this.oauth1Credentials(account);
    if (!creds) return { ok: false as const };

    const url = 'https://api.twitter.com/2/tweets';
    const authorization = buildOAuth1Header('POST', url, creds);
    const body: { text: string; media?: { media_ids: string[] } } = { text };
    if (mediaIds?.length) body.media = { media_ids: mediaIds };

    const res = await fetch(url, {
      method: 'POST',
      headers: { Authorization: authorization, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      this.logger.warn(`OAuth1 tweet failed: ${res.status}`);
      return { ok: false as const };
    }
    const data = (await res.json()) as { data?: { id: string } };
    if (!data.data?.id) return { ok: false as const };
    return { ok: true as const, tweetId: data.data.id };
  }

  private async postOAuth2(account: TwitterAccount, text: string, mediaIds?: string[]) {
    let accessToken = account.accessToken!;
    let result = await postTweetOAuth2(accessToken, text, mediaIds);
    if (!result.ok && result.status === 401 && looksLikeOAuth2RefreshToken(account.accessTokenSecret)) {
      const refreshed = await refreshOAuth2AccessToken(account.accessTokenSecret!);
      if (refreshed.ok) {
        accessToken = refreshed.accessToken;
        await this.prisma.oAuthAccount.update({
          where: { id: account.id },
          data: {
            accessToken: refreshed.accessToken,
            accessTokenSecret: refreshed.refreshToken ?? account.accessTokenSecret,
          },
        });
        result = await postTweetOAuth2(accessToken, text, mediaIds);
      }
    }
    if (!result.ok) {
      return { ok: false as const, reason: 'X rejected the post — reconnect X or use composer.' };
    }
    return { ok: true as const, tweetId: result.tweetId };
  }
}
