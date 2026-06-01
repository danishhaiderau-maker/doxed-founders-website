import { Module } from '@nestjs/common';
import { NotificationsModule } from '../notifications/notifications.module';
import { PointsModule } from '../points/points.module';
import { ExchangesModule } from '../exchanges/exchanges.module';
import { BotBridgeService } from './bot-bridge.service';
import { TradingAgentInstancesService } from './trading-agent-instances.service';
import { TradingAgentsController } from './trading-agents.controller';
import { TradingAgentsService } from './trading-agents.service';

@Module({
  imports: [NotificationsModule, PointsModule, ExchangesModule],
  controllers: [TradingAgentsController],
  providers: [TradingAgentsService, TradingAgentInstancesService, BotBridgeService],
  exports: [TradingAgentsService, BotBridgeService, TradingAgentInstancesService],
})
export class TradingAgentsModule {}
