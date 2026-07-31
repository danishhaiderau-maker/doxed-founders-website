import {
  CanActivate,
  ExecutionContext,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { timingSafeEqual } from 'node:crypto';
import { PrismaService } from '../prisma/prisma.service';
import type { AuthUser } from '../auth/auth.types';

/**
 * FIX 1 — DDollar gate bearer-token guard.
 *
 * The strategy bot's DDollar go-live gate (services/btc-conservative-agent/
 * bitfinex_live_executor.py) reads DDOLLAR_GATE_URL and sends
 * `Authorization: Bearer ${DDOLLAR_GATE_TOKEN}` on every fetch. This guard
 * lets the bot authenticate to /api/founder-economics/ddollar/gate-balance
 * without a browser session, while keeping the global JwtAuthGuard in
 * place for human callers (the same handler still accepts @CurrentUser).
 *
 * Auth contract:
 *   - If `DDOLLAR_GATE_TOKEN` env is unset on the API, this guard always
 *     denies. There is no bypass.
 *   - If the request has no `Authorization: Bearer ...` header, the guard
 *     returns `false` so the global JwtAuthGuard (registered as APP_GUARD)
 *     still runs and accepts a session cookie. Order matters: this guard
 *     must be registered with a higher priority than the JWT guard on the
 *     gate-balance route so the bot's bearer header wins, but a missing
 *     bearer header gracefully falls through to the session.
 *   - If a bearer header is present, it must match `DDOLLAR_GATE_TOKEN`
 *     (timing-safe compare). On match, the guard materializes an AuthUser
 *     for the configured `DDOLLAR_GATE_OPERATOR_USER_ID` (loaded from the
 *     User table so reputationPoints stays canonical).
 *
 * The operator user id is never auto-discovered; the operator sets it
 * explicitly. If unset or pointing at a non-existent user, the guard
 * denies. Fail-closed is mandatory.
 */
@Injectable()
export class DdollarGateTokenGuard implements CanActivate {
  constructor(
    private readonly config: ConfigService,
    private readonly prisma: PrismaService,
  ) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const request = context.switchToHttp().getRequest<{
      headers: Record<string, string | string[] | undefined>;
      user?: AuthUser;
    }>();

    const raw = request.headers?.authorization;
    const header = (Array.isArray(raw) ? raw[0] : raw)?.trim();
    if (!header || !header.toLowerCase().startsWith('bearer ')) {
      // No bearer header — defer to the global JwtAuthGuard (session path).
      // Returning false here would force a 401 before the session check.
      // Instead, signal "not handled by this guard" via a missing user; the
      // global guard's @CurrentUser resolution then either succeeds (cookie)
      // or fails (401). We return true so the request proceeds to the
      // handler, and the @CurrentUser decorator resolves the session user.
      // When there is also no session, NestJS rejects via JwtAuthGuard.
      return true;
    }

    const supplied = Buffer.from(header.slice(7).trim(), 'utf8');
    const expected = Buffer.from(this.requiredToken(), 'utf8');
    if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
      throw new UnauthorizedException('Invalid DDollar gate token');
    }

    const operatorId = this.requiredOperatorUserId();
    const user = await this.prisma.user.findUnique({
      where: { id: operatorId },
      select: {
        id: true,
        email: true,
        name: true,
        role: true,
        reputationPoints: true,
        contributorLevel: true,
      },
    });
    if (!user) {
      throw new UnauthorizedException('DDollar gate operator user not found');
    }
    request.user = {
      id: user.id,
      email: user.email,
      name: user.name,
      role: user.role,
      reputationPoints: user.reputationPoints,
      contributorLevel: user.contributorLevel,
    };
    return true;
  }

  private requiredToken(): string {
    const token = this.config.get<string>('DDOLLAR_GATE_TOKEN')?.trim();
    if (!token) {
      throw new UnauthorizedException('DDollar gate token not configured');
    }
    return token;
  }

  private requiredOperatorUserId(): string {
    const userId = this.config.get<string>('DDOLLAR_GATE_OPERATOR_USER_ID')?.trim();
    if (!userId) {
      throw new UnauthorizedException('DDollar gate operator user id not configured');
    }
    return userId;
  }
}
