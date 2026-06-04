import {
  CanActivate,
  ExecutionContext,
  ForbiddenException,
  Injectable,
} from '@nestjs/common';

@Injectable()
export class MetricsSyncGuard implements CanActivate {
  canActivate(context: ExecutionContext): boolean {
    const secret = process.env.METRICS_SYNC_SECRET?.trim();
    if (!secret) {
      if (process.env.NODE_ENV === 'production') {
        throw new ForbiddenException('Metrics sync is disabled — set METRICS_SYNC_SECRET');
      }
      return true;
    }

    const request = context.switchToHttp().getRequest<{ headers: Record<string, unknown> }>();
    const provided = request.headers['x-metrics-sync-secret'];
    const value = typeof provided === 'string' ? provided : Array.isArray(provided) ? provided[0] : '';
    if (value !== secret) {
      throw new ForbiddenException('Invalid metrics sync secret');
    }
    return true;
  }
}
