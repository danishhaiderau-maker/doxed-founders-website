import {
  BadRequestException,
  Injectable,
  Logger,
  UnauthorizedException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Prisma } from '@prisma/client';
import { createHmac, timingSafeEqual } from 'node:crypto';
import { PrismaService } from '../prisma/prisma.service';
import {
  FLY_CANONICAL_LOCK_ENFORCED,
  isFlyDeclaredDashboardUrl,
} from './fly-canonical-lock';

export type ShowcaseSnapshotBody = {
  snapshot_seq?: number;
  snapshot?: Record<string, unknown>;
  snapshot_json?: string;
  snapshot_hmac?: string;
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

  private controlSecret(): string {
    const expected = this.config.get<string>('BOT_CONTROL_SECRET')?.trim();
    if (!expected) {
      throw new UnauthorizedException('Showcase snapshot push not configured');
    }
    return expected;
  }

  assertAuthorized(secretHeader: string | undefined) {
    const expected = Buffer.from(this.controlSecret(), 'utf8');
    const supplied = Buffer.from(secretHeader?.trim() ?? '', 'utf8');
    if (expected.length !== supplied.length || !timingSafeEqual(expected, supplied)) {
      throw new UnauthorizedException('Invalid bot control secret');
    }
  }

  async ingest(body: ShowcaseSnapshotBody) {
    const rawSeq = body.snapshot_seq;
    if (typeof rawSeq !== 'number' || !Number.isSafeInteger(rawSeq) || rawSeq <= 0) {
      throw new BadRequestException('snapshot_seq must be a positive safe integer');
    }
    const seq = BigInt(rawSeq);
    const snapshotJson =
      typeof body.snapshot_json === 'string' ? body.snapshot_json : '';
    const suppliedHmac =
      typeof body.snapshot_hmac === 'string'
        ? body.snapshot_hmac.trim().toLowerCase()
        : '';
    if (!snapshotJson || !/^[a-f0-9]{64}$/.test(suppliedHmac)) {
      throw new UnauthorizedException('Signed showcase snapshot required');
    }
    const expectedHmac = createHmac('sha256', this.controlSecret())
      .update(`${rawSeq}.${snapshotJson}`, 'utf8')
      .digest('hex');
    const expectedBytes = Buffer.from(expectedHmac, 'hex');
    const suppliedBytes = Buffer.from(suppliedHmac, 'hex');
    if (
      expectedBytes.length !== suppliedBytes.length
      || !timingSafeEqual(expectedBytes, suppliedBytes)
    ) {
      throw new UnauthorizedException('Invalid showcase snapshot signature');
    }

    let rawSnapshot: unknown;
    try {
      rawSnapshot = JSON.parse(snapshotJson) as unknown;
    } catch {
      throw new BadRequestException('snapshot_json must contain valid JSON');
    }
    if (!rawSnapshot || typeof rawSnapshot !== 'object' || Array.isArray(rawSnapshot)) {
      throw new BadRequestException('snapshot must be an object');
    }
    const identity = rawSnapshot as Record<string, unknown>;
    const instanceId =
      typeof identity.bot_instance_id === 'string' ? identity.bot_instance_id.trim() : '';
    const sourceRevision =
      typeof identity.source_git_rev === 'string' ? identity.source_git_rev.trim() : '';
    const sourceTimestamp =
      typeof identity.server_ts === 'string'
        ? Date.parse(identity.server_ts)
        : Number.NaN;
    const sourceAgeMs = Date.now() - sourceTimestamp;
    if (
      identity.dashboard_owner !== true
      || identity.dashboard_port !== 7002
      || !instanceId
      || !sourceRevision
      || !Number.isFinite(sourceTimestamp)
      || sourceAgeMs < -10_000
      || sourceAgeMs > 120_000
    ) {
      throw new BadRequestException('snapshot did not prove a fresh canonical :7002 owner');
    }
    // FIX 2 — pushed-snapshot Fly-origin proof. When the source-controlled
    // lock is enforced, the publisher's snapshot must declare its public
    // dashboard URL as the canonical Fly URL. The lock files the desktop
    // launchers and prevents a desktop process from claiming ownership,
    // but this is the API-side belt-and-suspenders guard: even if a
    // desktop publisher somehow held BOT_CONTROL_SECRET, its snapshot
    // would carry a loopback/LAN dashboard_url and be rejected here.
    if (
      FLY_CANONICAL_LOCK_ENFORCED
      && !isFlyDeclaredDashboardUrl(identity.dashboard_url)
    ) {
      throw new BadRequestException(
        'snapshot dashboard_url is not canonical Fly; desktop publishers cannot be canonical',
      );
    }
    const snapshot = rawSnapshot as Prisma.InputJsonValue;
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    const prev = row?.showcaseRelaySnapshotSeq ?? BigInt(0);
    if (seq <= prev) {
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
