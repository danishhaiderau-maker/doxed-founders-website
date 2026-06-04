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
import { FounderDenModule } from './founder-den/founder-den.module';
import { FounderUpdatesModule } from './founder-updates/founder-updates.module';
import { NotificationsModule } from './notifications/notifications.module';
import { PointsModule } from './points/points.module';
import { PrismaModule } from './prisma/prisma.module';
import { ConvictionShareModule } from './conviction-share/conviction-share.module';
import { EngagementRewardsModule } from './engagement-rewards/engagement-rewards.module';
import { FounderOsModule } from './founder-os/founder-os.module';
import { SecurityModule } from './security/security.module';
import { AgentsModule } from './agents/agents.module';
import { BuildQueueModule } from './build-queue/build-queue.module';
import { BuilderModule } from './builder/builder.module';
import { EventsModule } from './events/events.module';
import { XSocialModule } from './x-social/x-social.module';
import { AccountModule } from './account/account.module';
import { PredictionMarketsModule } from './prediction-markets/prediction-markets.module';
import { FounderNodeModule } from './founder-node/founder-node.module';
import { AttestationModule } from './attestation/attestation.module';
import { TrustCenterModule } from './trust-center/trust-center.module';
import { TownHallModule } from './town-hall/town-hall.module';
import { TradingAgentsModule } from './trading-agents/trading-agents.module';
import { AdminControlModule } from './admin-control/admin-control.module';
import { MessagesModule } from './messages/messages.module';
import { FounderMemoryGraphModule } from './founder-memory/founder-memory-graph.module';
import { CredentialsModule } from './credentials/credentials.module';
import { PrivacyModule } from './privacy/privacy.module';
import { VaultModule } from './vault/vault.module';

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      envFilePath: ['.env.local', '.env'],
    }),
    ThrottlerModule.forRoot([{ ttl: 60000, limit: 100 }]),
    PrismaModule,
    CredentialsModule,
    PrivacyModule,
    FounderMemoryGraphModule,
    PointsModule,
    NotificationsModule,
    FounderUpdatesModule,
    FounderDenModule,
    ConvictionShareModule,
    EngagementRewardsModule,
    FounderOsModule,
    SecurityModule,
    AgentsModule,
    BuildQueueModule,
    BuilderModule,
    EventsModule,
    XSocialModule,
    AccountModule,
    PredictionMarketsModule,
    FounderNodeModule,
    AttestationModule,
    VaultModule,
    TrustCenterModule,
    TownHallModule,
    TradingAgentsModule,
    AdminControlModule,
    MessagesModule,
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
