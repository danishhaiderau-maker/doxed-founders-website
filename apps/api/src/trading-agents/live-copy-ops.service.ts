import { BadRequestException, Injectable, NotFoundException, UnauthorizedException } from '@nestjs/common';
import { timingSafeEqual } from 'node:crypto';
import { bitfinexAuthPost } from '../exchanges/bitfinex-api.client';
import { ExchangesService } from '../exchanges/exchanges.service';
import { PrismaService } from '../prisma/prisma.service';
import { liveCopyAccountArmed } from './live-copy-approval';
import { assertBotReadToken } from './ops-read-auth';

export { assertBotReadToken };
import { LiveCopyFlyReporterService, liveCopyAccountLabel, summarizeCopyStatus } from './live-copy-fly-reporter';
import { readPersistedRelayExecutorHealth } from './signal-subscriber-execution.service';

/** BOT_ADMIN_TOKEN (X-Bot-Admin-Token or Bearer), constant-time. */
export function assertBotAdminToken(adminHeader?: string, authorization?: string): void {
  const expected = (process.env.BOT_ADMIN_TOKEN ?? '').trim();
  if (!expected) throw new UnauthorizedException('BOT_ADMIN_TOKEN is not configured');
  const bearer = typeof authorization === 'string' && authorization.toLowerCase().startsWith('bearer ')
    ? authorization.slice(7).trim()
    : '';
  const a = Buffer.from((adminHeader?.trim() || bearer).trim(), 'utf8');
  const b = Buffer.from(expected, 'utf8');
  if (a.length === 0 || a.length !== b.length || !timingSafeEqual(a, b)) {
    throw new UnauthorizedException('Invalid BOT_ADMIN_TOKEN');
  }
}

/** Parse Bitfinex `auth/r/permissions` rows: [[scope, read, write], ...]. */
export function parseBitfinexKeyPermissions(rows: unknown): Record<string, { read: boolean; write: boolean }> | null {
  if (!Array.isArray(rows)) return null;
  const out: Record<string, { read: boolean; write: boolean }> = {};
  for (const r of rows) {
    if (!Array.isArray(r) || typeof r[0] !== 'string') continue;
    out[r[0]] = { read: Number(r[1]) === 1, write: Number(r[2]) === 1 };
  }
  return Object.keys(out).length ? out : null;
}

export const LIVE_COPY_MIN_DERIVATIVES_USD = 0.25;

/**
 * Option 1 operator routes (read-only; never place, cancel, arm or switch).
 * Responses never include key material, fingerprints or user identifiers
 * beyond the opaque account label.
 */
@Injectable()
export class LiveCopyOpsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly exchanges: ExchangesService,
    private readonly reporter: LiveCopyFlyReporterService,
  ) {}

  async accountCheck(
    slug: string,
    userId: string | undefined,
    adminHeader?: string,
    authorization?: string,
    handle?: string,
  ) {
    assertBotReadToken(adminHeader, authorization);
    if (!userId?.trim() && handle?.trim()) {
      const h = handle.trim().replace(/^@/, '');
      const user = await this.prisma.user.findFirst({
        where: {
          OR: [
            { platformHandle: { equals: h, mode: 'insensitive' } },
            { twitterHandle: { equals: h, mode: 'insensitive' } },
          ],
        },
        select: { id: true },
      });
      if (!user) return { schema: 'website_live_copy_account_check_v1', found: false, ready_for_live_copy: false, blockers: ['HANDLE_NOT_FOUND'] };
      userId = user.id;
    }
    if (!userId?.trim()) throw new BadRequestException('userId or handle query param is required');
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug }, select: { id: true } });
    if (!agent) throw new NotFoundException('Agent not found');
    const instance = await this.prisma.tradingAgentInstance.findUnique({
      where: { agentId_userId: { agentId: agent.id, userId: userId.trim() } },
      select: { id: true, status: true, exchangeProvider: true, dashboardState: true, expiresAt: true, lastError: true },
    });
    const checks: Record<string, unknown> = {};
    const blockers: string[] = [];
    if (!instance) {
      return { schema: 'website_live_copy_account_check_v1', found: false, ready_for_live_copy: false, blockers: ['NOT_HIRED'] };
    }
    const dash = (instance.dashboardState ?? {}) as Record<string, unknown>;
    const hireActive = instance.status === 'ACTIVE' && (!instance.expiresAt || instance.expiresAt.getTime() > Date.now());
    checks.entitlement = { status: instance.status, hire_active: hireActive, expires_at: instance.expiresAt?.toISOString() ?? null };
    if (!hireActive) blockers.push('ENTITLEMENT_INACTIVE');
    const armed = liveCopyAccountArmed(instance);
    // A user Start clears the alert and acks it; only an open, unacked alert blocks.
    const mismatchActive =
      typeof dash.positionMismatchDetectedAt === 'string' &&
      dash.positionMismatchAlert != null &&
      dash.positionMismatchAlertAcked !== true;
    const health = readPersistedRelayExecutorHealth(dash);
    checks.arm = {
      armed,
      relay_armed_at: typeof dash.relayArmedAt === 'string' ? dash.relayArmedAt : null,
      exchange_provider: instance.exchangeProvider,
      executor_healthy: health.healthy,
      position_mismatch: mismatchActive,
      position_mismatch_last_detected_at: typeof dash.positionMismatchDetectedAt === 'string' ? dash.positionMismatchDetectedAt : null,
      last_error: instance.lastError ? String(instance.lastError).slice(0, 200) : null,
    };
    if (!armed) blockers.push('ACCOUNT_NOT_ARMED');
    if (mismatchActive) blockers.push('POSITION_MISMATCH');

    const status = await this.exchanges.getUserExchangeStatus(userId.trim(), 'bitfinex');
    const resolution = await this.exchanges.resolveUserCredentials(userId.trim(), 'bitfinex');
    checks.keys = { connected: Boolean(status.connected), verified_at: (status as { verifiedAt?: string | null }).verifiedAt ?? null, credential_state: resolution.ok ? 'OK' : resolution.code };
    if (!resolution.ok || !resolution.credentials) {
      blockers.push('KEYS_MISSING_OR_UNREADABLE');
    } else {
      const creds = resolution.credentials;
      let perms: ReturnType<typeof parseBitfinexKeyPermissions> = null;
      let permsError: string | null = null;
      try {
        perms = parseBitfinexKeyPermissions(await bitfinexAuthPost<unknown>(creds, 'v2/auth/r/permissions'));
      } catch (err) {
        permsError = err instanceof Error ? err.message.slice(0, 120) : 'PERMISSIONS_READ_FAILED';
      }
      const wallet = await this.exchanges.getUserBitfinexWalletSnapshot(userId.trim());
      const keysValid = perms != null || wallet != null;
      checks.keys = {
        ...(checks.keys as object),
        valid: keysValid,
        permissions_read_error: permsError,
        can_trade: perms ? Boolean(perms.orders?.write) : null,
        can_read_wallets: perms ? Boolean(perms.wallets?.read) : null,
        withdraw_enabled: perms ? Boolean(perms.withdraw?.write) : null,
      };
      if (!keysValid) blockers.push('KEYS_INVALID');
      if (!perms) blockers.push('KEY_PERMISSIONS_UNVERIFIED');
      else {
        if (perms.withdraw?.write) blockers.push('KEY_HAS_WITHDRAW_PERMISSION');
        if (!perms.orders?.write) blockers.push('KEY_CANNOT_TRADE');
      }
      checks.balance = wallet
        ? {
            derivatives_available_usd: wallet.derivativesUsd,
            derivatives_total_usd: wallet.derivativesTotalUsd,
            exchange_usd: wallet.exchangeUsd,
            funding_usd: wallet.fundingUsd,
            min_required_usd: LIVE_COPY_MIN_DERIVATIVES_USD,
          }
        : null;
      if (!wallet) blockers.push('BALANCE_UNAVAILABLE');
      else if (wallet.derivativesUsd < LIVE_COPY_MIN_DERIVATIVES_USD) blockers.push('DERIVATIVES_BALANCE_BELOW_MIN');
    }
    return {
      schema: 'website_live_copy_account_check_v1',
      found: true,
      account: liveCopyAccountLabel(instance.id),
      ready_for_live_copy: blockers.length === 0,
      blockers,
      checks,
      checked_at: new Date().toISOString(),
    };
  }

  async copyStatus(slug: string, adminHeader?: string, authorization?: string, rejects?: unknown) {
    assertBotReadToken(adminHeader, authorization);
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug }, select: { id: true } });
    if (!agent) throw new NotFoundException('Agent not found');
    const nowMs = Date.now();
    const reports = await this.reporter.collect(nowMs);
    return {
      ...summarizeCopyStatus(reports, nowMs, this.reporter.acked),
      reporter: { ...this.reporter.stats },
      ingest_rejects_1h: rejects ?? null,
    };
  }
}
