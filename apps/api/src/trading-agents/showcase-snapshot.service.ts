import {
  BadRequestException,
  Inject,
  Injectable,
  Logger,
  OnModuleDestroy,
  Optional,
  UnauthorizedException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Prisma } from '@prisma/client';
import { createHmac, timingSafeEqual } from 'node:crypto';
import { PrismaService } from '../prisma/prisma.service';
import { PLATFORM_SETTINGS_ID } from '../prisma/platform-settings-cache';
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

/** Postgres copy is for restart recovery only; Fly pushes every ~2 s. */
const MIN_PERSIST_INTERVAL_MS = 60_000;
const PEER_TIMEOUT_MS = 1_500;
const PEER_COALESCE_MS = 500;
export const SHOWCASE_SNAPSHOT_CLOCK = 'SHOWCASE_SNAPSHOT_CLOCK';

@Injectable()
export class ShowcaseSnapshotService implements OnModuleDestroy {
  private readonly logger = new Logger(ShowcaseSnapshotService.name);
  private latest: CachedShowcaseSnapshot | null = null;
  private lastIngestAt = 0;
  private lastPersistAt = 0;
  private persistedSeq = 0;
  private persistInFlight: Promise<void> | null = null;
  private bootLoad: Promise<void> | null = null;
  private persistedMemo: CachedShowcaseSnapshot | null = null;
  private peerMemo: CachedShowcaseSnapshot | null = null;
  private peerFetchedAt = 0;
  private peerFailures = 0;
  private peerInFlight: Promise<CachedShowcaseSnapshot | null> | null = null;
  private readonly persistIntervalMs: number;
  private readonly peerUrl: string;
  private readonly now: () => number;

  constructor(
    private readonly config: ConfigService,
    private readonly prisma: PrismaService,
    @Optional() @Inject(SHOWCASE_SNAPSHOT_CLOCK) clock?: () => number,
  ) {
    this.now = clock ?? (() => Date.now());
    const configured = Number(this.config.get<string>('SHOWCASE_SNAPSHOT_PERSIST_MS'));
    this.persistIntervalMs = Number.isFinite(configured)
      ? Math.max(MIN_PERSIST_INTERVAL_MS, configured)
      : MIN_PERSIST_INTERVAL_MS;
    this.peerUrl = (this.config.get<string>('SHOWCASE_SNAPSHOT_PEER_URL') ?? '')
      .trim()
      .replace(/\/$/, '');
  }

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
    // lock is enforced and the publisher's snapshot declares a
    // `dashboard_url`, that URL must be the canonical Fly URL. Snapshots
    // built from /api/relay-state currently omit this field, so absent
    // is acceptable (the X-Desktop-Mirror header guard plus existing
    // owner checks remain authoritative); present-and-non-Fly is
    // rejected outright since a desktop process reports a loopback/LAN
    // URL in its own /api/state and would otherwise pass the existing
    // `dashboard_owner=true + port=7002` gate by sharing the relay
    // BOT_CONTROL_SECRET.
    if (
      FLY_CANONICAL_LOCK_ENFORCED
      && typeof identity.dashboard_url === 'string'
      && identity.dashboard_url.trim() !== ''
      && !isFlyDeclaredDashboardUrl(identity.dashboard_url)
    ) {
      throw new BadRequestException(
        'snapshot dashboard_url is not canonical Fly; desktop publishers cannot be canonical',
      );
    }
    await this.ensureBootLoaded();
    const prev = BigInt(this.latest?.snapshot_seq ?? 0);
    if (seq <= prev) {
      return { ok: true, skipped: true, snapshot_seq: Number(prev) };
    }
    const at = new Date(this.now());
    this.latest = { snapshot: identity, snapshot_seq: rawSeq, at };
    this.lastIngestAt = this.now();
    await this.persistIfDue();
    this.logger.debug(`Showcase snapshot cached seq=${seq}`);
    return { ok: true, snapshot_seq: Number(seq) };
  }

  /**
   * Latest pushed snapshot. `at` is when this platform received it, so the
   * bridge's freshness gates fail closed on anything restored from Postgres.
   *
   * - The process that receives Fly pushes (public API) serves memory.
   * - The relay-executor worker pulls from the API over private networking.
   * - Otherwise, read seq/at only and fetch the blob when the seq changed.
   */
  async getCachedSnapshot(): Promise<CachedShowcaseSnapshot> {
    if (this.lastIngestAt > 0 && this.latest) {
      return cloneRecord(this.latest);
    }
    if (this.peerUrl) {
      const fromPeer = await this.fetchFromPeer();
      if (fromPeer) return cloneRecord(fromPeer);
    }
    return cloneRecord(await this.readPersistedSeqGated());
  }

  /** Served to the relay worker over private networking. */
  async getLatestForPeer(sinceSeq: number | null): Promise<
    | (Omit<CachedShowcaseSnapshot, 'at'> & { at: string | null; unchanged?: false })
    | { unchanged: true; snapshot_seq: number; at: string | null }
  > {
    const record = this.lastIngestAt > 0 && this.latest
      ? this.latest
      : await this.readPersistedSeqGated();
    const at = record.at?.toISOString() ?? null;
    if (sinceSeq != null && record.snapshot && sinceSeq === record.snapshot_seq) {
      return { unchanged: true, snapshot_seq: record.snapshot_seq, at };
    }
    return { snapshot: record.snapshot, snapshot_seq: record.snapshot_seq, at };
  }

  async onModuleDestroy() {
    await this.persistLatest('shutdown');
  }

  private ensureBootLoaded(): Promise<void> {
    if (!this.bootLoad) {
      this.bootLoad = (async () => {
        const row = await this.prisma.platformSettings.findUnique({
          where: { id: PLATFORM_SETTINGS_ID },
          select: {
            showcaseRelaySnapshot: true,
            showcaseRelaySnapshotSeq: true,
            showcaseRelaySnapshotAt: true,
          },
        });
        const restored = recordFromRow(row);
        if (!this.latest || restored.snapshot_seq > this.latest.snapshot_seq) {
          this.latest = restored;
        }
        this.persistedSeq = restored.snapshot_seq;
      })().catch((err: unknown) => {
        this.bootLoad = null;
        throw err;
      });
    }
    return this.bootLoad;
  }

  private async persistIfDue() {
    if (this.now() - this.lastPersistAt < this.persistIntervalMs) return;
    await this.persistLatest('interval');
  }

  /** Durability for restart recovery only; never on the read path. */
  private async persistLatest(reason: 'interval' | 'shutdown') {
    const record = this.latest;
    if (!record?.snapshot || !record.at || record.snapshot_seq <= this.persistedSeq) return;
    if (this.persistInFlight) return;
    this.lastPersistAt = this.now();
    const snapshot = record.snapshot as Prisma.InputJsonValue;
    const seq = BigInt(record.snapshot_seq);
    this.persistInFlight = this.prisma.platformSettings
      .upsert({
        where: { id: PLATFORM_SETTINGS_ID },
        // The result is unused; avoid returning the snapshot and unrelated settings.
        select: { id: true },
        create: {
          id: PLATFORM_SETTINGS_ID,
          showcaseRelaySnapshot: snapshot,
          showcaseRelaySnapshotSeq: seq,
          showcaseRelaySnapshotAt: record.at,
        },
        update: {
          showcaseRelaySnapshot: snapshot,
          showcaseRelaySnapshotSeq: seq,
          showcaseRelaySnapshotAt: record.at,
        },
      })
      .then(() => {
        this.persistedSeq = Math.max(this.persistedSeq, record.snapshot_seq);
      })
      .catch((err: unknown) => {
        // Memory stays authoritative; retry on the next push.
        this.lastPersistAt = 0;
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(`Showcase snapshot persist (${reason}) failed: ${msg}`);
      })
      .finally(() => {
        this.persistInFlight = null;
      });
    await this.persistInFlight;
  }

  private async readPersistedSeqGated(): Promise<CachedShowcaseSnapshot> {
    const head = await this.prisma.platformSettings.findUnique({
      where: { id: PLATFORM_SETTINGS_ID },
      select: { showcaseRelaySnapshotSeq: true, showcaseRelaySnapshotAt: true },
    });
    const seq = Number(head?.showcaseRelaySnapshotSeq ?? 0);
    const memo = this.persistedMemo;
    if (memo && memo.snapshot && memo.snapshot_seq === seq) {
      memo.at = head?.showcaseRelaySnapshotAt ?? memo.at;
      return memo;
    }
    const row = await this.prisma.platformSettings.findUnique({
      where: { id: PLATFORM_SETTINGS_ID },
      select: {
        showcaseRelaySnapshot: true,
        showcaseRelaySnapshotSeq: true,
        showcaseRelaySnapshotAt: true,
      },
    });
    this.persistedMemo = recordFromRow(row);
    return this.persistedMemo;
  }

  private fetchFromPeer(): Promise<CachedShowcaseSnapshot | null> {
    const memo = this.peerMemo;
    if (memo && this.now() - this.peerFetchedAt < PEER_COALESCE_MS) {
      return Promise.resolve(memo);
    }
    if (!this.peerInFlight) {
      this.peerInFlight = this.fetchFromPeerOnce().finally(() => {
        this.peerInFlight = null;
      });
    }
    return this.peerInFlight;
  }

  private async fetchFromPeerOnce(): Promise<CachedShowcaseSnapshot | null> {
    const secret = this.config.get<string>('BOT_CONTROL_SECRET')?.trim();
    if (!secret || !this.peerUrl) return null;
    const memo = this.peerMemo;
    const since = memo?.snapshot ? `?since_seq=${memo.snapshot_seq}` : '';
    try {
      const res = await fetch(`${this.peerUrl}/api/internal/showcase-snapshot/latest${since}`, {
        signal: AbortSignal.timeout(PEER_TIMEOUT_MS),
        headers: { Accept: 'application/json', 'X-Bot-Control-Secret': secret },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = (await res.json()) as {
        unchanged?: boolean;
        snapshot?: unknown;
        snapshot_seq?: unknown;
        at?: unknown;
      };
      const at = typeof body.at === 'string' ? new Date(body.at) : null;
      const seq = typeof body.snapshot_seq === 'number' ? body.snapshot_seq : 0;
      let next: CachedShowcaseSnapshot;
      if (body.unchanged === true && memo?.snapshot && memo.snapshot_seq === seq) {
        next = { ...memo, at: at && Number.isFinite(at.getTime()) ? at : memo.at };
      } else {
        next = {
          snapshot: asSnapshotObject(body.snapshot),
          snapshot_seq: seq,
          at: at && Number.isFinite(at.getTime()) ? at : null,
        };
      }
      this.peerMemo = next;
      this.peerFetchedAt = this.now();
      if (this.peerFailures > 0) {
        this.logger.log(`Showcase snapshot peer recovered after ${this.peerFailures} failure(s)`);
        this.peerFailures = 0;
      }
      return next;
    } catch (err) {
      this.peerFailures += 1;
      if (this.peerFailures === 1 || this.peerFailures % 30 === 0) {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(
          `Showcase snapshot peer fetch failed (${this.peerFailures}): ${msg}; using persisted copy`,
        );
      }
      return null;
    }
  }
}

export type CachedShowcaseSnapshot = {
  snapshot: Record<string, unknown> | null;
  snapshot_seq: number;
  at: Date | null;
};

function asSnapshotObject(raw: unknown): Record<string, unknown> | null {
  return raw && typeof raw === 'object' && !Array.isArray(raw)
    ? (raw as Record<string, unknown>)
    : null;
}

function recordFromRow(
  row: {
    showcaseRelaySnapshot?: unknown;
    showcaseRelaySnapshotSeq?: bigint | null;
    showcaseRelaySnapshotAt?: Date | null;
  } | null,
): CachedShowcaseSnapshot {
  return {
    snapshot: asSnapshotObject(row?.showcaseRelaySnapshot),
    snapshot_seq: Number(row?.showcaseRelaySnapshotSeq ?? 0),
    at: row?.showcaseRelaySnapshotAt ?? null,
  };
}

/** Callers mutate the returned state; never hand out the shared copy. */
function cloneRecord(record: CachedShowcaseSnapshot): CachedShowcaseSnapshot {
  return {
    snapshot: record.snapshot ? structuredClone(record.snapshot) : null,
    snapshot_seq: record.snapshot_seq,
    at: record.at ? new Date(record.at.getTime()) : null,
  };
}
