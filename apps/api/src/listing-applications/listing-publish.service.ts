import {
  BadRequestException,
  Injectable,
} from '@nestjs/common';
import {
  FounderVerificationCriterion,
  normalizeProjectName,
  resolveListingChain,
  scoreFounderVerification,
  slugify,
  validateListingForApproval,
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
}

@Injectable()
export class ListingPublishService {
  constructor(private readonly prisma: PrismaService) {}

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

    const founder = application.founderName
      ? await this.upsertFounder(tx, application, criteria)
      : null;

    const projectSlug = await this.uniqueProjectSlug(
      tx,
      slugify(normalizeProjectName(application.projectName) || application.projectName) ||
        slugify(application.ticker),
    );

    const existing = await tx.project.findFirst({
      where: {
        OR: [
          { slug: projectSlug },
          { ticker: application.ticker, chainId: chain.id },
        ],
      },
    });
    if (existing?.approved && existing.source === ProjectSource.CURATED) {
      throw new BadRequestException(
        `A curated project already exists for ${application.ticker} on this chain`,
      );
    }

    const category = await tx.category.findFirst({
      orderBy: { name: 'asc' },
    });

    const project = existing
      ? await tx.project.update({
          where: { id: existing.id },
          data: this.projectData(application, chain.id, category?.id, founder?.id),
        })
      : await tx.project.create({
          data: {
            slug: projectSlug,
            ...this.projectData(application, chain.id, category?.id, founder?.id),
          },
        });

    await this.upsertMetrics(tx, project.id, application.marketPreview);
    await this.upsertSocials(tx, project.id, application);
    await this.upsertAudit(tx, project.id, application.auditUrl);

    return {
      projectId: project.id,
      projectSlug: project.slug,
      projectName: project.name,
      founderSlug: founder?.slug ?? null,
    };
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
    const hasSocial =
      application.telegramUrl ||
      application.founderTwitter ||
      application.founderGithub;

    if (!hasSocial) return;

    await tx.projectSocials.upsert({
      where: { projectId },
      update: {
        telegramUrl: application.telegramUrl,
        twitterUrl: application.founderTwitter,
        githubUrl: application.founderGithub,
      },
      create: {
        projectId,
        telegramUrl: application.telegramUrl,
        twitterUrl: application.founderTwitter,
        githubUrl: application.founderGithub,
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
