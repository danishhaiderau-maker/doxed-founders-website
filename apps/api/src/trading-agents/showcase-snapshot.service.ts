import { Injectable, Logger, UnauthorizedException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';

export type ShowcaseSnapshotBody = {
  snapshot_seq?: number;
  snapshot?: Record<string, unknown>;
  bot_version?: string;
  server_ts?: string;
};

@Injectable()
export class ShowcaseSnapshotService {
  private readonly logger = new Logger(ShowcaseSnapshotService.name);

  constructor(
    private readonly config: ConfigService,
    private readonly prisma: PrismaService,
  ) {}

  assertAuthorized(secretHeader: string | undefined) {
    const expected = this.config.get<string>('BOT_CONTROL_SECRET')?.trim();
    if (!expected) {
      throw new UnauthorizedException('Showcase snapshot push not configured');
    }
    if (secretHeader?.trim() !== expected) {
      throw new UnauthorizedException('Invalid bot control secret');
    }
  }

  async ingest(body: ShowcaseSnapshotBody) {
    const seq = BigInt(body.snapshot_seq ?? 0);
    const snapshot = (body.snapshot ?? body) as Prisma.InputJsonValue;
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    const prev = row?.showcaseRelaySnapshotSeq ?? BigInt(0);
    if (seq > 0n && seq <= prev) {
      return { ok: true, skipped: true, snapshot_seq: Number(prev) };
    }
    await this.prisma.platformSettings.upsert({
      where: { id: 'default' },
      create: {
        id: 'default',
        showcaseRelaySnapshot: snapshot,
        showcaseRelaySnapshotSeq: seq,
        showcaseRelaySnapshotAt: new Date(),
      },
      update: {
        showcaseRelaySnapshot: snapshot,
        showcaseRelaySnapshotSeq: seq,
        showcaseRelaySnapshotAt: new Date(),
      },
    });
    this.logger.debug(`Showcase snapshot cached seq=${seq}`);
    return { ok: true, snapshot_seq: Number(seq) };
  }

  async getCachedSnapshot(): Promise<{
    snapshot: Record<string, unknown> | null;
    snapshot_seq: number;
    at: Date | null;
  }> {
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    const raw = row?.showcaseRelaySnapshot;
    const snapshot =
      raw && typeof raw === 'object' && !Array.isArray(raw)
        ? (raw as Record<string, unknown>)
        : null;
    return {
      snapshot,
      snapshot_seq: Number(row?.showcaseRelaySnapshotSeq ?? 0),
      at: row?.showcaseRelaySnapshotAt ?? null,
    };
  }
}
