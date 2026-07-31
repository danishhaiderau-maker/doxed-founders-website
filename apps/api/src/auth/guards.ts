import {
  CanActivate,
  ExecutionContext,
  ForbiddenException,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { AuthGuard } from '@nestjs/passport';
import { UserRole } from '@prisma/client';
import { ConfigService } from '@nestjs/config';
import { timingSafeEqual } from 'node:crypto';
import { AuthUser } from './auth.types';
import { IS_PUBLIC_KEY } from './public.decorator';

@Injectable()
export class JwtAuthGuard extends AuthGuard('jwt') {
  constructor(
    private readonly reflector: Reflector,
    private readonly config: ConfigService,
  ) {
    super();
  }

  canActivate(context: ExecutionContext) {
    const isPublic = this.reflector.getAllAndOverride<boolean>(IS_PUBLIC_KEY, [
      context.getHandler(),
      context.getClass(),
    ]);
    if (isPublic) {
      return true;
    }

    const request = context.switchToHttp().getRequest<{ headers: Record<string, string | string[] | undefined> }>();
    const raw = request.headers?.authorization;
    const header = (Array.isArray(raw) ? raw[0] : raw)?.trim();

    // Desktop Founder Node uses `Authorization: FounderNode {nodeId}:{token}`.
    // Route-level FounderNodeGuard validates that; skip JWT so we do not 401 before it runs.
    if (header?.startsWith('FounderNode ')) {
      return true;
    }

    // FIX 1 — DDollar gate shim. The strategy bot's go-live gate fetches
    // /api/founder-economics/ddollar/gate-balance with
    // `Authorization: Bearer ${DDOLLAR_GATE_TOKEN}`. That token is not a
    // JWT, so the passport jwt strategy would 401 before the route-level
    // DdollarGateTokenGuard can materialize the operator user. When the
    // configured token matches (timing-safe), skip JWT and let the
    // route-level guard run. Fail-closed: an unset DDOLLAR_GATE_TOKEN
    // never matches, so this path is inert until the operator configures it.
    if (header?.toLowerCase().startsWith('bearer ')) {
      const expected = this.config.get<string>('DDOLLAR_GATE_TOKEN')?.trim();
      if (expected) {
        const supplied = header.slice(7).trim();
        const suppliedBuf = Buffer.from(supplied, 'utf8');
        const expectedBuf = Buffer.from(expected, 'utf8');
        if (
          suppliedBuf.length === expectedBuf.length
          && suppliedBuf.length > 0
          && timingSafeEqual(suppliedBuf, expectedBuf)
        ) {
          return true;
        }
      }
    }

    return super.canActivate(context);
  }
}

@Injectable()
export class AdminGuard implements CanActivate {
  constructor(private readonly reflector: Reflector) {}

  canActivate(context: ExecutionContext): boolean {
    // [DEMO_HARNESS_FIX_2026-07-08] Honor @Public() on a handler so the
    // orchestrator's token-gated /admin/demo/harness/internal route can be
    // reached without an admin JWT. The token check in the controller still
    // gates it. Mirrors JwtAuthGuard's behavior.
    const isPublic = this.reflector.getAllAndOverride<boolean>(IS_PUBLIC_KEY, [
      context.getHandler(),
      context.getClass(),
    ]);
    if (isPublic) return true;

    const request = context.switchToHttp().getRequest<{ user?: AuthUser }>();
    if (request.user?.role !== UserRole.ADMIN) {
      throw new ForbiddenException('Admin access required');
    }
    return true;
  }
}
