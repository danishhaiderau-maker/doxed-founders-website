import {
  BadRequestException,
  Injectable,
} from '@nestjs/common';
import {
  buildListingRelistDiff,
  countChangedFields,
  FounderVerificationCriterion,
  normalizeContractAddress,
  normalizeProjectName,
  resolveListingChain,
  scoreFounderVerification,
  slugify,
  snapshotFromApplication,
  validateListingForApproval,
  resolveListingGithubRepo,
  type ListingRelistField,
  type ListingRelistMatchType,
} from '@dcf/utils';
import {
  ChainSlug,
  ListingApplication,
  Prisma,
  ProjectLifecycleStage,
  ProjectSource,
  VerificationType,
} from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';

type Tx = Prisma.TransactionClient;

interface MarketPreview {
  priceUsd?: string | number;
  marketCap?: number;
  fdv?: number;
  volume24h?: number;
  liquidityUsd?: number;
  priceChange24h?: number;
}

export interface PublishedProjectResult {
  projectId: string;
  projectSlug: string;
  projectName: string;
  founderSlug: string | null;
  relisted?: boolean;
  changedFieldCount?: number;
  deactivatedDuplicateIds?: string[];
}

export type ListingRelistPreview = {
  hasExisting: boolean;
  matchType: ListingRelistMatchType | null;
  existingProjectId: string | null;
  existingProjectSlug: string | null;
  existingProjectName: string | null;
  sameContract: boolean;
  changedFieldCount: number;
  fields: ListingRelistField[];
};

@Injectable()
export class ListingPublishService {
  constructor(private readonly prisma: PrismaService) {}

  async getRelistPreview(application: ListingApplication): Promise<ListingRelistPreview> {
    const chainSlug = resolveListingChain(application);
    if (!chainSlug) {
      return {
        hasExisting: false,
        matchType: null,
        existingProjectId: null,
        existingProjectSlug: null,
        existingProjectName: null,
        sameContract: false,
        changedFieldCount: 0,
        fields: [],
      };
    }

    const chain = await this.prisma.chain.findUnique({
      where: { slug: chainSlug as ChainSlug },
    });
    if (!chain) {
      return {
        hasExisting: false,
        matchType: null,
        existingProjectId: null,
        existingProjectSlug: null,
        existingProjectName: null,
        sameContract: false,
        changedFieldCount: 0,
        fields: [],
      };
    }

    const slugBase =
      slugify(normalizeProjectName(application.projectName) || application.projectName) ||
      slugify(application.ticker);
    const match = await this.findExistingCuratedProject(
      this.prisma,
      application,
      chain.id,
      slugBase,
    );

    if (!match) {
      return {
        hasExisting: false,
        matchType: null,
        existingProjectId: null,
        existingProjectSlug: null,
        existingProjectName: null,
        sameContract: false,
        changedFieldCount: 0,
        fields: [],
      };
    }

    const previous = await this.projectToSnapshot(match.project.id);
    const next = snapshotFromApplication({
      ...application,
      marketPreview:
        application.marketPreview && typeof application.marketPreview === 'object'
          ? (application.marketPreview as Record<string, unknown>)
          : null,
    });
    const fields = buildListingRelistDiff(previous, next);
    const contractNorm = normalizeContractAddress(application.contractAddress);
    const existingNorm = normalizeContractAddress(match.project.contractAddress);

    return {
      hasExisting: true,
      matchType: match.matchType,
      existingProjectId: match.project.id,
      existingProjectSlug: match.project.slug,
      existingProjectName: match.project.name,
      sameContract: Boolean(contractNorm && existingNorm && contractNorm === existingNorm),
      changedFieldCount: countChangedFields(fields),
      fields,
    };
  }

  async publishApprovedApplication(
    application: ListingApplication,
  ): Promise<PublishedProjectResult> {
    const approval = validateListingForApproval({
      dexscreenerUrl: application.dexscreenerUrl,
      founderDoxxedStatus: application.founderDoxxedStatus,
      founderVideoUrl: application.founderVideoUrl,
      founderInterviewUrl: application.founderInterviewUrl,
      founderTwitter: application.founderTwitter,
      founderLinkedIn: application.founderLinkedIn,
      founderGithub: application.founderGithub,
      websiteUrl: application.websiteUrl,
      chainSlug: application.chainSlug,
      contractAddress: application.contractAddress,
      founderName: application.founderName,
      projectName: application.projectName,
    });

    if (!approval.ok) {
      throw new BadRequestException(approval.errors.join(' '));
    }

    const chainSlug = resolveListingChain(application);
    if (!chainSlug) {
      throw new BadRequestException(
        'Cannot publish: chain could not be inferred from DexScreener — set chain during admin review (optional enrichment).',
      );
    }

    const effectiveApplication: ListingApplication = {
      ...application,
      chainSlug: chainSlug as ListingApplication['chainSlug'],
      founderName:
        application.founderName?.trim() ||
        application.projectName.trim() ||
        application.ticker,
    };

    const verification = scoreFounderVerification({
      founderName: effectiveApplication.founderName,
      founderLinkedIn: effectiveApplication.founderLinkedIn,
      founderGithub: effectiveApplication.founderGithub,
      companyDetails: effectiveApplication.companyDetails,
      founderVideoUrl: effectiveApplication.founderVideoUrl,
      founderInterviewUrl: effectiveApplication.founderInterviewUrl,
    });

    const result = await this.prisma.$transaction(async (tx) =>
      this.publishInTransaction(tx, effectiveApplication, verification.criteria),
    );

    return result;
  }

  private async publishInTransaction(
    tx: Tx,
    application: ListingApplication,
    criteria: FounderVerificationCriterion[],
  ): Promise<PublishedProjectResult> {
    const chain = await tx.chain.findUnique({
      where: { slug: application.chainSlug as ChainSlug },
    });
    if (!chain) {
      throw new BadRequestException(`Unknown chain: ${application.chainSlug}`);
    }

    let founder = application.founderName
      ? await this.upsertFounder(tx, application, criteria)
      : null;

    if (!founder && application.userId) {
      founder = await this.ensureFounderFromSubmitter(tx, application);
    }

    const slugBase =
      slugify(normalizeProjectName(application.projectName) || application.projectName) ||
      slugify(application.ticker);

    const match = await this.findExistingCuratedProject(tx, application, chain.id, slugBase);
    const existing = match?.project ?? null;
    const relisted = Boolean(existing?.approved && existing.source === ProjectSource.CURATED);

    let deactivatedDuplicateIds: string[] = [];
    if (existing && relisted) {
      const dupes = await tx.project.findMany({
        where: {
          chainId: chain.id,
          ticker: application.ticker.toUpperCase(),
          id: { not: existing.id },
          source: ProjectSource.CURATED,
          approved: true,
        },
        select: { id: true },
      });
      if (dupes.length > 0) {
        deactivatedDuplicateIds = dupes.map((d) => d.id);
        await tx.project.updateMany({
          where: { id: { in: deactivatedDuplicateIds } },
          data: { approved: false, trackingActive: false },
        });
      }
    }

    const projectSlug = existing?.slug ?? (await this.uniqueProjectSlug(tx, slugBase));

    const category = await tx.category.findFirst({
      orderBy: { name: 'asc' },
    });

    const githubRepo = resolveListingGithubRepo(
      application.projectGithubUrl,
      application.founderGithub,
    );

    const project = existing
      ? await tx.project.update({
          where: { id: existing.id },
          data: {
            ...this.projectData(application, chain.id, category?.id, founder?.id),
            githubRepoFullName: githubRepo?.repoFullName ?? undefined,
          },
        })
      : await tx.project.create({
          data: {
            slug: projectSlug,
            ...this.projectData(application, chain.id, category?.id, founder?.id),
            githubRepoFullName: githubRepo?.repoFullName ?? null,
          },
        });

    if (founder && githubRepo?.repoFullName) {
      await tx.founder.update({
        where: { id: founder.id },
        data: { githubRepoFullName: githubRepo.repoFullName },
      });
    }

    await this.upsertMetrics(tx, project.id, application.marketPreview);
    await this.upsertSocials(tx, project.id, application);
    await this.upsertAudit(tx, project.id, application.auditUrl);

    let changedFieldCount = 0;
    if (relisted) {
      const previous = await this.projectToSnapshot(existing!.id, tx);
      const next = snapshotFromApplication({
        ...application,
        marketPreview:
          application.marketPreview && typeof application.marketPreview === 'object'
            ? (application.marketPreview as Record<string, unknown>)
            : null,
      });
      changedFieldCount = countChangedFields(buildListingRelistDiff(previous, next));
    }

    return {
      projectId: project.id,
      projectSlug: project.slug,
      projectName: project.name,
      founderSlug: founder?.slug ?? null,
      relisted,
      changedFieldCount: relisted ? changedFieldCount : undefined,
      deactivatedDuplicateIds: deactivatedDuplicateIds.length
        ? deactivatedDuplicateIds
        : undefined,
    };
  }

  private async findExistingCuratedProject(
    tx: Tx | PrismaService,
    application: ListingApplication,
    chainId: string,
    slugBase: string,
  ): Promise<{ project: { id: string; slug: string; name: string; contractAddress: string | null; approved: boolean; source: ProjectSource }; matchType: ListingRelistMatchType } | null> {
    const contractNorm = normalizeContractAddress(application.contractAddress);
    const ticker = application.ticker.toUpperCase();

    const onChain = await tx.project.findMany({
      where: { chainId, source: ProjectSource.CURATED, approved: true },
      orderBy: { updatedAt: 'desc' },
    });

    if (contractNorm) {
      const byContract = onChain.find(
        (p) => normalizeContractAddress(p.contractAddress) === contractNorm,
      );
      if (byContract) {
        return { project: byContract, matchType: 'contract' };
      }
    }

    const byTicker = onChain.find((p) => p.ticker.toUpperCase() === ticker);
    if (byTicker) {
      return { project: byTicker, matchType: 'ticker' };
    }

    const bySlug = await tx.project.findFirst({
      where: { slug: slugBase, chainId },
    });
    if (bySlug?.source === ProjectSource.CURATED) {
      return { project: bySlug, matchType: 'slug' };
    }

    return null;
  }

  private async projectToSnapshot(projectId: string, tx: Tx | PrismaService = this.prisma) {
    const project = await tx.project.findUnique({
      where: { id: projectId },
      include: {
        socials: true,
        metrics: true,
        founder: true,
      },
    });
    if (!project) {
      return snapshotFromApplication({});
    }

    return snapshotFromApplication({
      projectName: project.name,
      ticker: project.ticker,
      websiteUrl: project.websiteUrl,
      docsUrl: project.docsUrl,
      whitepaperUrl: project.whitepaperUrl,
      contractAddress: project.contractAddress,
      dexscreenerUrl: project.dexscreenerUrl,
      logoUrl: project.logoUrl,
      telegramUrl: project.socials?.telegramUrl,
      founderName: project.founder?.name,
      founderLinkedIn: project.founder?.linkedInUrl,
      founderTwitter: project.founder?.twitterUrl,
      founderGithub: project.founder?.githubUrl ?? null,
      projectGithubUrl:
        project.socials?.githubUrl ??
        (project.githubRepoFullName
          ? `https://github.com/${project.githubRepoFullName}`
          : null),
      founderVideoUrl: project.founder?.videoUrl,
      founderInterviewUrl: null,
      companyDetails: project.description,
      auditUrl: null,
      summary: project.summary,
      marketPreview: project.metrics
        ? {
            marketCap: project.metrics.marketCap
              ? Number(project.metrics.marketCap)
              : undefined,
            priceUsd: project.metrics.priceUsd
              ? String(project.metrics.priceUsd)
              : undefined,
          }
        : null,
    });
  }

  private projectData(
    application: ListingApplication,
    chainId: string,
    categoryId: string | undefined,
    founderId: string | undefined,
  ): Omit<Prisma.ProjectUncheckedCreateInput, 'slug'> {
    const { lifecycleStage, isLiveToken } = this.resolveListingLifecycle(application);

    const displayName = normalizeProjectName(application.projectName);
    return {
      name: displayName || application.projectName.trim(),
      ticker: application.ticker.toUpperCase(),
      summary: application.summary,
      description: application.companyDetails ?? application.summary,
      logoUrl: application.logoUrl,
      websiteUrl: application.websiteUrl,
      docsUrl: application.docsUrl,
      whitepaperUrl: application.whitepaperUrl,
      contractAddress: application.contractAddress,
      dexscreenerUrl: application.dexscreenerUrl,
      chainId,
      categoryId: categoryId ?? null,
      founderId: founderId ?? null,
      source: ProjectSource.CURATED,
      approved: true,
      featured: false,
      trackingActive: true,
      lifecycleStage,
      isLiveToken,
    };
  }

  /** Scout-approved listings are never "idea stage" — only Founder OS (DYNAMIC) projects use IDEA. */
  private resolveListingLifecycle(application: ListingApplication): {
    lifecycleStage: ProjectLifecycleStage;
    isLiveToken: boolean;
  } {
    const preview =
      application.marketPreview && typeof application.marketPreview === 'object'
        ? (application.marketPreview as MarketPreview)
        : null;
    const hasContract = Boolean(application.contractAddress?.trim());
    const hasDex = Boolean(application.dexscreenerUrl?.trim());
    const hasMarket =
      preview?.marketCap != null && preview.marketCap > 0 ||
      preview?.priceUsd != null && Number(preview.priceUsd) > 0;

    if (hasContract && (hasDex || hasMarket)) {
      return { lifecycleStage: ProjectLifecycleStage.LIVE_TRADING, isLiveToken: true };
    }
    if (hasContract) {
      return { lifecycleStage: ProjectLifecycleStage.TOKEN_LAUNCH, isLiveToken: true };
    }
    return { lifecycleStage: ProjectLifecycleStage.LAUNCH_READY, isLiveToken: false };
  }

  private async upsertFounder(
    tx: Tx,
    application: ListingApplication,
    criteria: FounderVerificationCriterion[],
  ) {
    const existing = application.founderLinkedIn
      ? await tx.founder.findFirst({
          where: { linkedInUrl: application.founderLinkedIn },
        })
      : await tx.founder.findFirst({
          where: { name: application.founderName! },
        });

    const baseSlug =
      existing?.slug ??
      (slugify(application.founderName!) ||
        slugify(application.founderLinkedIn?.split('/').pop() ?? 'founder'));
    const slug = existing?.slug ?? (await this.uniqueFounderSlug(tx, baseSlug));

    const founder = existing
      ? await tx.founder.update({
          where: { id: existing.id },
          data: {
            name: application.founderName!,
            bio: application.companyDetails,
            linkedInUrl: application.founderLinkedIn,
            twitterUrl: application.founderTwitter,
            githubUrl: application.founderGithub,
            videoUrl: application.founderVideoUrl,
          },
        })
      : await tx.founder.create({
          data: {
            slug,
            name: application.founderName!,
            bio: application.companyDetails,
            linkedInUrl: application.founderLinkedIn,
            twitterUrl: application.founderTwitter,
            githubUrl: application.founderGithub,
            videoUrl: application.founderVideoUrl,
          },
        });

    const verificationTypes = this.mapVerificationTypes(criteria);
    for (const type of verificationTypes) {
      await tx.founderVerification.upsert({
        where: { founderId_type: { founderId: founder.id, type } },
        update: { verified: true, verifiedAt: new Date() },
        create: {
          founderId: founder.id,
          type,
          verified: true,
          verifiedAt: new Date(),
        },
      });
    }

    return founder;
  }

  private mapVerificationTypes(
    criteria: FounderVerificationCriterion[],
  ): VerificationType[] {
    const types = new Set<VerificationType>();
    for (const criterion of criteria) {
      switch (criterion) {
        case 'LINKEDIN':
          types.add(VerificationType.LINKEDIN);
          break;
        case 'GITHUB':
          types.add(VerificationType.GITHUB);
          break;
        case 'FOUNDER_NAME':
          types.add(VerificationType.IDENTITY);
          break;
        case 'FOUNDER_VIDEO':
        case 'PUBLIC_INTERVIEW':
        case 'COMPANY_DETAILS':
          types.add(VerificationType.TEAM_DOXXED);
          break;
        default:
          break;
      }
    }
    return Array.from(types);
  }

  private async upsertMetrics(
    tx: Tx,
    projectId: string,
    marketPreview: Prisma.JsonValue | null,
  ) {
    if (!marketPreview || typeof marketPreview !== 'object') return;

    const preview = marketPreview as MarketPreview;
    const priceUsd =
      preview.priceUsd != null ? Number(preview.priceUsd) : undefined;

    if (
      priceUsd == null &&
      preview.marketCap == null &&
      preview.volume24h == null
    ) {
      return;
    }

    await tx.projectMetrics.upsert({
      where: { projectId },
      update: {
        priceUsd: priceUsd != null ? new Prisma.Decimal(priceUsd) : undefined,
        marketCap:
          preview.marketCap != null
            ? new Prisma.Decimal(preview.marketCap)
            : undefined,
        fdv:
          preview.fdv != null ? new Prisma.Decimal(preview.fdv) : undefined,
        volume24h:
          preview.volume24h != null
            ? new Prisma.Decimal(preview.volume24h)
            : undefined,
        liquidity:
          preview.liquidityUsd != null
            ? new Prisma.Decimal(preview.liquidityUsd)
            : undefined,
        priceChange24h:
          preview.priceChange24h != null
            ? new Prisma.Decimal(preview.priceChange24h)
            : undefined,
      },
      create: {
        projectId,
        priceUsd:
          priceUsd != null ? new Prisma.Decimal(priceUsd) : new Prisma.Decimal(0),
        marketCap:
          preview.marketCap != null
            ? new Prisma.Decimal(preview.marketCap)
            : undefined,
        fdv:
          preview.fdv != null ? new Prisma.Decimal(preview.fdv) : undefined,
        volume24h:
          preview.volume24h != null
            ? new Prisma.Decimal(preview.volume24h)
            : undefined,
        liquidity:
          preview.liquidityUsd != null
            ? new Prisma.Decimal(preview.liquidityUsd)
            : undefined,
        priceChange24h:
          preview.priceChange24h != null
            ? new Prisma.Decimal(preview.priceChange24h)
            : undefined,
      },
    });
  }

  private async upsertSocials(tx: Tx, projectId: string, application: ListingApplication) {
    const githubRef = resolveListingGithubRepo(
      application.projectGithubUrl,
      application.founderGithub,
    );
    const githubUrl = githubRef?.githubUrl ?? application.founderGithub ?? null;
    const hasSocial =
      application.telegramUrl || application.founderTwitter || githubUrl;

    if (!hasSocial) return;

    await tx.projectSocials.upsert({
      where: { projectId },
      update: {
        telegramUrl: application.telegramUrl,
        twitterUrl: application.founderTwitter,
        githubUrl,
      },
      create: {
        projectId,
        telegramUrl: application.telegramUrl,
        twitterUrl: application.founderTwitter,
        githubUrl,
      },
    });
  }

  /** Link listing submitter to a founder row so GitHub commit events can feed Discover. */
  private async ensureFounderFromSubmitter(tx: Tx, application: ListingApplication) {
    if (!application.userId) return null;

    const existing = await tx.founder.findUnique({
      where: { userId: application.userId },
    });
    if (existing) return existing;

    const name = application.founderName?.trim() || application.projectName.trim();
    const baseSlug = slugify(name) || slugify(application.ticker) || 'founder';
    const slug = await this.uniqueFounderSlug(tx, baseSlug);
    const githubRef = resolveListingGithubRepo(
      application.projectGithubUrl,
      application.founderGithub,
    );

    return tx.founder.create({
      data: {
        slug,
        userId: application.userId,
        name,
        githubUrl: githubRef?.githubUrl ?? application.founderGithub,
        githubRepoFullName: githubRef?.repoFullName ?? null,
        twitterUrl: application.founderTwitter,
        linkedInUrl: application.founderLinkedIn,
        videoUrl: application.founderVideoUrl,
        bio: application.companyDetails,
      },
    });
  }

  private async upsertAudit(tx: Tx, projectId: string, auditUrl: string | null) {
    if (!auditUrl) return;

    const existing = await tx.auditReport.findFirst({
      where: { projectId, reportUrl: auditUrl },
    });
    if (existing) return;

    await tx.auditReport.create({
      data: {
        projectId,
        auditor: 'Submitted audit',
        reportUrl: auditUrl,
        auditedAt: new Date(),
      },
    });
  }

  private async uniqueProjectSlug(tx: Tx, base: string): Promise<string> {
    let slug = base || 'project';
    let suffix = 1;
    while (await tx.project.findUnique({ where: { slug } })) {
      suffix += 1;
      slug = `${base}-${suffix}`;
    }
    return slug;
  }

  private async uniqueFounderSlug(tx: Tx, base: string): Promise<string> {
    let slug = base || 'founder';
    let suffix = 1;
    while (await tx.founder.findUnique({ where: { slug } })) {
      suffix += 1;
      slug = `${base}-${suffix}`;
    }
    return slug;
  }
}
