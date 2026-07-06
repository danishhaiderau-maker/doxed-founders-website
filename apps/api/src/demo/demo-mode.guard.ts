import { CanActivate, ExecutionContext, ForbiddenException, Injectable } from '@nestjs/common';
import { isDemoModeEnabled } from './demo.constants';

@Injectable()
export class DemoModeGuard implements CanActivate {
  canActivate(_context: ExecutionContext): boolean {
    if (!isDemoModeEnabled()) {
      throw new ForbiddenException(
        'Demo mode is disabled. Set DEMO_MODE_ENABLED=true on the API service to enable seed/reset.',
      );
    }
    return true;
  }
}
