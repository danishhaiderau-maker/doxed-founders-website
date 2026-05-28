import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { APP_GUARD } from '@nestjs/core';
import { ThrottlerModule, ThrottlerGuard } from '@nestjs/throttler';
import { AnalyticsModule } from './analytics/analytics.module';
import { AuthModule } from './auth/auth.module';
import { JwtAuthGuard } from './auth/guards';
import { FeedModule } from './feed/feed.module';
import { HealthModule } from './health/health.module';
import { ListingApplicationsModule } from './listing-applications/listing-applications.module';
import { PaperTradingModule } from './paper-trading/paper-trading.module';
import { ProjectsModule } from './projects/projects.module';
import { ReputationModule } from './reputation/reputation.module';
import { WatchlistModule } from './watchlist/watchlist.module';
import { FounderUpdatesModule } from './founder-updates/founder-updates.module';
import { NotificationsModule } from './notifications/notifications.module';
import { PointsModule } from './points/points.module';
import { PrismaModule } from './prisma/prisma.module';
import { XSocialModule } from './x-social/x-social.module';

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      envFilePath: ['.env.local', '.env'],
    }),
    ThrottlerModule.forRoot([{ ttl: 60000, limit: 100 }]),
    PrismaModule,
    PointsModule,
    NotificationsModule,
    FounderUpdatesModule,
    XSocialModule,
    AnalyticsModule,
    AuthModule,
    HealthModule,
    ListingApplicationsModule,
    PaperTradingModule,
    FeedModule,
    ProjectsModule,
    WatchlistModule,
    ReputationModule,
  ],
  providers: [
    {
      provide: APP_GUARD,
      useClass: ThrottlerGuard,
    },
    {
      provide: APP_GUARD,
      useClass: JwtAuthGuard,
    },
  ],
})
export class AppModule {}
