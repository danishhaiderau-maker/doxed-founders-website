import {
  BadRequestException,
  Injectable,
  Logger,
  NotFoundException,
} from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { buildOAuth1Header, verifyOAuth1Credentials } from '../x-social/x-oauth1';
import { uploadTweetImage } from '../x-social/x-media-upload.util';
import { XShareMediaService } from '../x-social/x-share-media.service';

type PostResult = { ok: true; tweetId: string; tweetUrl: string } | { ok: false; reason: string };

@Injectable()
export class ConvictionShareService {
  private readonly logger = new Logger(ConvictionShareService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly shareMedia: XShareMediaService,
  ) {}

  async getXConnectionStatus(userId: string) {
    const account = await this.prisma.oAuthAccount.findFirst({
      where: { userId, provider: 'twitter' },
      include: { user: { select: { twitterHandle: true } } },
    });
    const hasTokens = Boolean(account?.accessToken && account?.accessTokenSecret);
    let canPostInstantly = false;
    let tokenExpired = false;

    if (hasTokens && process.env.TWITTER_API_KEY && process.env.TWITTER_API_SECRET) {
      const check = await verifyOAuth1Credentials({
        consumerKey: process.env.TWITTER_API_KEY.trim(),
        consumerSecret: process.env.TWITTER_API_SECRET.trim(),
        accessToken: account!.accessToken!,
        accessTokenSecret: account!.accessTokenSecret!,
      });
      canPostInstantly = check.ok;
      tokenExpired = Boolean(check.expired);
    }

    return {
      connected: Boolean(account),
      canPostInstantly,
      tokenExpired,
      twitterHandle: account?.user.twitterHandle ?? null,
      message: canPostInstantly
        ? 'Post Proof of Conviction to your X in one tap.'
        : tokenExpired
          ? 'Your X token expired — sign out and sign in with X again to post instantly.'
          : account
            ? 'Reconnect with X to enable one-click posting.'
            : 'Connect X at sign-in to post instantly — no download or paste.',
    };
  }

  async postProofOfConviction(
    userId: string,
    input: { projectId: string; text: string; pnlPercent: number },
  ): Promise<PostResult> {
    const account = await this.prisma.oAuthAccount.findFirst({
      where: { userId, provider: 'twitter' },
    });
    if (!account?.accessToken || !account.accessTokenSecret) {
      throw new BadRequestException(
        'Connect your X account to post instantly. Sign out and sign in with X, or use Share via composer.',
      );
    }

    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: { twitterHandle: true },
    });

    const creds = {
      consumerKey: process.env.TWITTER_API_KEY!.trim(),
      consumerSecret: process.env.TWITTER_API_SECRET!.trim(),
      accessToken: account.accessToken,
      accessTokenSecret: account.accessTokenSecret,
    };

    const side = input.pnlPercent >= 0 ? 'pump' : 'dump';
    const imageBuffer = this.shareMedia.pickImageBuffer(side);
    let mediaIds: string[] | undefined;

    if (imageBuffer) {
      const uploaded = await uploadTweetImage(creds, imageBuffer, 'proof-of-conviction.png');
      if (uploaded.ok) {
        mediaIds = [uploaded.mediaId];
      } else {
        this.logger.warn(`User share image upload skipped: ${uploaded.reason}`);
      }
    }

    const url = 'https://api.twitter.com/2/tweets';
    const authorization = buildOAuth1Header('POST', url, creds);
    const body: { text: string; media?: { media_ids: string[] } } = {
      text: input.text.slice(0, 280),
    };
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
      this.logger.warn(`User tweet failed: ${res.status} ${errBody}`);
      throw new BadRequestException(
        'Could not post to X. Your token may have expired — sign out and sign in with X again.',
      );
    }

    const data = (await res.json()) as { data?: { id: string } };
    const tweetId = data.data?.id;
    if (!tweetId) {
      throw new BadRequestException('X did not return a tweet id');
    }

    const handle = user?.twitterHandle?.replace(/^@/, '') ?? 'i';
    return {
      ok: true,
      tweetId,
      tweetUrl: `https://x.com/${handle}/status/${tweetId}`,
    };
  }

  async getPositionConviction(userId: string, projectId: string) {
    const portfolio = await this.prisma.paperPortfolio.findUnique({
      where: { userId },
      include: {
        positions: {
          where: { projectId },
          include: {
            project: {
              include: {
                metrics: true,
                feedPosts: {
                  where: { userId },
                  orderBy: { createdAt: 'asc' },
                  take: 1,
                  select: { initialComment: true, createdAt: true, id: true },
                },
              },
            },
          },
        },
      },
    });

    const position = portfolio?.positions[0];
    if (!position) throw new NotFoundException('Position not found');

    const feed = position.project.feedPosts[0];
    const price = Number(position.project.metrics?.priceUsd ?? position.avgBuyPrice);

    return {
      ticker: position.project.ticker,
      projectName: position.project.name,
      entryPrice: Number(position.avgBuyPrice),
      currentPrice: price,
      returnPct:
        Number(position.avgBuyPrice) > 0
          ? ((price - Number(position.avgBuyPrice)) / Number(position.avgBuyPrice)) * 100
          : 0,
      thesis: position.convictionThesis ?? feed?.initialComment ?? null,
      catalyst: position.convictionCatalyst ?? null,
      targetPrice: position.convictionTargetUsd ? Number(position.convictionTargetUsd) : null,
      timeHorizon: position.convictionTimeHorizon ?? null,
      recordedAt: (position.convictionRecordedAt ?? feed?.createdAt)?.toISOString() ?? null,
      feedPostId: feed?.id ?? null,
    };
  }
}
