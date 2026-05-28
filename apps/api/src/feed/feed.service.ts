import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { PaperTradeSide, Prisma } from '@prisma/client';
import { POINTS } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { PointsService } from '../points/points.service';
import { AddInitialCommentDto, CreateFeedCommentDto } from './dto/feed.dto';

const HIGHLIGHT_HOURS = 6;
const ACTIVITY_WINDOW_HOURS = 7;
const MAX_HIGHLIGHTED = 3;

@Injectable()
export class FeedService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly points: PointsService,
  ) {}

  async createPostForTrade(
    paperTradeId: string,
    userId: string,
    projectId: string,
    initialComment?: string,
  ) {
    const trimmed = initialComment?.trim();
    const now = new Date();

    return this.prisma.feedPost.create({
      data: {
        paperTradeId,
        userId,
        projectId,
        initialComment: trimmed || null,
        commentCount: trimmed ? 1 : 0,
        lastCommentAt: trimmed ? now : null,
      },
    });
  }

  async refreshHighlights() {
    const now = new Date();

    await this.prisma.feedPost.updateMany({
      where: {
        highlightedUntil: { lt: now },
      },
      data: { highlightedUntil: null },
    });

    const windowStart = new Date(
      now.getTime() - ACTIVITY_WINDOW_HOURS * 60 * 60 * 1000,
    );

    const candidates = await this.prisma.feedPost.findMany({
      where: {
        commentCount: { gte: 1 },
        OR: [
          { lastCommentAt: { gte: windowStart } },
          { createdAt: { gte: windowStart } },
        ],
      },
      orderBy: [{ commentCount: 'desc' }, { lastCommentAt: 'desc' }],
      take: MAX_HIGHLIGHTED,
    });

    const highlightedUntil = new Date(
      now.getTime() + HIGHLIGHT_HOURS * 60 * 60 * 1000,
    );

    for (const post of candidates) {
      await this.prisma.feedPost.update({
        where: { id: post.id },
        data: { highlightedUntil },
      });
    }
  }

  async getFeed(filter: 'recent' | 'discussed' | 'highlighted' = 'recent') {
    await this.refreshHighlights();

    if (filter === 'highlighted') {
      const posts = await this.loadPosts({
        where: {
          highlightedUntil: { gt: new Date() },
        },
        orderBy: [{ commentCount: 'desc' }, { createdAt: 'desc' }],
      });
      return { filter, posts };
    }

    if (filter === 'discussed') {
      const posts = await this.loadPosts({
        orderBy: [{ commentCount: 'desc' }, { createdAt: 'desc' }],
        take: 50,
      });
      return { filter, posts };
    }

    const posts = await this.loadPosts({
      orderBy: { createdAt: 'desc' },
      take: 50,
    });
    return { filter, posts };
  }

  async getComments(feedPostId: string) {
    const post = await this.prisma.feedPost.findUnique({
      where: { id: feedPostId },
    });
    if (!post) {
      throw new NotFoundException('Feed post not found');
    }

    const comments = await this.prisma.feedComment.findMany({
      where: { feedPostId },
      orderBy: { createdAt: 'asc' },
      include: {
        user: { select: { id: true, name: true, email: true, avatarUrl: true } },
      },
    });

    return {
      feedPostId,
      initialComment: post.initialComment,
      comments: comments.map((c) => ({
        id: c.id,
        body: c.body,
        createdAt: c.createdAt,
        user: {
          id: c.user.id,
          name: c.user.name ?? c.user.email.split('@')[0],
          avatarUrl: c.user.avatarUrl,
        },
      })),
    };
  }

  async addComment(feedPostId: string, dto: CreateFeedCommentDto) {
    const post = await this.prisma.feedPost.findUnique({
      where: { id: feedPostId },
    });
    if (!post) {
      throw new NotFoundException('Feed post not found');
    }

    const body = dto.body.trim();
    if (!body) {
      throw new BadRequestException('Comment cannot be empty');
    }

    const comment = await this.prisma.$transaction(async (tx) => {
      const created = await tx.feedComment.create({
        data: {
          feedPostId,
          userId: dto.userId,
          body,
        },
        include: {
          user: { select: { id: true, name: true, email: true, avatarUrl: true } },
        },
      });

      await tx.feedPost.update({
        where: { id: feedPostId },
        data: {
          commentCount: { increment: 1 },
          lastCommentAt: new Date(),
        },
      });

      return created;
    });

    await this.refreshHighlights();

    await this.points.award(dto.userId, POINTS.FEED_COMMENT);

    return {
      id: comment.id,
      body: comment.body,
      createdAt: comment.createdAt,
      user: {
        id: comment.user.id,
        name: comment.user.name ?? comment.user.email.split('@')[0],
        avatarUrl: comment.user.avatarUrl,
      },
    };
  }

  async addInitialComment(feedPostId: string, dto: AddInitialCommentDto) {
    const post = await this.prisma.feedPost.findUnique({
      where: { id: feedPostId },
    });
    if (!post) {
      throw new NotFoundException('Feed post not found');
    }
    if (post.userId !== dto.userId) {
      throw new ForbiddenException('Only the trader can add the opening thesis');
    }
    if (post.initialComment) {
      throw new BadRequestException('Opening comment already posted');
    }

    const body = dto.body.trim();
    await this.prisma.feedPost.update({
      where: { id: feedPostId },
      data: {
        initialComment: body,
        commentCount: { increment: 1 },
        lastCommentAt: new Date(),
      },
    });

    await this.refreshHighlights();
    return { success: true, initialComment: body };
  }

  private async loadPosts(args: {
    where?: Prisma.FeedPostWhereInput;
    orderBy: Prisma.FeedPostOrderByWithRelationInput | Prisma.FeedPostOrderByWithRelationInput[];
    take?: number;
  }) {
    const posts = await this.prisma.feedPost.findMany({
      ...args,
      include: {
        user: { select: { id: true, name: true, email: true, avatarUrl: true } },
        project: {
          include: {
            metrics: true,
            chain: { select: { slug: true } },
          },
        },
        paperTrade: true,
      },
    });

    return posts.map((post) => this.mapPost(post));
  }

  private mapPost(
    post: Prisma.FeedPostGetPayload<{
      include: {
        user: { select: { id: true; name: true; email: true; avatarUrl: true } };
        project: { include: { metrics: true; chain: { select: { slug: true } } } };
        paperTrade: true;
      };
    }>,
  ) {
    const displayName =
      post.user.name ??
      post.user.email.split('@')[0].replace(/^paper-/, 'Trader ');
    const marketCap = post.project.metrics?.marketCap
      ? Number(post.project.metrics.marketCap)
      : null;

    return {
      id: post.id,
      paperTradeId: post.paperTradeId,
      side: post.paperTrade.side as PaperTradeSide,
      amountUsd: Number(post.paperTrade.totalUsd),
      priceUsd: Number(post.paperTrade.priceUsd),
      initialComment: post.initialComment,
      commentCount: post.commentCount,
      highlighted: Boolean(
        post.highlightedUntil && post.highlightedUntil.getTime() > Date.now(),
      ),
      highlightedUntil: post.highlightedUntil,
      createdAt: post.createdAt,
      trader: {
        id: post.user.id,
        name: displayName,
        avatarUrl: post.user.avatarUrl,
      },
      project: {
        id: post.project.id,
        slug: post.project.slug,
        name: post.project.name,
        ticker: post.project.ticker,
        logoUrl: post.project.logoUrl,
        dexscreenerUrl: post.project.dexscreenerUrl,
        chainSlug: post.project.chain.slug,
        marketCap,
      },
    };
  }
}
