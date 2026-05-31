import {
  BadRequestException,
  Injectable,
  Logger,
  NotFoundException,
} from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { uploadTweetImage } from '../x-social/x-media-upload.util';
import { XPostingResolverService } from '../x-social/x-posting-resolver.service';
import { XShareMediaService } from '../x-social/x-share-media.service';

type PostResult = { ok: true; tweetId: string; tweetUrl: string } | { ok: false; reason: string };

@Injectable()
export class ConvictionShareService {
  private readonly logger = new Logger(ConvictionShareService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly shareMedia: XShareMediaService,
    private readonly xPosting: XPostingResolverService,
  ) {}

  async getXConnectionStatus(userId: string) {
    return this.xPosting.getConnectionStatus(userId);
  }

  async postProofOfConviction(
    userId: string,
    input: { projectId: string; text: string; pnlPercent: number },
  ): Promise<PostResult> {
    const account = await this.prisma.oAuthAccount.findFirst({
      where: { userId, provider: 'twitter' },
    });
    if (!account?.accessToken) {
      throw new BadRequestException(
        'Connect your X account to post instantly. Use Open X composer or reconnect X.',
      );
    }

    const creds = this.xPosting.oauth1Credentials({
      ...account,
      user: { twitterHandle: null },
    });

    const side = input.pnlPercent >= 0 ? 'pump' : 'dump';
    const imageBuffer = this.shareMedia.pickImageBuffer(side);
    let mediaIds: string[] | undefined;

    if (imageBuffer && creds) {
      const uploaded = await uploadTweetImage(creds, imageBuffer, 'proof-of-conviction.png');
      if (uploaded.ok) {
        mediaIds = [uploaded.mediaId];
      } else {
        this.logger.warn(`User share image upload skipped: ${uploaded.reason}`);
      }
    }

    const result = await this.xPosting.postTweet(userId, input.text, mediaIds);
    if (!result.ok) {
      throw new BadRequestException(result.reason);
    }

    return {
      ok: true,
      tweetId: result.tweetId,
      tweetUrl: result.tweetUrl,
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
