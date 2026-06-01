import { Module } from '@nestjs/common';
import { AdminControlController } from './admin-control.controller';
import { AdminControlService } from './admin-control.service';
import { TradingAgentsModule } from '../trading-agents/trading-agents.module';

@Module({
  imports: [TradingAgentsModule],
  controllers: [AdminControlController],
  providers: [AdminControlService],
  exports: [AdminControlService],
})
export class AdminControlModule {}
