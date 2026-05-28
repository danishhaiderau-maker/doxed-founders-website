import { ExecutionContext, Injectable, UnauthorizedException } from '@nestjs/common';
import { AuthGuard } from '@nestjs/passport';
import { AuthUser } from './auth.types';

/** Attaches user when Bearer token is valid; anonymous OK without token; invalid token → 401. */
@Injectable()
export class OptionalJwtAuthGuard extends AuthGuard('jwt') {
  canActivate(context: ExecutionContext) {
    const request = context.switchToHttp().getRequest<{ headers: { authorization?: string } }>();
    const auth = request.headers.authorization;
    if (!auth?.startsWith('Bearer ')) {
      return true;
    }
    return super.canActivate(context) as Promise<boolean>;
  }

  handleRequest<TUser = AuthUser>(err: unknown, user: TUser): TUser {
    if (err || !user) {
      throw new UnauthorizedException(
        'Session expired or invalid. Sign in again to link scout points to your account.',
      );
    }
    return user;
  }
}
