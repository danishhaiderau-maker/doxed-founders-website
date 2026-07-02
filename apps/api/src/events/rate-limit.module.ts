import { Global, Module } from '@nestjs/common';
import { RateLimiterService } from './rate-limiter.service';

/**
 * Global module so every controller/service that calls an AI endpoint can
 * enforce the per-user DB-backed rate limiter without re-importing the whole
 * EventsModule (which would create circular deps with Share/Wall/etc.).
 */
@Global()
@Module({
  providers: [RateLimiterService],
  exports: [RateLimiterService],
})
export class RateLimitModule {}
