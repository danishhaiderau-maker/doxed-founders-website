import {
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { POINTS } from '@dcf/utils';
import { AnalyticsEventType, Prisma, ProjectSource } from '@prisma/client';
import { AnalyticsService } from '../analytics/analytics.service';
import { PointsService } from '../points/points.service';
import { PrismaService } from '../prisma/prisma.service';

const projectInclude = {
  chain: { select: { slug: true, name: true } },
  category: { select: { slug: true, name: true } },
  founder: {
    select: {
      slug: true,
      name: true,
      photoUrl: true,
      verifications: { where: { verified: true }, select: { type: true } },
    },
  },
  metrics: true,
} satisfies Prisma.ProjectInclude;

@Injectable()
export class WatchlistService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly analytics: AnalyticsService,
    private readonly points: PointsService,
  ) {}

  async list(userId: string) {
    const rows = await this.prisma.watchlist.findMany({
      where: { userId },
      orderBy: { createdAt: 'desc' },
      include: {
        project: { include: projectInclude },
      },
    });

    return rows
      .filter((row) => row.project.approved && row.project.source === ProjectSource.CURATED)
      .map((row) => this.mapProject(row.project));
  }

  async listSlugs(userId: string) {
    const rows = await this.prisma.watchlist.findMany({
      where: { userId },
      include: { project: { select: { slug: true, approved: true, source: true } } },
      orderBy: { createdAt: 'desc' },
    });

    return {
      slugs: rows
        .filter(
          (row) => row.project.approved && row.project.source === ProjectSource.CURATED,
        )
        .map((row) => row.project.slug),
    };
  }

  async add(userId: string, slug: string) {
    const project = await this.findCuratedProject(slug);

    await this.prisma.watchlist.upsert({
      where: {
        userId_projectId: { userId, projectId: project.id },
      },
      update: {},
      create: { userId, projectId: project.id },
    });

    await this.analytics.track(AnalyticsEventType.WATCHLIST_ADD, {
      userId,
      projectId: project.id,
      metadata: { slug: project.slug },
    });

    await this.points.award(userId, POINTS.WATCHLIST_ADD, 'WATCHLIST_ADD');

    return { saved: true, slug: project.slug };
  }

  async remove(userId: string, slug: string) {
    const project = await this.findCuratedProject(slug);

    await this.prisma.watchlist.deleteMany({
      where: { userId, projectId: project.id },
    });

    await this.analytics.track(AnalyticsEventType.WATCHLIST_REMOVE, {
      userId,
      projectId: project.id,
      metadata: { slug: project.slug },
    });

    return { saved: false, slug: project.slug };
  }

  private async findCuratedProject(slug: string) {
    const project = await this.prisma.project.findFirst({
      where: {
        slug,
        approved: true,
        source: ProjectSource.CURATED,
      },
    });

    if (!project) {
      throw new NotFoundException('Project not found');
    }

    return project;
  }

  private mapProject(
    project: Prisma.ProjectGetPayload<{ include: typeof projectInclude }>,
  ) {
    return {
      slug: project.slug,
      name: project.name,
      ticker: project.ticker,
      summary: project.summary,
      logoUrl: project.logoUrl,
      websiteUrl: project.websiteUrl,
      dexscreenerUrl: project.dexscreenerUrl,
      featured: project.featured,
      source: project.source,
      chain: project.chain,
      category: project.category,
      founder: project.founder
        ? {
            slug: project.founder.slug,
            name: project.founder.name,
            photoUrl: project.founder.photoUrl,
            verifications: project.founder.verifications.map((v) => v.type),
          }
        : null,
      metrics: project.metrics
        ? {
            priceUsd: project.metrics.priceUsd
              ? Number(project.metrics.priceUsd)
              : null,
            marketCap: project.metrics.marketCap
              ? Number(project.metrics.marketCap)
              : null,
            fdv: project.metrics.fdv ? Number(project.metrics.fdv) : null,
            volume24h: project.metrics.volume24h
              ? Number(project.metrics.volume24h)
              : null,
            liquidity: project.metrics.liquidity
              ? Number(project.metrics.liquidity)
              : null,
            holders: project.metrics.holders,
            priceChange24h: project.metrics.priceChange24h
              ? Number(project.metrics.priceChange24h)
              : null,
            updatedAt: project.metrics.updatedAt,
          }
        : null,
    };
  }
}
