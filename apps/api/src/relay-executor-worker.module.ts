import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { PrismaModule } from './prisma/prisma.module';
import { PointsModule } from './points/points.module';
import { BuilderScoreModule } from './founder-os/builder-score.service';
import { DdollarModule } from './ddollar/ddollar.module';
import { NotificationsModule } from './notifications/notifications.module';
import { ExchangesModule } from './exchanges/exchanges.module';
import { ShowcaseSnapshotService } from './trading-agents/showcase-snapshot.service';
import { BotBridgeService } from './trading-agents/bot-bridge.service';
import { SignalCyclesService } from './trading-agents/signal-cycles.service';
import { CopyRelaySimService } from './trading-agents/copy-relay-sim.service';
import { TradeCycleAuditService } from './trading-agents/trade-cycle-audit.service';
import { SignalSubscriberExecutionService } from './trading-agents/signal-subscriber-execution.service';

/** Isolated real-money worker: no public controllers or unrelated schedulers. */
@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      envFilePath: ['.env.local', '.env'],
    }),
    PrismaModule,
    BuilderScoreModule,
    DdollarModule,
    PointsModule,
    NotificationsModule,
    ExchangesModule,
  ],
  providers: [
    ShowcaseSnapshotService,
    BotBridgeService,
    SignalCyclesService,
    CopyRelaySimService,
    TradeCycleAuditService,
    SignalSubscriberExecutionService,
  ],
})
export class RelayExecutorWorkerModule {}
