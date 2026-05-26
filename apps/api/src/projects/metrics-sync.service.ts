import { Injectable, Logger, OnModuleInit } from '@nestjs/common';
import { Prisma } from '@prisma/client';
import { DexscreenerService } from '../dexscreener/dexscreener.service';
import { PrismaService } from '../prisma/prisma.service';

const STALE_MS = 15 * 60 * 1000;
const SYNC_INTERVAL_MS = 15 * 60 * 1000;
const REQUEST_GAP_MS = 350;

@Injectable()
export class MetricsSyncService implements OnModuleInit {
  private readonly logger = new Logger(MetricsSyncService.name);
  private syncing = false;

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
        dexscreenerUrl: { not: null },
        OR: [
          { metrics: { is: null } },
          { metrics: { updatedAt: { lt: cutoff } } },
        ],
      },
      select: { id: true, dexscreenerUrl: true },
    });

    let updated = 0;
    let failed = 0;

    for (const project of projects) {
      try {
        const ok = await this.syncProject(project.id, project.dexscreenerUrl!);
        if (ok) updated += 1;
      } catch {
        failed += 1;
      }
      await this.sleep(REQUEST_GAP_MS);
    }

    return { attempted: projects.length, updated, failed };
  }

  async syncBySlugIfStale(slug: string) {
    const project = await this.prisma.project.findFirst({
      where: {
        slug,
        approved: true,
        dexscreenerUrl: { not: null },
      },
      include: { metrics: true },
    });

    if (!project?.dexscreenerUrl) return false;
    if (!this.isStale(project.metrics?.updatedAt)) return false;

    return this.syncProject(project.id, project.dexscreenerUrl);
  }

  private isStale(updatedAt?: Date | null) {
    if (!updatedAt) return true;
    return updatedAt.getTime() < Date.now() - STALE_MS;
  }

  private async syncProject(
    projectId: string,
    dexscreenerUrl: string,
  ): Promise<boolean> {
    const preview = await this.dexscreener.previewFromUrl(dexscreenerUrl);
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
      where: { projectId },
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
        projectId,
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

    return true;
  }

  private sleep(ms: number) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}
