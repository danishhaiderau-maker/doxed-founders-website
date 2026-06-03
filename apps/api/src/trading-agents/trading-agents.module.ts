import { Module, forwardRef } from '@nestjs/common';
import { BuilderModule } from '../builder/builder.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { PointsModule } from '../points/points.module';
import { ExchangesModule } from '../exchanges/exchanges.module';
import { BotBridgeService } from './bot-bridge.service';
import { TradingAgentInstancesService } from './trading-agent-instances.service';
import { TradingAgentsController } from './trading-agents.controller';
import { TradingAgentsService } from './trading-agents.service';

@Module({
  imports: [NotificationsModule, PointsModule, ExchangesModule, forwardRef(() => BuilderModule)],
  controllers: [TradingAgentsController],
  providers: [TradingAgentsService, TradingAgentInstancesService, BotBridgeService],
  exports: [TradingAgentsService, BotBridgeService, TradingAgentInstancesService],
})
export class TradingAgentsModule {}
