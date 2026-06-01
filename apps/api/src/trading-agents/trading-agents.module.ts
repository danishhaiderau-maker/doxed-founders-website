import { Module } from '@nestjs/common';
import { NotificationsModule } from '../notifications/notifications.module';
import { BotBridgeService } from './bot-bridge.service';
import { TradingAgentsController } from './trading-agents.controller';
import { TradingAgentsService } from './trading-agents.service';

@Module({
  imports: [NotificationsModule],
  controllers: [TradingAgentsController],
  providers: [TradingAgentsService, BotBridgeService],
  exports: [TradingAgentsService, BotBridgeService],
})
export class TradingAgentsModule {}
