import { Injectable, NotFoundException } from '@nestjs/common';
import { Prisma, ProjectSource, ListingStatus } from '@prisma/client';
import { formatUsd, inferProjectLifecycleStage, resolveProjectListingKind, resolveEffectiveLifecycleStage } from '@dcf/utils';
import { HotBuyService } from '../feed/hot-buy.service';
import { PrismaService } from '../prisma/prisma.service';
import { MetricsSyncService } from './metrics-sync.service';

const projectInclude = {
  chain: { select: { slug: true, name: true } },
  category: { select: { slug: true, name: true } },
  founder: {
    select: {
      id: true,
      slug: true,
      name: true,
      photoUrl: true,
      linkedInUrl: true,
      twitterUrl: true,
      githubUrl: true,
      videoUrl: true,
      verifications: { where: { verified: true }, select: { type: true } },
    },
  },
  metrics: true,
  socials: true,
  auditReports: { orderBy: { createdAt: 'desc' as const }, take: 3 },
  featuredEntry: true,
} satisfies Prisma.ProjectInclude;

@Injectable()
export class ProjectsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly metricsSync: MetricsSyncService,
    private readonly hotBuy: HotBuyService,
  ) {}

  async findAll(params?: { featured?: boolean; category?: string }) {
    this.metricsSync.syncStaleInBackground();
    const where: Prisma.ProjectWhereInput = {
      approved: true,
      source: ProjectSource.CURATED,
      founderId: { not: null },
    };

    if (params?.featured) {
      where.featured = true;
    }
    if (params?.category) {
      where.category = { slug: params.category };
    }

    const projects = await this.prisma.project.findMany({
      where,
      include: projectInclude,
      orderBy: [{ featured: 'desc' }, { name: 'asc' }],
    });

    const tickers = [...new Set(projects.map((p) => p.ticker))];
    const listings =
      tickers.length > 0
        ? await this.prisma.listingApplication.findMany({
            where: { ticker: { in: tickers }, status: ListingStatus.APPROVED },
            orderBy: { reviewedAt: 'desc' },
            select: {
              ticker: true,
              founderDoxxedStatus: true,
              scoutHighlightNote: true,
              whyDoxxed: true,
            },
          })
        : [];
    const listingByTicker = new Map<string, (typeof listings)[0]>();
    for (const row of listings) {
      if (!listingByTicker.has(row.ticker)) listingByTicker.set(row.ticker, row);
    }

    return projects.map((p) => {
      const listing = listingByTicker.get(p.ticker);
      const scout = this.scoutMetaFromListing(listing);
      return {
        ...this.mapProjectSummary(p),
        scoutHighlight: scout.scoutHighlight,
        founderDoxxedStatus: scout.founderDoxxedStatus,
      };
    });
  }

  private scoutMetaFromListing(
    listing?: {
      scoutHighlightNote?: string | null;
      founderDoxxedStatus?: string | null;
      whyDoxxed?: string | null;
    } | null,
  ) {
    if (!listing) return { scoutHighlight: null, founderDoxxedStatus: null };
    if (listing.scoutHighlightNote) {
      return {
        scoutHighlight: listing.scoutHighlightNote,
        founderDoxxedStatus:
          (listing.founderDoxxedStatus as 'DOXXED' | 'BUILDING_IN_PUBLIC' | null) ??
          'BUILDING_IN_PUBLIC',
      };
    }
    const why = listing.whyDoxxed ?? '';
    if (/building in public/i.test(why)) {
      const note = why.replace(/^\[Building in public[^\]]*\]\s*/i, '').trim();
      return {
        scoutHighlight: note || why,
        founderDoxxedStatus: 'BUILDING_IN_PUBLIC' as const,
      };
    }
    return { scoutHighlight: null, founderDoxxedStatus: null };
  }

  async getPlatformStats() {
    const curatedWhere = {
      approved: true,
      source: ProjectSource.CURATED,
      founderId: { not: null },
    } as const;

    const [verifiedFounders, activeProjects, communityMembers, portfolioAgg, tradeCount] =
      await Promise.all([
        this.prisma.founder.count({
          where: { projects: { some: curatedWhere } },
        }),
        this.prisma.project.count({ where: curatedWhere }),
        this.prisma.user.count({ where: { banned: false } }),
        this.prisma.paperPortfolio.aggregate({
          _sum: { totalValue: true },
          _count: true,
        }),
        this.prisma.paperTrade.count(),
      ]);

    const simulatedCapital =
      Number(portfolioAgg._sum.totalValue ?? 0) > 0
        ? Number(portfolioAgg._sum.totalValue)
        : portfolioAgg._count * 10_000;

    return {
      verifiedFounders,
      activeProjects,
      communityMembers,
      simulatedCapital: Math.round(simulatedCapital),
      paperTraders: portfolioAgg._count,
      totalTrades: tradeCount,
    };
  }

  async getPlatformActivity(limit = 10) {
    const take = Math.min(Math.max(limit, 1), 20);

    const [buildPosts, videos, allocations] = await Promise.all([
      this.prisma.founderBuildPost.findMany({
        orderBy: { publishedAt: 'desc' },
        take,
        include: {
          founder: { select: { slug: true, name: true } },
          project: { select: { slug: true, name: true, ticker: true } },
        },
      }),
      this.prisma.founderVideo.findMany({
        orderBy: { publishedAt: 'desc' },
        take,
        include: {
          founder: { select: { slug: true, name: true } },
          project: { select: { slug: true, name: true, ticker: true } },
        },
      }),
      this.prisma.raiseAllocation.findMany({
        orderBy: { createdAt: 'desc' },
        take,
        include: {
          raise: {
            include: {
              project: { select: { slug: true, name: true, ticker: true } },
            },
          },
        },
      }),
    ]);

    type ActivityItem = {
      id: string;
      kind: 'build' | 'video' | 'demand';
      founderName: string;
      founderSlug?: string;
      projectSlug?: string;
      projectName?: string;
      headline: string;
      detail?: string;
      amountUsd?: number;
      at: string;
    };

    const items: ActivityItem[] = [
      ...buildPosts.map((p) => ({
        id: `build-${p.id}`,
        kind: 'build' as const,
        founderName: p.founder.name,
        founderSlug: p.founder.slug,
        projectSlug: p.project?.slug,
        projectName: p.project?.name,
        headline: p.headline,
        detail: p.githubUrl ? 'Shipped & published' : 'Build update',
        at: p.publishedAt.toISOString(),
      })),
      ...videos.map((v) => ({
        id: `video-${v.id}`,
        kind: 'video' as const,
        founderName: v.founder.name,
        founderSlug: v.founder.slug,
        projectSlug: v.project?.slug,
        projectName: v.project?.name,
        headline: v.title,
        detail: 'Founder video uploaded',
        at: v.publishedAt.toISOString(),
      })),
      ...allocations.map((a) => ({
        id: `demand-${a.id}`,
        kind: 'demand' as const,
        founderName: a.raise.project.name,
        projectSlug: a.raise.project.slug,
        projectName: a.raise.project.name,
        headline: `${formatUsd(Number(a.amountUsd))} demand allocated`,
        detail: 'Raise Room · paper capital',
        amountUsd: Number(a.amountUsd),
        at: a.createdAt.toISOString(),
      })),
    ];

    return items
      .sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime())
      .slice(0, take);
  }

  async findBySlug(slug: string) {
    await this.metricsSync.syncBySlug(slug, true);

    const project = await this.prisma.project.findFirst({
      where: {
        slug,
        approved: true,
      },
      include: {
        ...projectInclude,
        documents: { orderBy: { createdAt: 'desc' } },
      },
    });

    if (!project) {
      throw new NotFoundException('Project not found');
    }

    const marketCap = project.metrics?.marketCap ? Number(project.metrics.marketCap) : null;
    const listingKind = resolveProjectListingKind({
      source: project.source,
      founderId: project.founderId,
    });
    const effectiveStage = resolveEffectiveLifecycleStage({
      source: project.source,
      founderId: project.founderId,
      lifecycleStage: project.lifecycleStage,
      isLiveToken: project.isLiveToken,
      dexscreenerUrl: project.dexscreenerUrl,
      contractAddress: project.contractAddress,
      marketCap,
    });

    const listing = await this.prisma.listingApplication.findFirst({
      where: {
        status: 'APPROVED',
        ticker: project.ticker,
      },
      orderBy: { reviewedAt: 'desc' },
      select: {
        founderName: true,
        founderTwitter: true,
        founderLinkedIn: true,
        founderGithub: true,
        founderVideoUrl: true,
        founderInterviewUrl: true,
        companyDetails: true,
        whyList: true,
        whyDoxxed: true,
        verificationScore: true,
        verificationCriteria: true,
        websiteUrl: true,
        telegramUrl: true,
        auditUrl: true,
      },
    });

    const recentPaperBuyers = await this.hotBuy.getRecentBuyersForProject(project.id);

    return {
      ...this.mapProjectDetail(project),
      lifecycleStage: effectiveStage,
      listingKind,
      recentPaperBuyers,
      isVerifiedListing: listingKind === 'verified',
      isLiveToken:
        project.isLiveToken ||
        effectiveStage === 'LIVE_TRADING' ||
        effectiveStage === 'TOKEN_LAUNCH',
      verificationDossier: listing
        ? {
            founderName: listing.founderName,
            founderTwitter: listing.founderTwitter,
            founderLinkedIn: listing.founderLinkedIn,
            founderGithub: listing.founderGithub,
            founderVideoUrl: listing.founderVideoUrl,
            founderInterviewUrl: listing.founderInterviewUrl,
            companyDetails: listing.companyDetails,
            whyList: listing.whyList,
            whyDoxxed: listing.whyDoxxed,
            verificationScore: listing.verificationScore,
            verificationCriteria: listing.verificationCriteria as string[] | null,
            websiteUrl: listing.websiteUrl,
            telegramUrl: listing.telegramUrl,
            auditUrl: listing.auditUrl,
          }
        : project.founder
          ? {
              founderName: project.founder.name,
              founderTwitter: project.founder.twitterUrl,
              founderLinkedIn: project.founder.linkedInUrl,
              founderGithub: project.founder.githubUrl,
              founderVideoUrl: project.founder.videoUrl,
              whyList: null,
              whyDoxxed: null,
              verificationScore: null,
              verificationCriteria: project.founder.verifications.map((v) => v.type),
            }
          : null,
    };
  }

  async findFounders() {
    const founders = await this.prisma.founder.findMany({
      include: {
        verifications: { where: { verified: true }, select: { type: true } },
        _count: {
          select: {
            projects: { where: { approved: true, source: ProjectSource.CURATED } },
          },
        },
      },
      orderBy: { name: 'asc' },
    });

    return founders.map((f) => ({
      slug: f.slug,
      name: f.name,
      bio: f.bio,
      photoUrl: f.photoUrl,
      linkedInUrl: f.linkedInUrl,
      twitterUrl: f.twitterUrl,
      githubUrl: f.githubUrl,
      videoUrl: f.videoUrl,
      verifications: f.verifications.map((v) => v.type),
      projectCount: f._count.projects,
    }));
  }

  async findFounderBySlug(slug: string) {
    const founder = await this.prisma.founder.findUnique({
      where: { slug },
      include: {
        verifications: { where: { verified: true }, select: { type: true } },
        projects: {
          where: { approved: true, source: ProjectSource.CURATED },
          include: projectInclude,
          orderBy: [{ featured: 'desc' }, { name: 'asc' }],
        },
      },
    });

    if (!founder) {
      throw new NotFoundException('Founder not found');
    }

    return {
      slug: founder.slug,
      name: founder.name,
      bio: founder.bio,
      photoUrl: founder.photoUrl,
      linkedInUrl: founder.linkedInUrl,
      twitterUrl: founder.twitterUrl,
      githubUrl: founder.githubUrl,
      videoUrl: founder.videoUrl,
      verifications: founder.verifications.map((v) => v.type),
      projects: founder.projects.map((p) => this.mapProjectSummary(p)),
    };
  }

  private mapProjectSummary(
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
      lifecycleStage: project.lifecycleStage,
      launchReadiness: project.launchReadiness,
      bubbleScore: project.bubbleScore,
      isLiveToken: project.isLiveToken,
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
      metrics: this.mapMetrics(project.metrics),
    };
  }

  private mapProjectDetail(
    project: Prisma.ProjectGetPayload<{
      include: typeof projectInclude & { documents: true };
    }>,
  ) {
    return {
      ...this.mapProjectSummary(project),
      description: project.description,
      docsUrl: project.docsUrl,
      whitepaperUrl: project.whitepaperUrl,
      contractAddress: project.contractAddress,
      socials: project.socials,
      documents: project.documents,
      auditReports: project.auditReports.map((a) => ({
        auditor: a.auditor,
        reportUrl: a.reportUrl,
        auditedAt: a.auditedAt,
      })),
      founder: project.founder
        ? {
            slug: project.founder.slug,
            name: project.founder.name,
            photoUrl: project.founder.photoUrl,
            linkedInUrl: project.founder.linkedInUrl,
            twitterUrl: project.founder.twitterUrl,
            githubUrl: project.founder.githubUrl,
            verifications: project.founder.verifications.map((v) => v.type),
          }
        : null,
    };
  }

  private mapMetrics(metrics: Prisma.ProjectMetricsGetPayload<object> | null) {
    if (!metrics) return null;
    return {
      priceUsd: metrics.priceUsd ? Number(metrics.priceUsd) : null,
      marketCap: metrics.marketCap ? Number(metrics.marketCap) : null,
      fdv: metrics.fdv ? Number(metrics.fdv) : null,
      volume24h: metrics.volume24h ? Number(metrics.volume24h) : null,
      liquidity: metrics.liquidity ? Number(metrics.liquidity) : null,
      holders: metrics.holders,
      priceChange24h: metrics.priceChange24h ? Number(metrics.priceChange24h) : null,
      updatedAt: metrics.updatedAt,
    };
  }
}
