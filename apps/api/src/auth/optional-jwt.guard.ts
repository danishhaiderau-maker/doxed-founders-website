import { ExecutionContext, Injectable } from '@nestjs/common';
import { AuthGuard } from '@nestjs/passport';
import { AuthUser } from './auth.types';

/** Attaches user when Bearer token is valid; never blocks anonymous requests. */
@Injectable()
export class OptionalJwtAuthGuard extends AuthGuard('jwt') {
  canActivate(context: ExecutionContext) {
    const request = context.switchToHttp().getRequest<{ headers: { authorization?: string } }>();
    const auth = request.headers.authorization;
    if (!auth?.startsWith('Bearer ')) {
      return true;
    }
    return (super.canActivate(context) as Promise<boolean>).catch(() => true);
  }

  handleRequest<TUser = AuthUser>(err: unknown, user: TUser): TUser | null {
    if (err || !user) return null;
    return user;
  }
}
