import { Module } from '@nestjs/common';
import { TradingAgentsController } from './trading-agents.controller';
import { TradingAgentsService } from './trading-agents.service';

@Module({
  controllers: [TradingAgentsController],
  providers: [TradingAgentsService],
  exports: [TradingAgentsService],
})
export class TradingAgentsModule {}
