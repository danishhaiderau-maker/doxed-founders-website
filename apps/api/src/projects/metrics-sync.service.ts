import { Injectable, Logger, OnModuleInit } from '@nestjs/common';
import { ChainSlug, Prisma, ProjectLifecycleStage } from '@prisma/client';
import { DexscreenerService } from '../dexscreener/dexscreener.service';
import { PrismaService } from '../prisma/prisma.service';

const STALE_MS = 2 * 60 * 1000;
const SYNC_INTERVAL_MS = 2 * 60 * 1000;
const REQUEST_GAP_MS = 350;
const PAGE_SYNC_COOLDOWN_MS = 60 * 1000;

type ProjectSyncRow = {
  id: string;
  dexscreenerUrl: string | null;
  contractAddress: string | null;
  chain: { slug: ChainSlug } | null;
};

@Injectable()
export class MetricsSyncService implements OnModuleInit {
  private readonly logger = new Logger(MetricsSyncService.name);
  private syncing = false;
  private readonly lastPageSyncBySlug = new Map<string, number>();

  constructor(
    private readonly prisma: PrismaService,
    private readonly dexscreener: DexscreenerService,
  ) {}

  onModuleInit() {
    this.syncStaleInBackground();
    setInterval(() => this.syncStaleInBackground(), SYNC_INTERVAL_MS);
  }

  syncStaleInBackground() {
    if (this.syncing) return;
    this.syncing = true;
    this.syncStaleProjects()
      .then((result) => {
        if (result.attempted > 0) {
          this.logger.log(
            `Metrics sync: ${result.updated}/${result.attempted} updated (${result.failed} failed)`,
          );
        }
      })
      .catch((err: Error) => {
        this.logger.warn(`Metrics sync failed: ${err.message}`);
      })
      .finally(() => {
        this.syncing = false;
      });
  }

  async syncStaleProjects() {
    const cutoff = new Date(Date.now() - STALE_MS);
    const projects = await this.prisma.project.findMany({
      where: {
        approved: true,
        trackingActive: true,
        OR: [
          { dexscreenerUrl: { not: null } },
          { contractAddress: { not: null } },
        ],
        AND: [
          {
            OR: [
              { metrics: { is: null } },
              { metrics: { updatedAt: { lt: cutoff } } },
            ],
          },
        ],
      },
      select: {
        id: true,
        dexscreenerUrl: true,
        contractAddress: true,
        chain: { select: { slug: true } },
      },
    });

    let updated = 0;
    let failed = 0;

    for (const project of projects) {
      try {
        const ok = await this.syncProjectRecord(project);
        if (ok) updated += 1;
      } catch {
        failed += 1;
      }
      await this.sleep(REQUEST_GAP_MS);
    }

    return { attempted: projects.length, updated, failed };
  }

  /** Refresh metrics when a project page is opened (rate-limited per slug). */
  async syncBySlug(slug: string, force = false) {
    const project = await this.prisma.project.findFirst({
      where: {
        slug,
        approved: true,
        OR: [
          { dexscreenerUrl: { not: null } },
          { contractAddress: { not: null } },
        ],
      },
      include: { metrics: true, chain: { select: { slug: true } } },
    });

    if (!project) return false;

    if (force) {
      const last = this.lastPageSyncBySlug.get(slug) ?? 0;
      if (Date.now() - last < PAGE_SYNC_COOLDOWN_MS) {
        return false;
      }
      this.lastPageSyncBySlug.set(slug, Date.now());
    } else if (!this.isStale(project.metrics?.updatedAt)) {
      return false;
    }

    return this.syncProjectRecord({
      id: project.id,
      dexscreenerUrl: project.dexscreenerUrl,
      contractAddress: project.contractAddress,
      chain: project.chain,
    });
  }

  /** @deprecated use syncBySlug */
  async syncBySlugIfStale(slug: string) {
    return this.syncBySlug(slug, false);
  }

  private isStale(updatedAt?: Date | null) {
    if (!updatedAt) return true;
    return updatedAt.getTime() < Date.now() - STALE_MS;
  }

  private async syncProjectRecord(project: ProjectSyncRow): Promise<boolean> {
    const preview = await this.resolvePreview(project);
    if (!preview) return false;

    const mp = preview.marketPreview;
    if (
      mp.priceUsd == null &&
      mp.marketCap == null &&
      mp.volume24h == null
    ) {
      return false;
    }

    const priceUsd =
      mp.priceUsd != null ? new Prisma.Decimal(mp.priceUsd) : undefined;

    await this.prisma.projectMetrics.upsert({
      where: { projectId: project.id },
      update: {
        priceUsd,
        marketCap:
          mp.marketCap != null ? new Prisma.Decimal(mp.marketCap) : undefined,
        fdv: mp.fdv != null ? new Prisma.Decimal(mp.fdv) : undefined,
        volume24h:
          mp.volume24h != null ? new Prisma.Decimal(mp.volume24h) : undefined,
        liquidity:
          mp.liquidityUsd != null
            ? new Prisma.Decimal(mp.liquidityUsd)
            : undefined,
        priceChange24h:
          mp.priceChange24h != null
            ? new Prisma.Decimal(mp.priceChange24h)
            : undefined,
      },
      create: {
        projectId: project.id,
        priceUsd: priceUsd ?? new Prisma.Decimal(0),
        marketCap:
          mp.marketCap != null ? new Prisma.Decimal(mp.marketCap) : undefined,
        fdv: mp.fdv != null ? new Prisma.Decimal(mp.fdv) : undefined,
        volume24h:
          mp.volume24h != null ? new Prisma.Decimal(mp.volume24h) : undefined,
        liquidity:
          mp.liquidityUsd != null
            ? new Prisma.Decimal(mp.liquidityUsd)
            : undefined,
        priceChange24h:
          mp.priceChange24h != null
            ? new Prisma.Decimal(mp.priceChange24h)
            : undefined,
      },
    });

    const liquidity = mp.liquidityUsd ?? 0;
    const hasLiveMarket =
      mp.priceUsd != null && Number(mp.priceUsd) > 0 && liquidity > 0;

    await this.prisma.project.update({
      where: { id: project.id },
      data: {
        dexscreenerUrl: preview.dexscreenerUrl,
        contractAddress: preview.contractAddress ?? project.contractAddress ?? undefined,
        ...(hasLiveMarket
          ? {
              isLiveToken: true,
              lifecycleStage: ProjectLifecycleStage.LIVE_TRADING,
            }
          : {}),
      },
    });

    return true;
  }

  private async resolvePreview(project: ProjectSyncRow) {
    if (project.contractAddress && project.chain?.slug) {
      try {
        return await this.dexscreener.previewFromContract(
          project.chain.slug,
          project.contractAddress,
        );
      } catch (err) {
        this.logger.warn(
          `Contract metrics lookup failed for ${project.id}: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    if (project.dexscreenerUrl) {
      return this.dexscreener.previewFromUrl(project.dexscreenerUrl);
    }

    return null;
  }

  private sleep(ms: number) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}
