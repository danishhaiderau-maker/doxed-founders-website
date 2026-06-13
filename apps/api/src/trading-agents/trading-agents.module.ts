import { Module } from '@nestjs/common';
import { NotificationsModule } from '../notifications/notifications.module';
import { PointsModule } from '../points/points.module';
import { ExchangesModule } from '../exchanges/exchanges.module';
import { BotBridgeService } from './bot-bridge.service';
import { SignalApiKeyGuard } from './signal-api-key.guard';
import { SignalCyclesController } from './signal-cycles.controller';
import { SignalCyclesService } from './signal-cycles.service';
import { TradingAgentInstancesService } from './trading-agent-instances.service';
import { TradingAgentsController } from './trading-agents.controller';
import { TradingAgentsService } from './trading-agents.service';

@Module({
  imports: [NotificationsModule, PointsModule, ExchangesModule],
  controllers: [TradingAgentsController, SignalCyclesController],
  providers: [
    TradingAgentsService,
    TradingAgentInstancesService,
    BotBridgeService,
    SignalCyclesService,
    SignalApiKeyGuard,
  ],
  exports: [TradingAgentsService, BotBridgeService, TradingAgentInstancesService, SignalCyclesService],
})
export class TradingAgentsModule {}
